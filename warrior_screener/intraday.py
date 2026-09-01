"""Intraday collection and feature extraction for the in-play names.

The 1-minute bars are the actual research payload: the screen only decides
*which* symbols to keep, and this module records how each of them behaved
minute by minute, plus a flat feature row per ticker per day so a strategy can
be prototyped without re-parsing the raw bars.

All session boundaries are US/Eastern, and extended hours are included --
pre-market is where a Warrior-style gapper sets up.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, time
from statistics import mean
from typing import Any

from warrior_screener.models import Candidate, MinuteBar

logger = logging.getLogger(__name__)

PREMARKET_START = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTERHOURS_END = time(20, 0)

REGULAR_SESSION_MINUTES = 390
BIG_MINUTE_VOLUME = 100_000
"""A 1-minute bar above this is the kind of volume spike momentum traders chase."""


def compute_features(
    candidate: Candidate, bars: Sequence[MinuteBar], trade_date: date
) -> dict[str, Any]:
    """Reduce one ticker's minute bars to a single feature row.

    Returns the row even when bars are missing (all price fields ``None``), so
    every in-play selection stays represented in the archive and gaps are
    explicit rather than silent.
    """
    row: dict[str, Any] = {
        "trade_date": trade_date.isoformat(),
        "ticker": candidate.ticker,
        "qualification": candidate.qualification,
        "score": candidate.score,
        "prev_close": candidate.prev_close,
        "gap_pct": candidate.gap_pct,
        "change_pct": candidate.change_pct,
        "relative_volume": candidate.relative_volume,
        "float_shares": candidate.float_shares,
        "news_count": candidate.news_count,
        "minute_bars": len(bars),
    }

    premarket = [bar for bar in bars if bar.timestamp.time() < REGULAR_OPEN]
    regular = [bar for bar in bars if REGULAR_OPEN <= bar.timestamp.time() < REGULAR_CLOSE]
    afterhours = [bar for bar in bars if bar.timestamp.time() >= REGULAR_CLOSE]

    row.update(_premarket_features(premarket, candidate.prev_close))
    row.update(_regular_session_features(regular))
    row.update(
        {
            "afterhours_volume": sum(bar.volume for bar in afterhours) or None,
            "afterhours_close": afterhours[-1].close if afterhours else None,
            "day_volume": sum(bar.volume for bar in bars) or None,
        }
    )
    return row


def _premarket_features(bars: Sequence[MinuteBar], prev_close: float | None) -> dict[str, Any]:
    """Pre-market range, volume and the time the pre-market high printed."""
    if not bars:
        return {
            "premarket_high": None,
            "premarket_low": None,
            "premarket_volume": None,
            "premarket_high_time": None,
            "premarket_change_pct": None,
        }
    high_bar = max(bars, key=lambda bar: bar.high)
    high = high_bar.high
    return {
        "premarket_high": round(high, 4),
        "premarket_low": round(min(bar.low for bar in bars), 4),
        "premarket_volume": sum(bar.volume for bar in bars),
        "premarket_high_time": high_bar.timestamp.strftime("%H:%M"),
        "premarket_change_pct": _pct_change(prev_close, bars[-1].close),
    }


def _regular_session_features(bars: Sequence[MinuteBar]) -> dict[str, Any]:
    """Open, high/low with timing, VWAP, volume distribution and halt proxy."""
    empty: dict[str, Any] = {
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "high_time": None,
        "low_time": None,
        "minutes_to_high": None,
        "rth_volume": None,
        "rth_vwap": None,
        "open_to_high_pct": None,
        "open_to_close_pct": None,
        "close_vs_high_pct": None,
        "first_5min_range_pct": None,
        "first_30min_volume_share": None,
        "first_hour_volume_share": None,
        "max_minute_volume": None,
        "big_volume_minutes": None,
        "avg_minute_range_pct": None,
        "minutes_traded": None,
        "untraded_minutes": None,
    }
    if not bars:
        return empty

    open_price = bars[0].open or bars[0].close
    high_bar = max(bars, key=lambda bar: bar.high)
    low_bar = min(bars, key=lambda bar: bar.low)
    close_price = bars[-1].close
    rth_volume = sum(bar.volume for bar in bars)

    open_minute = bars[0].timestamp.replace(hour=9, minute=30, second=0, microsecond=0)
    minutes_to_high = int((high_bar.timestamp - open_minute).total_seconds() // 60)

    first_5 = [b for b in bars if (b.timestamp - open_minute).total_seconds() < 5 * 60]
    first_30 = [b for b in bars if (b.timestamp - open_minute).total_seconds() < 30 * 60]
    first_60 = [b for b in bars if (b.timestamp - open_minute).total_seconds() < 60 * 60]

    notional = sum((bar.vwap or bar.close) * bar.volume for bar in bars)

    return {
        "open": round(open_price, 4),
        "high": round(high_bar.high, 4),
        "low": round(low_bar.low, 4),
        "close": round(close_price, 4),
        "high_time": high_bar.timestamp.strftime("%H:%M"),
        "low_time": low_bar.timestamp.strftime("%H:%M"),
        "minutes_to_high": minutes_to_high,
        "rth_volume": rth_volume,
        "rth_vwap": round(notional / rth_volume, 4) if rth_volume else None,
        "open_to_high_pct": _pct_change(open_price, high_bar.high),
        "open_to_close_pct": _pct_change(open_price, close_price),
        # Negative: how far off the high it closed, i.e. did the move hold.
        "close_vs_high_pct": _pct_change(high_bar.high, close_price),
        "first_5min_range_pct": (
            _pct_change(min(b.low for b in first_5), max(b.high for b in first_5))
            if first_5
            else None
        ),
        "first_30min_volume_share": _share(sum(b.volume for b in first_30), rth_volume),
        "first_hour_volume_share": _share(sum(b.volume for b in first_60), rth_volume),
        "max_minute_volume": max(bar.volume for bar in bars),
        "big_volume_minutes": sum(1 for bar in bars if bar.volume >= BIG_MINUTE_VOLUME),
        "avg_minute_range_pct": round(
            mean([(bar.high - bar.low) / bar.low * 100.0 for bar in bars if bar.low]), 4
        )
        if any(bar.low for bar in bars)
        else None,
        "minutes_traded": len(bars),
        # A minute with no trades at all during regular hours is usually a
        # volatility halt on these names -- worth flagging for the strategy.
        "untraded_minutes": max(REGULAR_SESSION_MINUTES - len(bars), 0),
    }


def _pct_change(start: float | None, end: float | None) -> float | None:
    """Percentage change from ``start`` to ``end``, or ``None`` if undefined."""
    if not start or end is None:
        return None
    return round((end - start) / start * 100.0, 4)


def _share(part: int, whole: int) -> float | None:
    """``part`` as a fraction of ``whole``, or ``None`` when whole is zero."""
    if not whole:
        return None
    return round(part / whole, 4)
