"""End-to-end collection tests, run entirely against the fake provider."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date, timedelta

from tests.test_constants import IN_PLAY_TICKER, TRADE_DATE
from warrior_screener.collector import backfill, collect_day
from warrior_screener.providers.base import ProviderError
from warrior_screener.storage import Archive


class TestCollectDay:
    def test_writes_every_artefact(self, settings, provider):
        archive = Archive(settings.data_dir)
        outcome = collect_day(settings, provider, archive, TRADE_DATE)

        assert outcome.status == "collected"
        assert outcome.in_play == 1
        assert archive.has_daily_bars(TRADE_DATE)
        assert (archive.scan_dir(TRADE_DATE) / "candidates.csv").exists()
        assert (archive.scan_dir(TRADE_DATE) / "in_play.csv").exists()
        assert archive.has_minute_bars(TRADE_DATE, IN_PLAY_TICKER)
        assert archive.features_path(TRADE_DATE).exists()
        assert archive.in_play_history_path.exists()

    def test_feature_row_joins_screen_and_intraday(self, settings, provider):
        archive = Archive(settings.data_dir)
        collect_day(settings, provider, archive, TRADE_DATE)
        with archive.features_path(TRADE_DATE).open() as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        row = rows[0]
        assert row["ticker"] == IN_PLAY_TICKER
        assert float(row["relative_volume"]) == 20.0
        assert float(row["high"]) > 0

    def test_rerun_is_skipped_unless_forced(self, settings, provider):
        archive = Archive(settings.data_dir)
        collect_day(settings, provider, archive, TRADE_DATE)
        calls_before = dict(provider.calls)

        assert collect_day(settings, provider, archive, TRADE_DATE).status == "skipped"
        assert provider.calls == calls_before

        forced = collect_day(settings, provider, archive, TRADE_DATE, force=True)
        assert forced.status == "collected"
        assert provider.calls["minute"] > calls_before["minute"]

    def test_history_is_fetched_once_and_then_cached(self, settings, provider):
        archive = Archive(settings.data_dir)
        collect_day(settings, provider, archive, TRADE_DATE)
        grouped_calls = provider.calls["grouped"]
        # 20 prior sessions plus the scan date.
        assert grouped_calls == settings.criteria.rvol_lookback_days + 1

        next_day = TRADE_DATE + timedelta(days=1)
        provider.bars_by_date[next_day] = provider.bars_by_date[TRADE_DATE]
        collect_day(settings, provider, archive, next_day)
        # Only the new session needs fetching; the RVOL window is on disk.
        assert provider.calls["grouped"] == grouped_calls + 1

    def test_market_closed_day_is_recorded_not_skipped_silently(self, settings, provider):
        archive = Archive(settings.data_dir)
        holiday = date(2026, 12, 25)
        outcome = collect_day(settings, provider, archive, holiday)
        assert outcome.status == "market_closed"

        entries = [
            json.loads(line)
            for line in (archive.root / "runs.jsonl").read_text().strip().splitlines()
        ]
        assert entries[-1]["status"] == "market_closed"

    def test_intraday_can_be_disabled(self, settings, provider):
        archive = Archive(settings.data_dir)
        collect_day(replace(settings, collect_intraday=False), provider, archive, TRADE_DATE)
        assert not archive.has_minute_bars(TRADE_DATE, IN_PLAY_TICKER)
        assert provider.calls["minute"] == 0

    def test_one_bad_symbol_does_not_lose_the_session(self, settings, provider, monkeypatch):
        archive = Archive(settings.data_dir)

        def explode(ticker, trade_date):
            raise ProviderError("simulated outage")

        monkeypatch.setattr(provider, "minute_bars", explode)
        outcome = collect_day(settings, provider, archive, TRADE_DATE)

        assert outcome.status == "collected"
        assert archive.read_in_play(TRADE_DATE)  # the screen still landed
        assert archive.features_path(TRADE_DATE).exists()

    def test_padding_produces_a_five_name_list_on_a_quiet_day(self, settings, provider):
        """The user-facing promise: 5-10 names a day, tagged by strictness."""
        archive = Archive(settings.data_dir)
        padded = replace(settings, criteria=replace(settings.criteria, fill_to_min=True))
        collect_day(padded, provider, archive, TRADE_DATE)

        rows = archive.read_in_play(TRADE_DATE)
        # The fixture universe only holds three coarse survivors, so padding
        # takes every eligible near-miss and stops -- it never invents rows.
        assert 1 <= len(rows) <= padded.criteria.min_in_play
        assert rows[0]["qualification"] == "strict"
        assert {row["qualification"] for row in rows} <= {"strict", "relaxed"}


class TestBackfill:
    def test_walks_weekdays_oldest_first(self, settings, provider):
        archive = Archive(settings.data_dir)
        start, end = date(2026, 8, 24), date(2026, 8, 28)  # Mon-Fri
        outcomes = backfill(settings, provider, archive, start, end)

        assert [o.trade_date for o in outcomes] == [
            start + timedelta(days=offset) for offset in range(5)
        ]
        assert archive.collected_dates() == [o.trade_date for o in outcomes]

    def test_skips_weekends_without_calling_the_provider(self, settings, provider):
        archive = Archive(settings.data_dir)
        outcomes = backfill(settings, provider, archive, date(2026, 8, 29), date(2026, 8, 30))
        assert outcomes == []
        assert provider.calls["grouped"] == 0

    def test_a_failing_session_does_not_abort_the_range(self, settings, provider, monkeypatch):
        archive = Archive(settings.data_dir)
        original = provider.grouped_daily
        failed_once: set[date] = set()

        def flaky(trade_date):
            # A transient outage on one session: it fails the first time it is
            # requested and recovers afterwards.
            if trade_date == date(2026, 8, 26) and trade_date not in failed_once:
                failed_once.add(trade_date)
                raise ProviderError("simulated outage")
            return original(trade_date)

        monkeypatch.setattr(provider, "grouped_daily", flaky)
        outcomes = backfill(settings, provider, archive, date(2026, 8, 25), date(2026, 8, 27))

        statuses = {o.trade_date: o.status for o in outcomes}
        assert statuses[date(2026, 8, 26)] == "failed"
        assert statuses[date(2026, 8, 27)] == "collected"

    def test_rejects_a_backwards_range(self, settings, provider):
        archive = Archive(settings.data_dir)
        try:
            backfill(settings, provider, archive, date(2026, 8, 28), date(2026, 8, 24))
        except ValueError as exc:
            assert "start date" in str(exc)
        else:
            raise AssertionError("expected ValueError for an inverted range")
