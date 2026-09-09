"""Per-slot CSV archive of the day's screen candidates.

The dashboard only ever needs today's board. This module exists for the other
consumer: a research repo that wants, for every ticker the screen flagged on a
given day, the same measurements taken at four fixed points -- five minutes
before the open, five and ten minutes after it, and just before the close.

Two decisions in here shape how usable that dataset is.

**Carried tickers.** Each slot archives the candidates it finds *plus* every
ticker that was a candidate at an earlier slot the same day, even when it no
longer qualifies. Without that, a name that ran 60% at 09:35 and faded to +4%
by the close is simply absent from the closing file -- and a morning feature
with no closing figure cannot be turned into a label. Carried rows are marked,
so a study can always recover "was a live candidate at this instant" from
``qualification`` rather than from presence in the file.

**The intended slot is recorded next to the real timestamp.** GitHub's
scheduler runs late under load, sometimes by ten minutes or more. A row
therefore carries both ``slot`` (which measurement this was meant to be) and
``captured_at`` (when it actually happened). Analysis that cares about
precision can filter on the second; everything else can group by the first.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

from warrior_screener.models import Candidate

logger = logging.getLogger(__name__)

FIELDNAMES = (
    "slot",
    "captured_at",
    "trade_date",
    "ticker",
    "exchange",
    "security_type",
    "sector",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "change_pct",
    "gap_pct",
    "range_pct",
    "volume",
    "avg_volume",
    "relative_volume",
    "dollar_volume",
    "float_shares",
    "market_cap",
    "score",
    "qualification",
    "rejected_by",
    "in_play",
)
"""The archive's column contract. Append new columns at the end -- never
reorder or rename, since files already written cannot be migrated."""

CARRIED = "carried"
"""``qualification`` for a row present only because the ticker qualified
earlier today. It is not a verdict on the row: it means this ticker failed the
price/change/volume gate at this instant, and is here for continuity."""


def write_slot(
    path: Path,
    candidates: list[Candidate],
    *,
    slot: str,
    captured_at: datetime,
    in_play: set[str],
    sectors: dict[str, str | None],
) -> Path:
    """Write one slot's rows to ``path`` as CSV, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                _row(
                    candidate, slot=slot, captured_at=captured_at, in_play=in_play, sectors=sectors
                )
            )
    logger.info("Archived %d rows to %s (slot=%s)", len(candidates), path, slot)
    return path


def _row(
    candidate: Candidate,
    *,
    slot: str,
    captured_at: datetime,
    in_play: set[str],
    sectors: dict[str, str | None],
) -> dict[str, object]:
    return {
        "slot": slot,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "trade_date": candidate.trade_date.isoformat(),
        "ticker": candidate.ticker,
        "exchange": candidate.primary_exchange or "",
        "security_type": candidate.security_type or "",
        "sector": sectors.get(candidate.ticker) or "",
        "open": candidate.open,
        "high": candidate.high,
        "low": candidate.low,
        "close": candidate.close,
        "prev_close": candidate.prev_close if candidate.prev_close is not None else "",
        "change_pct": _blank_if_none(candidate.change_pct),
        "gap_pct": _blank_if_none(candidate.gap_pct),
        "range_pct": _blank_if_none(candidate.range_pct),
        "volume": candidate.volume,
        "avg_volume": _blank_if_none(candidate.avg_volume),
        "relative_volume": _blank_if_none(candidate.relative_volume),
        "dollar_volume": _blank_if_none(candidate.dollar_volume),
        "float_shares": _blank_if_none(candidate.float_shares),
        "market_cap": _blank_if_none(candidate.market_cap),
        "score": _blank_if_none(candidate.score) if candidate.qualification != CARRIED else "",
        "qualification": candidate.qualification,
        "rejected_by": "|".join(candidate.rejected_by),
        "in_play": "true" if candidate.ticker in in_play else "false",
    }


def _blank_if_none(value: object) -> object:
    """Write an empty cell rather than the string "None" for a missing value."""
    return "" if value is None else value


def tickers_seen(directory: Path) -> set[str]:
    """Every ticker already archived under ``directory`` (one day's slots).

    Used to carry a morning candidate through to the later slots. A missing
    directory is an empty set, not an error: the first slot of the day has
    nothing to carry, and a research repo that never ran yesterday should not
    break today's run.
    """
    if not directory.is_dir():
        return set()

    seen: set[str] = set()
    for csv_path in sorted(directory.glob("*.csv")):
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    ticker = (row.get("ticker") or "").strip()
                    if ticker:
                        seen.add(ticker)
        except OSError as exc:  # pragma: no cover - unreadable file, keep going
            logger.warning("Could not read %s for carry-forward: %s", csv_path, exc)
    logger.info("Carrying %d ticker(s) forward from %s", len(seen), directory)
    return seen
