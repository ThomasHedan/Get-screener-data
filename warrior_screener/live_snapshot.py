"""A Warrior-style screen run live against TradingView's snapshot, right now.

This is the fast path: no API key, one HTTP request, results in a couple of
seconds. It reuses the archived pipeline's own filters, scoring and selection
(:mod:`warrior_screener.scanner`) so a candidate means the same thing here as
it does in the daily archive -- but see the module docstring on
:mod:`warrior_screener.providers.tradingview` for two differences that matter:
this cannot look at a past date, and its relative volume is time-of-day
normalized rather than full-session.

Because there is no free bulk news source, every candidate here has
``news_checked=False``. That is not a bug: it is the same "we do not know, so
do not claim there is no catalyst" signal the archived pipeline uses for a
candidate that never reached the news lookup, and it means every candidate
lands as ``relaxed`` unless ``require_news_catalyst`` is turned off -- which
is exactly the honest state of things, since nothing here checked for news.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from warrior_screener.config import Criteria
from warrior_screener.models import Candidate
from warrior_screener.providers.tradingview import (
    SECURITY_TYPE_TO_POLYGON,
    MarketSnapshotRow,
    fetch_market_snapshot,
)
from warrior_screener.scanner import evaluate, score_candidates, select_in_play

if TYPE_CHECKING:
    from warrior_screener.scanner import ScanResult

logger = logging.getLogger(__name__)


def candidates_from_snapshot(
    rows: list[MarketSnapshotRow], criteria: Criteria, *, as_of: date | None = None
) -> list[Candidate]:
    """Apply the screen's price/change/volume filters to a live snapshot.

    Adapted to a snapshot row that already carries relative volume and float
    instead of needing prior sessions and a reference lookup to compute them.
    """
    trade_date = as_of or date.today()
    survivors: list[Candidate] = []

    for row in rows:
        if not passes_coarse_filters(row, criteria):
            continue
        survivors.append(build_candidate(row, trade_date))

    return survivors


def passes_coarse_filters(row: MarketSnapshotRow, criteria: Criteria) -> bool:
    """The cheap price/volume/change gate every candidate must clear.

    Split out from :func:`candidates_from_snapshot` so the archive can build a
    row for a ticker that fails it -- a name carried over from an earlier slot
    today is exactly a ticker that no longer qualifies, and dropping it would
    leave the morning's runners with no closing figures.
    """
    if not criteria.min_price <= row.close <= criteria.max_price:
        return False
    if row.volume < criteria.min_day_volume:
        return False
    if row.change_pct < criteria.min_change_pct:
        return False
    if criteria.min_gap_pct is not None:
        gap_pct = _gap_pct(row)
        if gap_pct is None or gap_pct < criteria.min_gap_pct:
            return False
    return True


def build_candidate(row: MarketSnapshotRow, trade_date: date) -> Candidate:
    """Turn one snapshot row into a Candidate, filters already decided."""
    prev_close = row.prev_close
    gap_pct = _gap_pct(row)

    return Candidate(
        ticker=row.ticker,
        trade_date=trade_date,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        prev_close=prev_close,
        gap_pct=_round(gap_pct),
        change_pct=_round(row.change_pct),
        range_pct=_round((row.high - row.low) / row.low * 100.0 if row.low else None),
        avg_volume=_round(row.average_volume, 1),
        relative_volume=_round(row.relative_volume),
        dollar_volume=_round(row.close * row.volume, 0),
        security_type=SECURITY_TYPE_TO_POLYGON.get(row.security_type),
        primary_exchange=row.exchange,
        float_shares=int(row.float_shares) if row.float_shares else None,
        shares_outstanding=int(row.float_shares) if row.float_shares else None,
        market_cap=row.market_cap,
        # No free bulk news source backs this path -- see the module
        # docstring. news_checked stays False, never claim "no catalyst".
        news_count=0,
        news_checked=False,
    )


def _gap_pct(row: MarketSnapshotRow) -> float | None:
    """Today's open against the previous close, in percent.

    Only meaningful once the regular session has opened: before 09:30 ET
    TradingView's ``open`` is not yet today's, so this reads as a gap that has
    not happened. That is the reason the pre-open slot exists for research
    rather than for the board.
    """
    prev_close = row.prev_close
    if not prev_close or not row.open:
        return None
    return (row.open - prev_close) / prev_close * 100.0


def screen_live(
    criteria: Criteria,
    *,
    rows: list[MarketSnapshotRow] | None = None,
    exchange_allowlist: bool = True,
) -> ScanResult:
    """Run the Warrior screen against a live TradingView snapshot.

    ``rows`` lets a caller supply a snapshot it has already fetched, so the
    board and the archive can be built from one request rather than two --
    which also guarantees they describe the same instant. Omit it and the
    snapshot is fetched here.

    ``exchange_allowlist`` restricts results to ``criteria.allowed_exchanges``;
    switch it off to see what the raw TradingView universe (which already
    excludes OTC by construction) would select without that filter.
    """
    from warrior_screener.scanner import ScanResult  # local import: avoid a cycle at module load

    if rows is None:
        rows = fetch_market_snapshot()
    candidates = candidates_from_snapshot(rows, criteria)

    effective_criteria = criteria if exchange_allowlist else _without_exchange_filter(criteria)
    for candidate in candidates:
        candidate.rejected_by = evaluate(candidate, effective_criteria)

    score_candidates(candidates, effective_criteria)
    in_play = select_in_play(candidates, effective_criteria)

    stats = {
        "universe_rows": len(rows),
        "coarse_candidates": len(candidates),
        "strict_qualifiers": sum(1 for c in candidates if not c.rejected_by),
        "in_play": len(in_play),
        "relaxed_in_play": sum(1 for c in in_play if c.qualification == "relaxed"),
    }
    logger.info("Live snapshot scan: %s", stats)
    return ScanResult(
        trade_date=date.today(),
        candidates=sorted(candidates, key=lambda c: c.score, reverse=True),
        in_play=in_play,
        stats=stats,
    )


def _without_exchange_filter(criteria: Criteria) -> Criteria:
    from dataclasses import replace

    return replace(criteria, allowed_exchanges=())


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None
