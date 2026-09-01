"""Tests for using the TradingView snapshot as a reference-lookup source.

Verifies the priority order (on-disk cache -> snapshot -> historical
provider), the fallback for tickers missing from the snapshot (typically
delisted names), and that a snapshot outage degrades gracefully rather than
breaking a collection.
"""

from __future__ import annotations

from dataclasses import replace

from tests.test_constants import IN_PLAY_TICKER, TRADE_DATE
from warrior_screener.collector import _maybe_fetch_snapshot, collect_day
from warrior_screener.models import Candidate
from warrior_screener.providers.tradingview import TradingViewError
from warrior_screener.scanner import Enricher
from warrior_screener.storage import Archive


def make_snapshot_row(ticker: str = IN_PLAY_TICKER, **overrides):
    from tests.test_live_snapshot import make_row

    return make_row(ticker, **overrides)


class TestEnricherSnapshotPriority:
    def _candidate(self) -> Candidate:
        return Candidate(ticker=IN_PLAY_TICKER, trade_date=TRADE_DATE, close=4.2)

    def test_snapshot_hit_answers_without_a_provider_call(self, archive, settings, provider):
        snapshot = {IN_PLAY_TICKER: make_snapshot_row(float_shares=6_000_000.0)}
        enricher = Enricher(provider, archive, settings, snapshot=snapshot)

        reference = enricher._reference(IN_PLAY_TICKER, TRADE_DATE)

        assert reference is not None
        assert reference.shares_outstanding == 6_000_000
        assert provider.calls["reference"] == 0
        assert enricher.snapshot_hits == 1

    def test_ticker_missing_from_snapshot_falls_back_to_the_provider(
        self, archive, settings, provider
    ):
        enricher = Enricher(provider, archive, settings, snapshot={})  # e.g. delisted

        enricher._reference(IN_PLAY_TICKER, TRADE_DATE)

        assert provider.calls["reference"] == 1
        assert enricher.snapshot_hits == 0

    def test_on_disk_cache_still_wins_over_the_snapshot(self, archive, settings, provider):
        # A fresh on-disk cache entry should never be second-guessed by a
        # snapshot lookup -- it is cheaper and was itself possibly sourced
        # from the snapshot on an earlier run.
        enricher = Enricher(
            provider, archive, settings, snapshot={IN_PLAY_TICKER: make_snapshot_row()}
        )
        enricher._reference(IN_PLAY_TICKER, TRADE_DATE)  # populates the on-disk cache
        enricher._snapshot_hits = 0
        provider.calls["reference"] = 0

        enricher._reference(IN_PLAY_TICKER, TRADE_DATE)

        assert provider.calls["reference"] == 0
        assert enricher.snapshot_hits == 0  # answered from cache, not re-counted

    def test_snapshot_reference_is_recorded_to_the_archive_log(self, archive, settings, provider):
        enricher = Enricher(
            provider, archive, settings, snapshot={IN_PLAY_TICKER: make_snapshot_row()}
        )
        enricher._reference(IN_PLAY_TICKER, TRADE_DATE)
        log = (archive.reference_dir / "tickers.jsonl").read_text()
        assert IN_PLAY_TICKER in log

    def test_enrich_populates_the_candidate_from_the_snapshot(self, archive, settings, provider):
        snapshot = {
            IN_PLAY_TICKER: make_snapshot_row(
                exchange="XNAS", security_type="stock", float_shares=3_000_000.0
            )
        }
        enricher = Enricher(provider, archive, settings, snapshot=snapshot)
        candidate = self._candidate()

        enricher.enrich(candidate, settings.criteria)

        assert candidate.security_type == "CS"
        assert candidate.primary_exchange == "XNAS"
        assert candidate.float_shares == 3_000_000


class TestMaybeFetchSnapshot:
    def test_disabled_returns_empty_without_a_network_call(self, settings, monkeypatch):
        def explode():
            raise AssertionError("fetch_market_snapshot must not be called when disabled")

        monkeypatch.setattr("warrior_screener.collector.fetch_market_snapshot", explode)
        assert _maybe_fetch_snapshot(replace(settings, use_tradingview_reference=False)) == {}

    def test_enabled_returns_a_ticker_keyed_dict(self, settings, monkeypatch):
        rows = [make_snapshot_row("AAA"), make_snapshot_row("BBB")]
        monkeypatch.setattr("warrior_screener.collector.fetch_market_snapshot", lambda: rows)
        result = _maybe_fetch_snapshot(replace(settings, use_tradingview_reference=True))
        assert set(result) == {"AAA", "BBB"}

    def test_a_provider_outage_degrades_to_an_empty_dict_not_an_exception(
        self, settings, monkeypatch
    ):
        def explode():
            raise TradingViewError("scanner unreachable")

        monkeypatch.setattr("warrior_screener.collector.fetch_market_snapshot", explode)
        assert _maybe_fetch_snapshot(replace(settings, use_tradingview_reference=True)) == {}


class TestCollectDayUsesTheSnapshot:
    def test_snapshot_reference_replaces_the_polygon_reference_call(
        self, settings, provider, tmp_path
    ):
        archive = Archive(tmp_path / "data2")
        snapshot = {IN_PLAY_TICKER: make_snapshot_row(float_shares=8_000_000.0)}

        outcome = collect_day(
            replace(settings, use_tradingview_reference=True),
            provider,
            archive,
            TRADE_DATE,
            tradingview_snapshot=snapshot,
        )

        assert outcome.status == "collected"
        # The IN_PLAY_TICKER's own reference lookup was answered by the
        # snapshot; only the other coarse survivors still hit Polygon.
        assert provider.calls["reference"] < 3

    def test_an_explicit_snapshot_is_not_refetched(self, settings, provider, monkeypatch):
        archive = Archive(settings.data_dir)

        def explode():
            raise AssertionError("collect_day must not re-fetch a snapshot it was given")

        monkeypatch.setattr("warrior_screener.collector.fetch_market_snapshot", explode)
        collect_day(
            replace(settings, use_tradingview_reference=True),
            provider,
            archive,
            TRADE_DATE,
            tradingview_snapshot={},
        )  # must not raise


class TestBackfillFetchesOnce:
    def test_snapshot_is_fetched_exactly_once_for_the_whole_range(
        self, settings, provider, monkeypatch
    ):
        from datetime import date

        from warrior_screener.collector import backfill

        archive = Archive(settings.data_dir)
        calls = {"count": 0}

        def counting_fetch():
            calls["count"] += 1
            return [make_snapshot_row(IN_PLAY_TICKER)]

        monkeypatch.setattr("warrior_screener.collector.fetch_market_snapshot", counting_fetch)
        start, end = date(2026, 8, 24), date(2026, 8, 28)  # Mon-Fri
        backfill(replace(settings, use_tradingview_reference=True), provider, archive, start, end)

        assert calls["count"] == 1
