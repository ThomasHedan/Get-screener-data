"""Tests for the Warrior criteria, scoring and selection."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_constants import (
    BIG_FLOAT_TICKER,
    EXPENSIVE_TICKER,
    IN_PLAY_TICKER,
    LOW_RVOL_TICKER,
    TRADE_DATE,
    WARRANT_TICKER,
)
from warrior_screener.models import Candidate
from warrior_screener.scanner import (
    Enricher,
    coarse_candidates,
    evaluate,
    run_scan,
    score_candidates,
    select_in_play,
)


def _tickers(candidates) -> set[str]:
    return {candidate.ticker for candidate in candidates}


class TestCoarsePass:
    def test_keeps_the_gapper(self, scan_bars, history, criteria):
        result = coarse_candidates(scan_bars, history, criteria, limit=50)
        assert IN_PLAY_TICKER in _tickers(result)

    @pytest.mark.parametrize(
        "ticker, reason",
        [
            (EXPENSIVE_TICKER, "above max_price"),
            ("FLAT", "under min_change_pct"),
            ("THIN", "under min_day_volume"),
            (WARRANT_TICKER, "warrant suffix"),
        ],
    )
    def test_rejects(self, scan_bars, history, criteria, ticker, reason):
        result = coarse_candidates(scan_bars, history, criteria, limit=50)
        assert ticker not in _tickers(result), f"{ticker} should be dropped: {reason}"

    def test_computes_change_and_rvol(self, scan_bars, history, criteria):
        candidate = next(
            c
            for c in coarse_candidates(scan_bars, history, criteria, limit=50)
            if c.ticker == IN_PLAY_TICKER
        )
        assert candidate.change_pct == pytest.approx(40.0)  # 3.00 -> 4.20
        assert candidate.gap_pct == pytest.approx(20.0)  # 3.00 -> 3.60 open
        assert candidate.relative_volume == pytest.approx(20.0)  # 4M on a 200k average

    def test_ticker_without_prior_close_is_skipped(self, scan_bars, history, criteria):
        history.prev_close.pop(IN_PLAY_TICKER)
        assert IN_PLAY_TICKER not in _tickers(
            coarse_candidates(scan_bars, history, criteria, limit=50)
        )

    def test_limit_keeps_the_strongest(self, scan_bars, history, criteria):
        result = coarse_candidates(scan_bars, history, criteria, limit=1)
        assert len(result) == 1
        assert result[0].ticker == IN_PLAY_TICKER

    def test_empty_market_returns_nothing(self, history, criteria):
        assert coarse_candidates([], history, criteria, limit=10) == []


class TestEvaluate:
    def _candidate(self, **overrides) -> Candidate:
        defaults = {
            "ticker": IN_PLAY_TICKER,
            "trade_date": TRADE_DATE,
            "relative_volume": 20.0,
            "float_shares": 8_000_000,
            "security_type": "CS",
            "primary_exchange": "XNAS",
            "news_count": 2,
            "news_checked": True,
        }
        defaults.update(overrides)
        return Candidate(**defaults)

    def test_clean_candidate_passes(self, criteria):
        assert evaluate(self._candidate(), criteria) == []

    @pytest.mark.parametrize(
        "overrides, expected",
        [
            ({"float_shares": 90_000_000}, "float"),
            ({"float_shares": None}, "float"),
            ({"relative_volume": 1.5}, "relative_volume"),
            ({"relative_volume": None}, "relative_volume"),
            ({"news_count": 0}, "news"),
            ({"news_count": 0, "news_checked": False}, "news_unknown"),
            ({"security_type": "ETF"}, "security_type"),
            ({"primary_exchange": "OTC"}, "exchange"),
        ],
    )
    def test_each_criterion_is_enforced(self, criteria, overrides, expected):
        assert expected in evaluate(self._candidate(**overrides), criteria)

    def test_unknown_reference_does_not_trip_type_or_exchange(self, criteria):
        # A delisted name the provider no longer describes should be judged on
        # what we do know, not rejected for a missing security type.
        reasons = evaluate(self._candidate(security_type=None, primary_exchange=None), criteria)
        assert "security_type" not in reasons
        assert "exchange" not in reasons

    def test_unchecked_news_is_not_reported_as_missing_news(self, criteria):
        # "we never looked" must not be recorded as "there was no catalyst".
        reasons = evaluate(self._candidate(news_count=0, news_checked=False), criteria)
        assert reasons == ["news_unknown"]

    def test_news_not_required_when_disabled(self, criteria):
        relaxed = replace(criteria, require_news_catalyst=False)
        assert evaluate(self._candidate(news_count=0), relaxed) == []


class TestScoring:
    def _pool(self) -> list[Candidate]:
        return [
            Candidate(
                ticker="HIGH",
                trade_date=TRADE_DATE,
                relative_volume=50.0,
                change_pct=90.0,
                float_shares=2_000_000,
                news_count=3,
            ),
            Candidate(
                ticker="MID",
                trade_date=TRADE_DATE,
                relative_volume=10.0,
                change_pct=30.0,
                float_shares=9_000_000,
                news_count=1,
            ),
            Candidate(
                ticker="LOW",
                trade_date=TRADE_DATE,
                relative_volume=6.0,
                change_pct=12.0,
                float_shares=40_000_000,
                news_count=0,
            ),
        ]

    def test_ranks_the_strongest_setup_first(self, criteria):
        pool = self._pool()
        score_candidates(pool, criteria)
        assert [c.ticker for c in sorted(pool, key=lambda c: -c.score)] == ["HIGH", "MID", "LOW"]

    def test_scores_are_bounded(self, criteria):
        pool = self._pool()
        score_candidates(pool, criteria)
        assert all(0.0 <= candidate.score <= 1.0 for candidate in pool)

    def test_single_candidate_is_scored(self, criteria):
        pool = self._pool()[:1]
        score_candidates(pool, criteria)
        assert pool[0].score > 0

    def test_empty_pool_is_a_no_op(self, criteria):
        score_candidates([], criteria)  # must not raise

    def test_missing_float_is_not_rewarded(self, criteria):
        with_float = Candidate(
            ticker="A",
            trade_date=TRADE_DATE,
            relative_volume=10.0,
            change_pct=30.0,
            float_shares=5_000_000,
            news_count=1,
        )
        without = Candidate(
            ticker="B",
            trade_date=TRADE_DATE,
            relative_volume=10.0,
            change_pct=30.0,
            float_shares=None,
            news_count=1,
        )
        score_candidates([with_float, without], criteria)
        assert with_float.score > without.score


class TestSelection:
    def _candidates(self, count: int, *, rejected: list[str] | None = None) -> list[Candidate]:
        return [
            Candidate(
                ticker=f"T{index}",
                trade_date=TRADE_DATE,
                score=1.0 - index / 100,
                rejected_by=list(rejected or []),
            )
            for index in range(count)
        ]

    def test_caps_at_max_in_play(self, criteria):
        selected = select_in_play(self._candidates(25), replace(criteria, max_in_play=10))
        assert len(selected) == 10
        assert all(c.qualification == "strict" for c in selected)

    def test_orders_by_score(self, criteria):
        selected = select_in_play(self._candidates(5), criteria)
        assert [c.ticker for c in selected] == ["T0", "T1", "T2", "T3", "T4"]

    def test_quiet_day_returns_only_real_qualifiers_when_padding_is_off(self, criteria):
        pool = self._candidates(2) + self._candidates(6, rejected=["float"])[2:]
        assert len(select_in_play(pool, criteria)) == 2

    def test_padding_tops_up_to_min_in_play(self, criteria):
        padded = replace(criteria, fill_to_min=True, min_in_play=5)
        strict = self._candidates(2)
        near_misses = [
            Candidate(
                ticker=f"N{i}", trade_date=TRADE_DATE, score=0.5 - i / 100, rejected_by=["float"]
            )
            for i in range(6)
        ]
        selected = select_in_play(strict + near_misses, padded)
        assert len(selected) == 5
        assert [c.qualification for c in selected] == ["strict"] * 2 + ["relaxed"] * 3

    def test_padding_never_admits_a_core_failure(self, criteria):
        padded = replace(criteria, fill_to_min=True, min_in_play=5)
        # "exchange" is not in relaxed_drop_filters, so these stay out entirely.
        selected = select_in_play(self._candidates(6, rejected=["exchange"]), padded)
        assert selected == []


class TestRunScan:
    def test_end_to_end_selects_the_setup(self, scan_bars, history, settings, archive, provider):
        result = run_scan(
            scan_bars, history, settings, Enricher(provider, archive, settings), TRADE_DATE
        )
        assert [c.ticker for c in result.in_play] == [IN_PLAY_TICKER]
        assert result.stats["strict_qualifiers"] == 1

    def test_near_misses_are_archived_with_their_reasons(
        self, scan_bars, history, settings, archive, provider
    ):
        result = run_scan(
            scan_bars, history, settings, Enricher(provider, archive, settings), TRADE_DATE
        )
        by_ticker = {c.ticker: c for c in result.candidates}
        assert by_ticker[BIG_FLOAT_TICKER].rejected_by == ["float", "news_unknown"]
        assert "relative_volume" in by_ticker[LOW_RVOL_TICKER].rejected_by

    def test_news_is_not_requested_for_structurally_dead_candidates(
        self, scan_bars, history, settings, archive, provider
    ):
        run_scan(scan_bars, history, settings, Enricher(provider, archive, settings), TRADE_DATE)
        # Three coarse survivors get a reference lookup; only the one that is
        # still in the running is worth a news call.
        assert provider.calls["news"] == 1
        assert provider.calls["reference"] == 3

    def test_cache_only_enricher_makes_no_requests(
        self, scan_bars, history, settings, archive, provider
    ):
        run_scan(scan_bars, history, settings, Enricher(provider, archive, settings), TRADE_DATE)
        provider.calls["reference"] = 0
        run_scan(scan_bars, history, settings, Enricher(None, archive, settings), TRADE_DATE)
        assert provider.calls["reference"] == 0
