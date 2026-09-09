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

    def test_float_and_shares_outstanding_both_carry_the_same_proxy(self, criteria):
        candidate = candidates_from_snapshot([make_row(float_shares=5_000_000.0)], criteria)[0]
        assert candidate.float_shares == 5_000_000
        assert candidate.shares_outstanding == 5_000_000

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
