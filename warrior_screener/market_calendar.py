"""Minimal US equity market calendar -- just enough to flag stale data.

This is deliberately not a full market-calendar library: no early closes, no
years beyond the current one, no historical lookups. It exists for exactly
one job -- so ``warrior_screener snapshot`` can tell the user "you are not
looking at live trading" instead of silently presenting the last session's
numbers as if the market is open right now. That mistake is easy to make: a
holiday Monday looks, from a script's point of view, identical to a normal
trading day until you check a calendar.

The archived pipeline (``warrior_screener.collector``) never needed this --
Polygon's own ``grouped_daily`` returns no bars at all on a closed day, which
``collect_day`` already reports as ``market_closed``. TradingView's snapshot
has no such signal: it always returns the most recent session's data, closed
market or not.

Source: https://www.nyse.com/markets/hours-calendars (verified 2026-09-07).
**Update ``US_MARKET_HOLIDAYS`` every January** -- there is no free,
unauthenticated bulk calendar API, and a short hardcoded list is standard
practice for exactly this reason in lightweight tools.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

PREMARKET_START = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTERHOURS_END = time(20, 0)

# Full-day NYSE/Nasdaq closures only. Early-close days (day after
# Thanksgiving, Christmas Eve) are deliberately excluded -- the market trades
# part of the session, so "stale, nothing happened today" would be the wrong
# message on those dates.
US_MARKET_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Washington's Birthday
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth National Independence Day
        date(2026, 7, 3),  # Independence Day (observed)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving Day
        date(2026, 12, 25),  # Christmas Day
    }
)


def is_trading_day(day: date) -> bool:
    """True if US equity markets hold a full regular session on ``day``.

    Only accurate for dates covered by ``US_MARKET_HOLIDAYS`` above (2026) --
    a date far outside that range returns True for any weekday, which is a
    reasonable default (most weekdays are trading days) but will miss that
    year's actual holidays. Update the list before relying on this past 2026.
    """
    return day.weekday() < 5 and day not in US_MARKET_HOLIDAYS


def session_phase(moment: datetime | None = None) -> str:
    """Classify ``moment`` (default: now) into a US equity session phase.

    Returns one of ``"closed"``, ``"pre-market"``, ``"regular"`` or
    ``"after-hours"``. This exists for the same reason as ``is_trading_day``,
    to catch a case that a plain weekend/holiday check does not: caught live
    on 2026-09-08 at 04:52 ET, right after a holiday Monday -- a normal
    trading day, so no holiday warning fired, but the previous session's
    numbers were still identical, because pre-market for illiquid names had
    barely started. "Is this a trading day" and "has trading actually
    happened yet today" are different questions; this answers the second one.
    """
    now = (moment or datetime.now(EASTERN)).astimezone(EASTERN)
    if not is_trading_day(now.date()):
        return "closed"
    current_time = now.time()
    if current_time < PREMARKET_START or current_time >= AFTERHOURS_END:
        return "closed"
    if current_time < REGULAR_OPEN:
        return "pre-market"
    if current_time < REGULAR_CLOSE:
        return "regular"
    return "after-hours"
