"""Prior-session context: previous closes and average volume for RVOL.

Relative volume is the single most important Warrior Trading criterion, and it
needs a rolling window of prior sessions. Those sessions are fetched once with
the grouped-daily endpoint and cached on disk, so a warmed-up archive costs one
API call per new trading day -- and the cache doubles as the survivorship-free
full-market daily bar history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from warrior_screener.providers.base import MarketDataProvider
from warrior_screener.storage import Archive

logger = logging.getLogger(__name__)

MIN_SESSIONS_FOR_RVOL = 3
"""Below this, a ticker is too newly listed for a meaningful average volume."""


@dataclass
class SessionHistory:
    """Previous-close and volume history for the sessions before a scan date."""

    trade_date: date
    previous_session: date | None = None
    prev_close: dict[str, float] = field(default_factory=dict)
    volume_history: dict[str, list[int]] = field(default_factory=dict)
    sessions_loaded: int = 0

    def average_volume(self, ticker: str) -> float | None:
        """Mean daily volume over the loaded window, or ``None`` if too new."""
        volumes = self.volume_history.get(ticker)
        if not volumes or len(volumes) < MIN_SESSIONS_FOR_RVOL:
            return None
        return mean(volumes)

    def relative_volume(self, ticker: str, volume: int) -> float | None:
        """Today's volume as a multiple of the average, or ``None`` if unknown."""
        average = self.average_volume(ticker)
        if not average:
            return None
        return volume / average


def load_history(
    provider: MarketDataProvider,
    archive: Archive,
    trade_date: date,
    lookback_days: int,
    *,
    allow_fetch: bool = True,
    refresh_previous_after_days: float = 1.0,
) -> SessionHistory:
    """Load the ``lookback_days`` sessions preceding ``trade_date``.

    Walks backwards over calendar days, skipping weekends, reading each session
    from the archive and fetching it only when missing. Sessions with no bars
    (market holidays) are cached as empty so the walk never re-requests them.

    The immediately preceding session is re-fetched when its cached copy is
    older than ``refresh_previous_after_days``. Provider prices are split-adjusted
    as of the moment they are fetched, so a previous close cached before a split
    would be compared against a post-split close today -- and these low-float
    small caps reverse-split constantly. Without the refresh a routine 1:10
    reverse split reads as a +900% gapper and lands in the dataset as one. It
    costs at most one extra request per scan.
    """
    history = SessionHistory(trade_date=trade_date)
    # Weekends and up to ~10 holidays inside the window mean the calendar-day
    # budget has to run well ahead of the session count.
    max_calendar_days = lookback_days * 2 + 15
    cursor = trade_date - timedelta(days=1)
    checked = 0

    while history.sessions_loaded < lookback_days and checked < max_calendar_days:
        checked += 1
        if cursor.weekday() >= 5:  # Saturday / Sunday
            cursor -= timedelta(days=1)
            continue

        is_previous_session = history.previous_session is None
        age_days = archive.daily_bars_age_days(cursor)
        stale_baseline = (
            is_previous_session and age_days is not None and age_days > refresh_previous_after_days
        )

        bars = archive.read_daily_bars(cursor)
        if stale_baseline or (not bars and age_days is None):
            if not allow_fetch:
                cursor -= timedelta(days=1)
                continue
            if stale_baseline:
                logger.info(
                    "Re-fetching %s (cached %.1f days ago) to keep split adjustment "
                    "consistent with %s",
                    cursor,
                    age_days,
                    trade_date,
                )
            bars = list(provider.grouped_daily(cursor))
            archive.write_daily_bars(cursor, bars)

        if not bars:  # market holiday
            logger.debug("No bars for %s, treating as a non-trading day", cursor)
            cursor -= timedelta(days=1)
            continue

        if history.previous_session is None:
            history.previous_session = cursor
            history.prev_close = {bar.ticker: bar.close for bar in bars}

        for bar in bars:
            history.volume_history.setdefault(bar.ticker, []).append(bar.volume)

        history.sessions_loaded += 1
        cursor -= timedelta(days=1)

    if history.sessions_loaded < lookback_days:
        logger.warning(
            "Only %d of %d requested prior sessions available before %s",
            history.sessions_loaded,
            lookback_days,
            trade_date,
        )
    return history
