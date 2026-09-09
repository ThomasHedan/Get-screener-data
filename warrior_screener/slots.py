"""Which of the day's four measurements a scheduled run is, and whether it runs.

GitHub cron is UTC-only, so every Eastern slot needs two entries -- one for EDT
and one for EST -- of which exactly one must survive. And GitHub's scheduler
runs late under load, sometimes past ten minutes, which matters when slots sit
five minutes apart.

So the slot is taken from the cron expression that fired
(``github.event.schedule``), never from the clock: a run delayed to 09:47 is
still the 09:40 measurement, and says so. The clock is used for one narrow
job -- rejecting the wrong-timezone twin, which is always a full hour out and
therefore unambiguous even after a long delay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from warrior_screener.market_calendar import is_trading_day

#: Slot name -> the Eastern wall-clock time it is meant to capture.
SLOT_TIMES: dict[str, time] = {
    "pre_open": time(9, 25),
    "t_plus_5": time(9, 35),
    "t_plus_10": time(9, 40),
    "close": time(15, 55),
}

#: Cron expression -> slot. Each slot appears twice, an hour apart in UTC, so
#: that one of the pair lands on the intended Eastern time whichever side of a
#: daylight-saving switch the date falls.
CRON_TO_SLOT: dict[str, str] = {
    "25 13 * * 1-5": "pre_open",
    "25 14 * * 1-5": "pre_open",
    "35 13 * * 1-5": "t_plus_5",
    "35 14 * * 1-5": "t_plus_5",
    "40 13 * * 1-5": "t_plus_10",
    "40 14 * * 1-5": "t_plus_10",
    "55 19 * * 1-5": "close",
    "55 20 * * 1-5": "close",
}

#: Slots that also publish to the dashboard. All of them do: the page shows the
#: day's slots side by side, so the pre-open watchlist and the ten-minutes-in
#: board are each one click away. What the pre-open snapshot must not do is
#: masquerade as live -- before 09:30 TradingView still reports yesterday's
#: figures for anything thinly traded -- so the page labels that view rather
#: than the schedule withholding it.
BOARD_SLOTS = frozenset(SLOT_TIMES) | {"manual"}

#: The view the page opens on when several slots exist for the day. Falls back
#: to whatever is most recent, so before 09:40 you land on the pre-open board.
PREFERRED_SLOT = "t_plus_10"

#: Slots whose figures precede the opening bell, and are captioned as such.
PRE_OPEN_SLOTS = frozenset({"pre_open"})

#: How far from its Eastern target a run may drift and still count. Wide enough
#: to absorb GitHub's scheduling delay, less than half the hour that separates a
#: slot from its wrong-timezone twin.
TOLERANCE_MINUTES = 30


@dataclass(frozen=True)
class Resolution:
    """What a run should do."""

    run: bool
    slot: str
    refresh_board: bool
    why: str


def resolve(
    schedule: str | None,
    now_et: datetime,
    *,
    manual_slot: str | None = None,
    manual_board: bool = True,
) -> Resolution:
    """Decide whether this run fires, and as which slot.

    ``schedule`` is the cron expression GitHub reports for a scheduled run, and
    is None for a manual one. A manual run is a deliberate human act, so it is
    never gated on the calendar or the clock -- it is how you test the pipeline
    on a Sunday, and its rows carry a real ``captured_at`` saying so.
    """
    if schedule is None:
        slot = manual_slot or "manual"
        return Resolution(
            run=True,
            slot=slot,
            refresh_board=manual_board,
            why=f"manual run, recorded as {slot}",
        )

    slot = CRON_TO_SLOT.get(schedule.strip(), "")
    if not slot:
        # A cron entry was added to the workflow without being mapped here.
        # Refuse rather than guess: a mislabelled row is worse than a gap.
        return Resolution(False, "unknown", False, f"unmapped cron expression {schedule!r}")

    if not is_trading_day(now_et.date()):
        return Resolution(False, slot, False, f"{now_et.date()} is not a trading day")

    target = SLOT_TIMES[slot]
    drift = abs(_minutes(now_et.time()) - _minutes(target))
    if drift > TOLERANCE_MINUTES:
        return Resolution(
            False,
            slot,
            False,
            f"ET {now_et:%H:%M} is {drift} min from {slot} at {target:%H:%M} "
            f"-- the other timezone's entry owns this slot today",
        )

    return Resolution(
        run=True,
        slot=slot,
        refresh_board=slot in BOARD_SLOTS,
        why=f"ET {now_et:%H:%M}, {drift} min from {slot} at {target:%H:%M}",
    )


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute
