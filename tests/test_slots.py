"""Tests for slot resolution: the DST pairing and the delay tolerance."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from warrior_screener.slots import CRON_TO_SLOT, SLOT_TIMES, resolve

EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# A Wednesday in each half of the year, neither of them a market holiday.
SUMMER = datetime(2026, 7, 8, tzinfo=UTC)  # EDT, UTC-4
WINTER = datetime(2026, 12, 2, tzinfo=UTC)  # EST, UTC-5


def fire(cron: str, day: datetime) -> datetime:
    """The Eastern time at which GitHub would fire ``cron`` on ``day``."""
    minute, hour = cron.split()[0], cron.split()[1]
    return day.replace(hour=int(hour), minute=int(minute)).astimezone(EASTERN)


class TestDaylightSavingPairing:
    """Every slot must fire exactly once a day, in both halves of the year.

    This is the property the whole two-entries-per-slot design exists for: if
    both entries of a pair ever pass, the archive gets a duplicate row at the
    wrong time; if neither does, the measurement is simply missing.
    """

    @pytest.mark.parametrize("day,label", [(SUMMER, "EDT"), (WINTER, "EST")])
    def test_exactly_one_cron_per_slot_survives(self, day, label):
        fired: dict[str, list[str]] = {slot: [] for slot in SLOT_TIMES}
        for cron, slot in CRON_TO_SLOT.items():
            decision = resolve(cron, fire(cron, day))
            if decision.run:
                fired[slot].append(cron)

        for slot, crons in fired.items():
            assert len(crons) == 1, f"{slot} fired {len(crons)} times in {label}: {crons}"

    @pytest.mark.parametrize("day", [SUMMER, WINTER])
    def test_the_surviving_run_lands_on_its_eastern_target(self, day):
        for cron, slot in CRON_TO_SLOT.items():
            moment = fire(cron, day)
            if resolve(cron, moment).run:
                assert moment.time().replace(second=0, microsecond=0) == SLOT_TIMES[slot]

    def test_the_wrong_timezone_twin_says_why_it_stood_down(self):
        # 14:25 UTC is 10:25 ET in summer -- an hour past the pre-open slot.
        decision = resolve("25 14 * * 1-5", fire("25 14 * * 1-5", SUMMER))
        assert decision.run is False
        assert "60 min from pre_open" in decision.why


class TestSchedulerDelay:
    """A late run keeps its identity; the slot comes from the cron, not the clock."""

    def test_a_run_delayed_past_the_next_slot_keeps_its_own(self):
        # t_plus_5 firing 7 minutes late lands at 09:42 -- past t_plus_10's
        # 09:40. Reading the clock would relabel it; reading the cron does not.
        late = datetime(2026, 7, 8, 9, 42, tzinfo=EASTERN)
        decision = resolve("35 13 * * 1-5", late)
        assert decision.run is True
        assert decision.slot == "t_plus_5"

    def test_a_run_inside_the_tolerance_still_fires(self):
        decision = resolve("40 13 * * 1-5", datetime(2026, 7, 8, 10, 9, tzinfo=EASTERN))
        assert decision.run is True

    def test_a_run_beyond_the_tolerance_is_dropped(self):
        # 40 minutes late is indistinguishable from the other timezone's entry,
        # so the honest move is to record nothing.
        decision = resolve("40 13 * * 1-5", datetime(2026, 7, 8, 10, 20, tzinfo=EASTERN))
        assert decision.run is False


class TestGating:
    def test_a_market_holiday_is_skipped(self):
        # 2026-09-07 is Labor Day: TradingView would serve Friday's numbers and
        # the archive would file them as Monday's.
        decision = resolve("35 13 * * 1-5", datetime(2026, 9, 7, 9, 35, tzinfo=EASTERN))
        assert decision.run is False
        assert "not a trading day" in decision.why

    def test_an_unmapped_cron_refuses_rather_than_guessing(self):
        decision = resolve("0 12 * * 1-5", datetime(2026, 7, 8, 8, 0, tzinfo=EASTERN))
        assert decision.run is False
        assert "unmapped" in decision.why

    def test_the_pre_open_slot_publishes_as_its_own_view(self):
        # It publishes, because the pre-open watchlist is half of what the page
        # is for. What protects the reader is the caption on that view, not
        # withholding it -- see PRE_OPEN_SLOTS.
        decision = resolve("25 13 * * 1-5", fire("25 13 * * 1-5", SUMMER))
        assert decision.run is True
        assert decision.refresh_board is True

    # BOARD_SLOTS also holds "manual", which has no cron entry by definition.
    @pytest.mark.parametrize("slot", sorted(SLOT_TIMES))
    def test_every_scheduled_slot_publishes(self, slot):
        summer_hours = ("13", "19")  # the EDT half of each pair
        cron = next(
            c for c, s in CRON_TO_SLOT.items() if s == slot and c.split()[1] in summer_hours
        )
        decision = resolve(cron, fire(cron, SUMMER))
        assert decision.run is True
        assert decision.refresh_board is True


class TestManualRuns:
    def test_a_manual_run_is_never_gated(self):
        # Sunday, and a holiday-free weekend: a human asked, so it runs.
        decision = resolve(None, datetime(2026, 7, 5, 3, 0, tzinfo=EASTERN))
        assert decision.run is True
        assert decision.slot == "manual"

    def test_a_manual_run_can_name_its_slot(self):
        decision = resolve(None, datetime(2026, 7, 8, 9, 35, tzinfo=EASTERN), manual_slot="close")
        assert decision.slot == "close"

    def test_a_manual_run_can_decline_to_touch_the_board(self):
        decision = resolve(None, datetime(2026, 7, 8, 9, 35, tzinfo=EASTERN), manual_board=False)
        assert decision.refresh_board is False
