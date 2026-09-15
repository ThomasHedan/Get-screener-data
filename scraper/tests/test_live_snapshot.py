"""Tests for the live snapshot screen, run against synthetic snapshot rows."""

from __future__ import annotations

from dataclasses import replace

import pytest

from warrior_screener.config import Criteria
from warrior_screener.live_snapshot import candidates_from_snapshot
from warrior_screener.providers.tradingview import MarketSnapshotRow
from warrior_screener.scanner import evaluate


def make_row(ticker: str = "GAPR", **overrides) -> MarketSnapshotRow:
    defaults = dict(
        ticker=ticker,
        exchange="XNAS",
        security_type="stock",
        security_subtype="common",
        open=3.6,
        high=4.8,
        low=3.4,
        close=4.2,
        change_pct=40.0,
        volume=4_000_000,
        relative_volume=20.0,
        average_volume=200_000.0,
        market_cap=33_600_000.0,
        float_shares=8_000_000.0,
        sector="Health Technology",
    )
    defaults.update(overrides)
    return MarketSnapshotRow(**defaults)


@pytest.fixture
def criteria() -> Criteria:
    return replace(Criteria(), fill_to_min=False)


class TestCandidateConstruction:
    def test_a_clean_setup_becomes_a_qualifying_candidate(self, criteria):
        candidates = candidates_from_snapshot([make_row()], criteria)
        assert len(candidates) == 1
        assert candidates[0].ticker == "GAPR"
        assert candidates[0].change_pct == 40.0
        assert candidates[0].relative_volume == 20.0

    def test_news_is_never_claimed_absent_only_unchecked(self, criteria):
        candidate = candidates_from_snapshot([make_row()], criteria)[0]
        assert candidate.news_checked is False
        assert candidate.news_count == 0
        # The shared evaluate() must therefore report "unknown", not "missing".
        assert "news_unknown" in evaluate(candidate, criteria)
        assert "news" not in evaluate(candidate, criteria)

    def test_common_stock_maps_to_cs_and_dr_to_adrc(self, criteria):
        stock = candidates_from_snapshot([make_row(security_type="stock")], criteria)[0]
        adr = candidates_from_snapshot([make_row(ticker="ADR", security_type="dr")], criteria)[0]
        assert stock.security_type == "CS"
        assert adr.security_type == "ADRC"

    def test_float_is_carried_through(self, criteria):
        candidate = candidates_from_snapshot([make_row(float_shares=5_000_000.0)], criteria)[0]
        assert candidate.float_shares == 5_000_000

    def test_missing_float_is_none_not_zero(self, criteria):
        candidate = candidates_from_snapshot([make_row(float_shares=None)], criteria)[0]
        assert candidate.float_shares is None


class TestCoarseFilters:
    @pytest.mark.parametrize(
        "overrides, reason",
        [
            ({"close": 25.0}, "above max_price"),
            ({"close": 0.5}, "below min_price"),
            ({"change_pct": 3.0}, "under min_change_pct"),
            ({"volume": 1_000}, "under min_day_volume"),
        ],
    )
    def test_rejects_before_scoring(self, criteria, overrides, reason):
        candidates = candidates_from_snapshot([make_row(**overrides)], criteria)
        assert candidates == [], f"should have been dropped: {reason}"

    def test_gap_filter_is_opt_in(self, criteria):
        gappy = replace(criteria, min_gap_pct=10.0)
        # open=3.6, and prev_close derives to 3.0 from close=4.2/change=40% -> gap 20%: passes.
        assert len(candidates_from_snapshot([make_row()], gappy)) == 1
        # A candidate that only ramped up after the open, without gapping, should drop.
        no_gap = make_row(open=3.0)  # gap_pct = (3.0-3.0)/3.0 = 0%
        assert candidates_from_snapshot([no_gap], gappy) == []

    def test_unknown_gap_is_not_treated_as_a_gap_down(self, criteria):
        # TradingView reports open=0 for a name that has not traded the regular
        # session yet, so gap_pct is undefined pre-open. Dropping those would
        # empty the 09:25 scan and would call "unknown" a failure -- the mistake
        # news_checked exists to avoid.
        gappy = replace(criteria, min_gap_pct=0.0)
        kept = candidates_from_snapshot([make_row(open=0.0)], gappy)
        assert len(kept) == 1
        assert kept[0].gap_pct is None, "an unverified gap must stay visibly unknown"
        # prev_close derives to 3.0; open=2.7 is a real -10% gap-down and drops.
        assert candidates_from_snapshot([make_row(open=2.7)], gappy) == []

    def test_undefined_prev_close_still_screens_on_change_pct(self, criteria):
        # change_pct comes straight from TradingView; prev_close is only derived
        # for the optional gap filter, so a pathological -100% row must not
        # crash the coarse pass even though its prev_close is undefined.
        row = make_row(change_pct=-100.0)
        candidates = candidates_from_snapshot([row], criteria)
        assert candidates == []  # -100% also fails min_change_pct, but must not raise

    def test_empty_snapshot_returns_nothing(self, criteria):
        assert candidates_from_snapshot([], criteria) == []


