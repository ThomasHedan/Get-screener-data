"""Tests for the minimal US trading-day calendar."""

from __future__ import annotations

from datetime import date

import pytest

from warrior_screener.market_calendar import US_MARKET_HOLIDAYS, is_trading_day


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
