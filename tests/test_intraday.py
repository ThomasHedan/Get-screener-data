"""Tests for the intraday feature extraction."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_minute_bars
from tests.test_constants import IN_PLAY_TICKER, TRADE_DATE
from warrior_screener.intraday import compute_features
from warrior_screener.models import Candidate, MinuteBar

EASTERN = ZoneInfo("America/New_York")


@pytest.fixture
def candidate() -> Candidate:
    return Candidate(
        ticker=IN_PLAY_TICKER,
        trade_date=TRADE_DATE,
        prev_close=3.0,
        gap_pct=20.0,
        change_pct=40.0,
        relative_volume=20.0,
        float_shares=8_000_000,
        news_count=3,
        score=0.87,
        qualification="strict",
    )


@pytest.fixture
def features(candidate) -> dict:
    return compute_features(candidate, make_minute_bars(IN_PLAY_TICKER), TRADE_DATE)


class TestSessionSplit:
    def test_premarket_is_measured_separately(self, features):
        # The fixture runs 04:00 onward, so 330 pre-market minutes at 20k each.
        assert features["premarket_volume"] == 330 * 20_000
        assert features["premarket_high"] is not None

    def test_regular_session_open_and_close(self, features):
        assert features["open"] is not None
        assert features["close"] is not None
        assert features["minutes_traded"] == 70  # 09:30 to 10:39 in the fixture

    def test_high_of_day_timing(self, features):
        # The fixture peaks 345 minutes after 04:00, i.e. 09:45 ET.
        assert features["high_time"] == "09:45"
        assert features["minutes_to_high"] == 15

    def test_untraded_minutes_flag_the_halt_gap(self, features):
        assert features["untraded_minutes"] == 390 - 70


class TestDerivedMetrics:
    def test_vwap_sits_inside_the_range(self, features):
        assert features["low"] <= features["rth_vwap"] <= features["high"]

    def test_close_vs_high_is_negative_on_a_fade(self, features):
        assert features["close_vs_high_pct"] < 0

    def test_volume_shares_are_fractions(self, features):
        assert 0 < features["first_30min_volume_share"] <= 1
        assert features["first_hour_volume_share"] >= features["first_30min_volume_share"]

    def test_big_volume_minutes_counted(self, features):
        assert features["big_volume_minutes"] > 0

    def test_screen_fields_are_carried_through(self, features):
        assert features["ticker"] == IN_PLAY_TICKER
        assert features["float_shares"] == 8_000_000
        assert features["qualification"] == "strict"


class TestEdgeCases:
    def test_no_bars_still_produces_a_row(self, candidate):
        row = compute_features(candidate, [], TRADE_DATE)
        assert row["ticker"] == IN_PLAY_TICKER
        assert row["minute_bars"] == 0
        assert row["open"] is None
        assert row["premarket_volume"] is None

    def test_premarket_only_session(self, candidate):
        bars = [b for b in make_minute_bars(IN_PLAY_TICKER) if b.timestamp.hour < 9]
        row = compute_features(candidate, bars, TRADE_DATE)
        assert row["premarket_volume"] > 0
        assert row["open"] is None

    def test_single_minute_session(self, candidate):
        start = datetime.combine(TRADE_DATE, datetime.min.time(), tzinfo=EASTERN) + timedelta(
            hours=9, minutes=30
        )
        bar = MinuteBar(IN_PLAY_TICKER, start, 4.0, 4.5, 3.9, 4.4, 50_000, 4.2, 100)
        row = compute_features(candidate, [bar], TRADE_DATE)
        assert row["open"] == 4.0
        assert row["close"] == 4.4
        assert row["minutes_to_high"] == 0
        assert row["first_hour_volume_share"] == 1.0

    def test_zero_volume_bars_do_not_divide_by_zero(self, candidate):
        start = datetime.combine(TRADE_DATE, datetime.min.time(), tzinfo=EASTERN) + timedelta(
            hours=9, minutes=30
        )
        bars = [MinuteBar(IN_PLAY_TICKER, start, 4.0, 4.0, 4.0, 4.0, 0, None, 0)]
        row = compute_features(candidate, bars, TRADE_DATE)
        assert row["rth_vwap"] is None
        assert row["first_30min_volume_share"] is None