class TestExchangeMapping:
    def test_mic_coded_exchange_passes_the_shared_allowed_exchanges_filter(self, criteria):
        # Regression guard: candidates_from_snapshot must receive already-
        # translated MIC codes from the provider layer, or every row fails
        # the exchange check silently (see TestParsing in test_tradingview.py).
        candidate = candidates_from_snapshot([make_row(exchange="XNAS")], criteria)[0]
        assert "exchange" not in evaluate(candidate, criteria)


class TestIntradayMomentum:
    """The move since the open, which is what the 09:35 capture is taken for."""

    def test_open_change_excludes_the_overnight_gap(self, criteria):
        # Gapped to 10.00 at the bell, trades 11.00 five minutes later: the
        # first five minutes are +10%, whatever yesterday's close was.
        row = make_row(open=10.0, close=11.0, high=11.2, low=9.9, change_pct=120.0)
        candidate = candidates_from_snapshot([row], criteria)[0]
        assert candidate.open_change_pct == 10.0
        assert candidate.change_pct == 120.0

    def test_a_stale_gapper_reads_flat_since_the_open(self, criteria):
        # Up 40% on the day but unchanged since the bell -- the case change_pct
        # cannot tell apart from a live mover.
        row = make_row(open=7.0, close=7.0, high=7.3, low=6.8, change_pct=40.0)
        candidate = candidates_from_snapshot([row], criteria)[0]
        assert candidate.open_change_pct == 0.0

    def test_pre_open_is_unknown_not_zero(self, criteria):
        # TradingView reports open=0 before the regular session starts.
        candidate = candidates_from_snapshot([make_row(open=0.0)], criteria)[0]
        assert candidate.open_change_pct is None
        assert candidate.range_position is not None

    def test_range_position_places_the_last_price(self, criteria):
        row = make_row(open=5.0, low=4.0, high=8.0, close=7.0)
        assert candidates_from_snapshot([row], criteria)[0].range_position == 0.75

    def test_range_position_is_unknown_with_no_range(self, criteria):
        row = make_row(open=5.0, low=5.0, high=5.0, close=5.0)
        assert candidates_from_snapshot([row], criteria)[0].range_position is None


class TestIntradayMomentumFilters:
    def test_min_open_change_drops_a_name_below_its_open(self, criteria):
        criteria = replace(criteria, min_open_change_pct=0.0)
        fading = make_row(ticker="FADE", open=10.0, close=9.0, high=10.5, low=8.9)
        holding = make_row(ticker="HOLD", open=10.0, close=11.0, high=11.2, low=9.9)
        kept = candidates_from_snapshot([fading, holding], criteria)
        assert [c.ticker for c in kept] == ["HOLD"]

    def test_pre_open_survives_the_filter(self, criteria):
        # Same rule as the gap filter: unknown is not a failure, or the 09:25
        # capture would come back empty every morning.
        criteria = replace(criteria, min_open_change_pct=0.0)
        kept = candidates_from_snapshot([make_row(open=0.0)], criteria)
        assert len(kept) == 1

    def test_min_range_position_drops_a_name_on_its_lows(self, criteria):
        criteria = replace(criteria, min_range_position=0.5)
        sinking = make_row(ticker="SINK", open=5.0, low=4.0, high=8.0, close=4.5)
        strong = make_row(ticker="TOPS", open=5.0, low=4.0, high=8.0, close=7.5)
        kept = candidates_from_snapshot([sinking, strong], criteria)
        assert [c.ticker for c in kept] == ["TOPS"]
