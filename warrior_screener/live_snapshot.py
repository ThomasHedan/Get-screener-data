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
from warrior_screener.providers.tradingview import MarketSnapshotRow, fetch_market_snapshot
from warrior_screener.scanner import evaluate, score_candidates, select_in_play

if TYPE_CHECKING:
    from warrior_screener.scanner import ScanResult

logger = logging.getLogger(__name__)


def candidates_from_snapshot(
    rows: list[MarketSnapshotRow], criteria: Criteria, *, as_of: date | None = None
) -> list[Candidate]:
    """Apply the screen's price/change/volume filters to a live snapshot.

    Mirrors :func:`warrior_screener.scanner.coarse_candidates`, adapted to a
    snapshot row that already carries relative volume and float instead of
    needing prior sessions and a reference lookup to compute them.
    """
    trade_date = as_of or date.today()
    survivors: list[Candidate] = []

    for row in rows:
        if not criteria.min_price <= row.close <= criteria.max_price:
            continue
        if row.volume < criteria.min_day_volume:
            continue
        if row.change_pct < criteria.min_change_pct:
            continue

        prev_close = row.prev_close
        gap_pct = (row.open - prev_close) / prev_close * 100.0 if prev_close and row.open else None
        if criteria.min_gap_pct is not None and (gap_pct is None or gap_pct < criteria.min_gap_pct):
            continue

        candidate = Candidate(
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
            security_type="CS" if row.security_type == "stock" else "ADRC",
            primary_exchange=row.exchange,
            float_shares=int(row.float_shares) if row.float_shares else None,
            shares_outstanding=int(row.float_shares) if row.float_shares else None,
            market_cap=row.market_cap,
            # No free bulk news source backs this path -- see the module
            # docstring. news_checked stays False, never claim "no catalyst".
            news_count=0,
            news_checked=False,
        )
        survivors.append(candidate)

    return survivors


def screen_live(
    criteria: Criteria,
    *,
    exchange_allowlist: bool = True,
) -> ScanResult:
    """Fetch a live TradingView snapshot and run the Warrior screen against it.

    ``exchange_allowlist`` restricts results to ``criteria.allowed_exchanges``;
    switch it off to see what the raw TradingView universe (which already
    excludes OTC by construction) would select without that filter.
    """
    from warrior_screener.scanner import ScanResult  # local import: avoid a cycle at module load

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
