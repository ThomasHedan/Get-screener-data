"""Append-only, date-partitioned storage for the daily archive.

Everything is written as plain CSV/JSONL with the standard library. That is a
deliberate choice: the collector is a cron job that must not fail because a
binary wheel stopped matching the interpreter, and CSV keeps the archive
readable by pandas, polars, DuckDB, R or a text editor a decade from now.

Layout under ``data/``::

    daily_bars/2026-08-31.csv        full-market daily bars (RVOL history + archive)
    scans/2026-08-31/candidates.csv  every evaluated candidate, with reject reasons
    scans/2026-08-31/in_play.csv     the 5-10 selected names
    intraday/2026-08-31/ABCD.csv     1-minute bars, 04:00-20:00 ET
    features/2026-08-31.csv          intraday features for the in-play names
    reference/tickers.jsonl          append-only reference snapshots
    reference/cache.json             reference lookup cache (TTL-bounded)
    in_play_history.csv              every in-play row ever selected, one file
    runs.jsonl                       one record per collector run

Writes are atomic (temp file + ``os.replace``) so an interrupted run never
leaves a half-written partition behind.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
import time
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from warrior_screener.models import Candidate, DailyBar, MinuteBar, TickerReference

logger = logging.getLogger(__name__)

DAILY_BAR_FIELDS = ("ticker", "open", "high", "low", "close", "volume", "vwap", "trade_count")
MINUTE_BAR_FIELDS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "trade_count",
)


class Archive:
    """Reads and writes the on-disk daily archive."""

    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir)

    # ------------------------------------------------------------- Locations

    def daily_bars_path(self, trade_date: date) -> Path:
        return self.root / "daily_bars" / f"{trade_date.isoformat()}.csv"

    def scan_dir(self, trade_date: date) -> Path:
        return self.root / "scans" / trade_date.isoformat()

    def intraday_dir(self, trade_date: date) -> Path:
        return self.root / "intraday" / trade_date.isoformat()

    def features_path(self, trade_date: date) -> Path:
        return self.root / "features" / f"{trade_date.isoformat()}.csv"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    @property
    def in_play_history_path(self) -> Path:
        return self.root / "in_play_history.csv"

    # ------------------------------------------------------------ Daily bars

    def has_daily_bars(self, trade_date: date) -> bool:
        return self.daily_bars_path(trade_date).exists()

    def daily_bars_age_days(self, trade_date: date) -> float | None:
        """Days since the cached session was fetched, or ``None`` if uncached.

        Prices in the archive are split-adjusted *as of the moment they were
        fetched*, so age is what decides whether a cached bar can still be
        compared against a freshly fetched one.
        """
        path = self.daily_bars_path(trade_date)
        if not path.exists():
            return None
        return (time.time() - path.stat().st_mtime) / 86_400.0

    def write_daily_bars(self, trade_date: date, bars: Sequence[DailyBar]) -> Path:
        """Persist a full-market session. An empty session writes a header-only
        file, which is how the walker remembers that the market was closed."""
        path = self.daily_bars_path(trade_date)
        rows = [
            {
                "ticker": bar.ticker,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "vwap": bar.vwap,
                "trade_count": bar.trade_count,
            }
            for bar in bars
        ]
        _write_csv(path, DAILY_BAR_FIELDS, rows)
        logger.debug("Wrote %d daily bars to %s", len(rows), path)
        return path

    def read_daily_bars(self, trade_date: date) -> list[DailyBar]:
        """Read a cached session, or return an empty list if it is not cached."""
        path = self.daily_bars_path(trade_date)
        if not path.exists():
            return []
        bars: list[DailyBar] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                bars.append(
                    DailyBar(
                        ticker=row["ticker"],
                        trade_date=trade_date,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(row["volume"]),
                        vwap=_float_or_none(row.get("vwap")),
                        trade_count=_int_or_none(row.get("trade_count")),
                    )
                )
        return bars

    # ----------------------------------------------------------------- Scans

    def write_scan(
        self,
        trade_date: date,
        candidates: Sequence[Candidate],
        in_play: Sequence[Candidate],
    ) -> tuple[Path, Path]:
        """Write the full candidate table and the selected in-play names."""
        scan_dir = self.scan_dir(trade_date)
        fields = list(Candidate(ticker="", trade_date=trade_date).to_row())

        candidates_path = scan_dir / "candidates.csv"
        _write_csv(candidates_path, fields, [c.to_row() for c in candidates])

        in_play_path = scan_dir / "in_play.csv"
        _write_csv(in_play_path, fields, [c.to_row() for c in in_play])

        self._append_in_play_history(trade_date, in_play, fields)
        return candidates_path, in_play_path

    def read_in_play(self, trade_date: date) -> list[dict[str, str]]:
        """Return the in-play rows recorded for a session, if any."""
        path = self.scan_dir(trade_date) / "in_play.csv"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def read_candidates(self, trade_date: date) -> list[dict[str, str]]:
        """Return the full candidate table recorded for a session, if any."""
        path = self.scan_dir(trade_date) / "candidates.csv"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def _append_in_play_history(
        self, trade_date: date, in_play: Sequence[Candidate], fields: list[str]
    ) -> None:
        """Maintain the single-file history, replacing any rows for this date.

        Re-running a day is idempotent: the previous rows for ``trade_date`` are
        dropped before the new ones are appended.
        """
        path = self.in_play_history_path
        existing: list[dict[str, Any]] = []
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as handle:
                existing = [
                    row
                    for row in csv.DictReader(handle)
                    if row.get("trade_date") != trade_date.isoformat()
                ]
        rows = existing + [c.to_row() for c in in_play]
        rows.sort(key=lambda row: (str(row.get("trade_date")), -float(row.get("score") or 0)))
        _write_csv(path, fields, rows)

    # -------------------------------------------------------------- Intraday

    def write_minute_bars(self, trade_date: date, ticker: str, bars: Sequence[MinuteBar]) -> Path:
        """Persist one ticker's intraday session."""
        path = self.intraday_dir(trade_date) / f"{_safe_filename(ticker)}.csv"
        rows = [
            {
                "timestamp": bar.timestamp.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "vwap": bar.vwap,
                "trade_count": bar.trade_count,
            }
            for bar in bars
        ]
        _write_csv(path, MINUTE_BAR_FIELDS, rows)
        return path

    def has_minute_bars(self, trade_date: date, ticker: str) -> bool:
        return (self.intraday_dir(trade_date) / f"{_safe_filename(ticker)}.csv").exists()

    def write_features(self, trade_date: date, rows: Sequence[dict[str, Any]]) -> Path:
        """Write the derived intraday feature table for a session."""
        path = self.features_path(trade_date)
        fields = list(rows[0]) if rows else ["ticker", "trade_date"]
        _write_csv(path, fields, rows)
        return path

    # ------------------------------------------------------------- Reference

    def record_reference(self, reference: TickerReference) -> None:
        """Append a reference snapshot.

        This file is never rewritten or pruned. When a ticker is delisted the
        provider eventually stops serving its details, and this record is the
        only remaining description of what it was.
        """
        path = self.reference_dir / "tickers.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": reference.ticker,
            "name": reference.name,
            "security_type": reference.security_type,
            "primary_exchange": reference.primary_exchange,
            "shares_outstanding": reference.shares_outstanding,
            "market_cap": reference.market_cap,
            "is_active": reference.is_active,
            "list_date": reference.list_date.isoformat() if reference.list_date else None,
            "as_of": reference.as_of.isoformat() if reference.as_of else None,
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")

    def load_reference_cache(self) -> dict[str, dict[str, Any]]:
        """Load the TTL-bounded reference lookup cache."""
        path = self.reference_dir / "cache.json"
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except json.JSONDecodeError:
            logger.warning("Reference cache at %s is corrupt; rebuilding it", path)
            return {}

    def save_reference_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        """Persist the reference lookup cache atomically."""
        path = self.reference_dir / "cache.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(cache, indent=0, sort_keys=True))

    # ------------------------------------------------------------- Run audit

    def record_run(self, payload: dict[str, Any]) -> None:
        """Append one line to the run log, for monitoring gaps in the archive."""
        path = self.root / "runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"recorded_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        entry.update(payload)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    def collected_dates(self) -> list[date]:
        """Return the sessions that already have a scan on disk, oldest first."""
        scans_root = self.root / "scans"
        if not scans_root.exists():
            return []
        dates: list[date] = []
        for child in scans_root.iterdir():
            if not child.is_dir():
                continue
            try:
                dates.append(date.fromisoformat(child.name))
            except ValueError:
                logger.debug("Ignoring non-date directory in scans/: %s", child.name)
        return sorted(dates)


# ---------------------------------------------------------------- CSV helpers


def _write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    """Write ``rows`` to ``path`` atomically, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    field_list = list(fields)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=field_list, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        temp_name = handle.name
    os.replace(temp_name, path)


def _atomic_write(path: Path, text: str) -> None:
    """Write text to ``path`` via a temp file in the same directory."""
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temp_name = handle.name
    os.replace(temp_name, path)


def _safe_filename(ticker: str) -> str:
    """Make a ticker safe as a filename (class shares carry dots and slashes)."""
    return "".join(char if char.isalnum() or char in "-._" else "_" for char in ticker)


def _float_or_none(value: Any) -> float | None:
    return float(value) if value not in (None, "") else None


def _int_or_none(value: Any) -> int | None:
    return int(float(value)) if value not in (None, "") else None
