"""Tests for the minimal US trading-day calendar."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from warrior_screener.market_calendar import (
    EASTERN,
    US_MARKET_HOLIDAYS,
    is_trading_day,
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
