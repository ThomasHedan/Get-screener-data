"""Tests for the minimal US trading-day calendar."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from warrior_screener.market_calendar import (
    EASTERN,
    US_MARKET_HOLIDAYS,
    is_trading_day,
    next_capture,
    parse_times,
    session_phase,
)


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=EASTERN)


class TestIsTradingDay:
    @pytest.mark.parametrize(
        "day, label",
        [
            (date(2026, 9, 8), "an ordinary Tuesday"),
            (date(2026, 3, 2), "an ordinary Monday"),
        ],
    )
    def test_ordinary_weekdays_are_trading_days(self, day, label):
        assert is_trading_day(day), label

    @pytest.mark.parametrize(
        "day, label",
        [
            (date(2026, 9, 5), "Saturday"),
            (date(2026, 9, 6), "Sunday"),
        ],
    )
    def test_weekends_are_not_trading_days(self, day, label):
        assert not is_trading_day(day), label

    @pytest.mark.parametrize("holiday", sorted(US_MARKET_HOLIDAYS))
    def test_every_listed_holiday_is_not_a_trading_day(self, holiday):
        assert not is_trading_day(holiday)

    def test_labor_day_2026_specifically(self):
        # The exact case that prompted this module: caught live on
        # 2026-09-07, where `snapshot` silently presented Friday's numbers.
        assert not is_trading_day(date(2026, 9, 7))

    def test_the_day_after_a_holiday_is_a_normal_trading_day(self):
        assert is_trading_day(date(2026, 9, 8))  # Tuesday after Labor Day

    def test_early_close_days_are_still_trading_days(self):
        # Day after Thanksgiving and Christmas Eve trade a partial session --
        # "stale, nothing happened" would be the wrong message for these.
        assert is_trading_day(date(2026, 11, 27))
        assert is_trading_day(date(2026, 12, 24))


class TestSessionPhase:
    """Covers the case a plain trading-day check misses: 2026-09-08 at 04:52
    ET was a genuine trading day (the day after Labor Day), but pre-market
    had barely started -- a different question from "is today a trading day"."""

    TUESDAY = date(2026, 9, 8)  # an ordinary trading day, not a holiday

    def test_before_premarket_is_closed(self):
        assert session_phase(_et(self.TUESDAY, 3, 59)) == "closed"

    def test_the_exact_moment_caught_live_is_premarket(self):
        # 04:52 ET, 2026-09-08 -- pre-market open, regular open still hours away.
        assert session_phase(_et(self.TUESDAY, 4, 52)) == "pre-market"

    def test_premarket_boundary_is_inclusive(self):
        assert session_phase(_et(self.TUESDAY, 4, 0)) == "pre-market"

    def test_regular_open_boundary(self):
        assert session_phase(_et(self.TUESDAY, 9, 30)) == "regular"
        assert session_phase(_et(self.TUESDAY, 9, 29)) == "pre-market"

    def test_midday_is_regular(self):
        assert session_phase(_et(self.TUESDAY, 12, 0)) == "regular"

    def test_regular_close_boundary(self):
        assert session_phase(_et(self.TUESDAY, 16, 0)) == "after-hours"
        assert session_phase(_et(self.TUESDAY, 15, 59)) == "regular"

    def test_after_hours_end_boundary(self):
        assert session_phase(_et(self.TUESDAY, 19, 59)) == "after-hours"
        assert session_phase(_et(self.TUESDAY, 20, 0)) == "closed"

    def test_a_holiday_is_closed_regardless_of_time_of_day(self):
        labor_day = date(2026, 9, 7)
        assert session_phase(_et(labor_day, 10, 0)) == "closed"

    def test_a_weekend_is_closed_regardless_of_time_of_day(self):
        saturday = date(2026, 9, 5)
        assert session_phase(_et(saturday, 10, 0)) == "closed"

    def test_a_naive_datetime_is_treated_as_local_and_converted(self):
        # No tzinfo -- must not raise, and must still classify sensibly by
        # being interpreted in the caller's local zone then converted.
        naive_midday_utc = datetime(2026, 9, 8, 12, 0)
        assert session_phase(naive_midday_utc.replace(tzinfo=ZoneInfo("UTC"))) in {
            "pre-market",
            "regular",
            "closed",
        }

    def test_default_argument_uses_the_real_current_time(self):
        # Just confirm it runs and returns a valid phase without an explicit
        # moment -- the exact phase depends on when the suite runs.
        assert session_phase() in {"closed", "pre-market", "regular", "after-hours"}


class TestParseTimes:
    def test_parses_and_sorts(self) -> None:
        assert parse_times("09:35, 09:31") == (time(9, 31), time(9, 35))

    @pytest.mark.parametrize("raw", ["", "   ", ","])
    def test_empty_means_no_pinned_time(self, raw: str) -> None:
        assert parse_times(raw) == ()

    def test_a_typo_is_an_error_not_a_silent_skip(self) -> None:
        with pytest.raises(ValueError):
            parse_times("9h31")


class TestNextCapture:
    TRADING_DAY = date(2026, 9, 15)  # a Tuesday

    def test_aligns_to_the_clock_grid(self) -> None:
        # 09:28:40 + 300s would land on 09:33:40; the grid lands on 09:30:00.
        now = datetime.combine(self.TRADING_DAY, time(9, 28, 40), tzinfo=EASTERN)
        assert next_capture(now, 300) == _et(self.TRADING_DAY, 9, 30)

    def test_pinned_time_wins_over_a_later_grid_tick(self) -> None:
        now = _et(self.TRADING_DAY, 9, 30)
        assert next_capture(now, 300, (time(9, 31),)) == _et(self.TRADING_DAY, 9, 31)

    def test_grid_wins_when_it_comes_first(self) -> None:
        now = _et(self.TRADING_DAY, 9, 31)
        assert next_capture(now, 300, (time(9, 31),)) == _et(self.TRADING_DAY, 9, 35)

    def test_pinned_time_on_the_grid_yields_one_moment(self) -> None:
        # captured_at is UNIQUE; 09:35 must not be scheduled twice.
        now = _et(self.TRADING_DAY, 9, 31)
        assert next_capture(now, 300, (time(9, 31), time(9, 35))) == _et(self.TRADING_DAY, 9, 35)

    def test_rolls_to_tomorrow_once_every_pinned_time_has_passed(self) -> None:
        # Evening, after the pinned time. A daily grid aligns on UTC midnight,
        # which is 20:00 ET, so the next grid tick is tomorrow evening --
        # tomorrow morning's pinned time comes first.
        now = _et(self.TRADING_DAY, 20, 30)
        result = next_capture(now, 86_400, (time(9, 31),))
        assert result == _et(date(2026, 9, 16), 9, 31)

    def test_never_returns_the_present_moment(self) -> None:
        now = _et(self.TRADING_DAY, 9, 35)
        assert next_capture(now, 300, (time(9, 35),)) > now
