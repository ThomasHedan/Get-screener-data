"""Minimal US equity market calendar -- just enough to flag stale data.

This is deliberately not a full market-calendar library: no early closes, no
years beyond the current one, no historical lookups. It exists for exactly
one job -- so ``warrior_screener snapshot`` can tell the user "you are not
looking at live trading" instead of silently presenting the last session's
numbers as if the market is open right now. That mistake is easy to make: a
holiday Monday looks, from a script's point of view, identical to a normal
trading day until you check a calendar.

TradingView's snapshot gives no signal of its own here: it always returns the
most recent session's data, closed market or not.

Source: https://www.nyse.com/markets/hours-calendars (verified 2026-09-07).
**Update ``US_MARKET_HOLIDAYS`` every January** -- there is no free,
unauthenticated bulk calendar API, and a short hardcoded list is standard
practice for exactly this reason in lightweight tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
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


def parse_times(raw: str) -> tuple[time, ...]:
    """Parse ``"09:31,09:35"`` into Eastern times-of-day, in order.

    Pinned in ET rather than local time on purpose: Paris and New York change
    clocks on different dates, so a time pinned in local time drifts an hour
    away from the open twice a year.
    """
    return tuple(
        sorted(time.fromisoformat(part.strip()) for part in raw.split(",") if part.strip())
    )


def next_capture(now: datetime, interval_seconds: int, pinned: Sequence[time] = ()) -> datetime:
    """The next moment worth capturing: the next clock-aligned tick, or the
    next pinned time-of-day, whichever comes first.

    Two properties the previous "sleep(interval) after each scan" did not have:

    * **Aligned.** Ticks land on clock boundaries (09:30, 09:35, ...) instead
      of wherever the container happened to start, and they stop drifting by
      however long each scan took.
    * **Pinned.** ``pinned`` times are always hit, whatever the interval. The
      open drive is decided in the first minutes; 09:31 is not reachable from
      a 5-minute grid.

    A pinned time that coincides with an aligned tick yields one moment, not
    two -- ``scan.captured_at`` is UNIQUE, and a double capture would collide.
    """
    now = now.astimezone(EASTERN)
    step = max(1, interval_seconds)
    # Align on the epoch so the grid survives a DST change intact.
    moments = [datetime.fromtimestamp((int(now.timestamp()) // step + 1) * step, EASTERN)]
    for offset in (0, 1):  # today's pinned times, then tomorrow's, for late evenings
        day = now.date() + timedelta(days=offset)
        moments.extend(datetime.combine(day, moment, EASTERN) for moment in pinned)
    return min(moment for moment in moments if moment > now)
