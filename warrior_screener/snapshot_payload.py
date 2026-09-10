"""The dashboard payload: one scan, serialised the way the board reads it.

Kept out of :mod:`warrior_screener.cli` because two callers build it now --
the ``snapshot`` subcommand and the scheduler that drives the container
deployment -- and a second copy of this dict is precisely how a scan and the
board it feeds drift apart.

The TypeScript mirror of this shape is ``ScreenerPayload`` in lib/screener.ts.
A field added here has to be added there too.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from warrior_screener.config import Criteria


def json_row(candidate: Any) -> dict[str, Any]:
    """One in-play name, flattened to the fields the dashboard renders."""
    return {
        "ticker": candidate.ticker,
        "close": candidate.close,
        "change_pct": candidate.change_pct,
        "gap_pct": candidate.gap_pct,
        "relative_volume": candidate.relative_volume,
        "volume": candidate.volume,
        "avg_volume": candidate.avg_volume,
        "float_shares": candidate.float_shares,
        "market_cap": candidate.market_cap,
        "primary_exchange": candidate.primary_exchange,
        "score": candidate.score,
        "qualification": candidate.qualification,
        "rejected_by": list(candidate.rejected_by),
        # False means nothing checked for news, not that there is no catalyst.
        "news_checked": candidate.news_checked,
        "news_count": candidate.news_count,
    }


def staleness_notice(now_et: datetime, phase: str) -> str | None:
    """The warning that belongs on this result, or None when it is live.

    TradingView has no "market closed" or "no trades yet" signal of its own --
    it always answers with the most recent session, which reads exactly like
    live data unless something says otherwise. Caught live twice: once run on a
    market holiday (silently showed Friday's numbers), then again at 04:52 ET
    the following trading day -- a real trading day, so that check passed, but
    pre-market had barely started and lower-volume names still showed the
    *same* stale numbers. "Is today a trading day" and "has trading actually
    happened yet" are different questions; session_phase answers the second.

    This matters more now than it did as a terminal warning: the dashboard is
    refreshed two hours before the open, squarely inside the pre-market case,
    so the notice travels into the JSON and onto the page.
    """
    stamp = now_et.strftime("%H:%M %Z")
    if phase == "closed":
        return (
            f"Market closed ({now_et.strftime('%Y-%m-%d %H:%M %Z')}) -- these figures "
            "are from the last session, not today."
        )
    if phase == "pre-market":
        return (
            f"Pre-market ({stamp}, regular open is 09:30 ET) -- lower-volume names may "
            "still show yesterday's numbers until they actually trade today."
        )
    if phase == "after-hours":
        return (
            f"After-hours ({stamp}, regular close was 16:00 ET) -- figures include "
            "today's regular session plus after-hours activity."
        )
    return None


def build_payload(
    result: Any,
    now_et: datetime,
    phase: str,
    notice: str | None,
    criteria: Criteria,
) -> dict[str, Any]:
    """Serialise a scan for the dashboard, staleness and criteria included.

    Anything the page needs to render an honest screen has to be in here --
    including *when* this ran and whether the market was open at the time. A
    dashboard that cannot tell a live board from yesterday's leftovers is
    worse than no dashboard.
    """
    return {
        "generated_at": now_et.isoformat(timespec="seconds"),
        "trade_date": result.trade_date.isoformat(),
        "session_phase": phase,
        "notice": notice,
        "stats": result.stats,
        "criteria": {
            "min_change_pct": criteria.min_change_pct,
            "min_price": criteria.min_price,
            "max_price": criteria.max_price,
            "min_relative_volume": criteria.min_relative_volume,
            "max_float_shares": criteria.max_float_shares,
            "min_day_volume": criteria.min_day_volume,
            "require_news_catalyst": criteria.require_news_catalyst,
        },
        "in_play": [json_row(candidate) for candidate in result.in_play],
    }
