"""Tests for the on-disk archive."""

from __future__ import annotations

import csv
from datetime import date

from tests.conftest import make_minute_bars
from tests.test_constants import IN_PLAY_TICKER, TRADE_DATE
from warrior_screener.models import Candidate, DailyBar, TickerReference


def _candidate(ticker: str = IN_PLAY_TICKER, **overrides) -> Candidate:
    defaults = {"score": 0.9, "qualification": "strict", "close": 4.2, "news_count": 3}
    defaults.update(overrides)
    return Candidate(ticker=ticker, trade_date=TRADE_DATE, **defaults)


class TestDailyBars:
    def test_round_trip(self, archive):
        bars = [DailyBar("AAA", TRADE_DATE, 1.0, 2.0, 0.9, 1.8, 1_000, 1.5, 42)]
        archive.write_daily_bars(TRADE_DATE, bars)
        assert archive.read_daily_bars(TRADE_DATE) == bars

    def test_missing_session_reads_empty(self, archive):
        assert archive.read_daily_bars(TRADE_DATE) == []
        assert not archive.has_daily_bars(TRADE_DATE)

    def test_holiday_is_remembered_as_an_empty_file(self, archive):
        # This is what stops the history walker re-requesting known holidays.
        archive.write_daily_bars(TRADE_DATE, [])
        assert archive.has_daily_bars(TRADE_DATE)
        assert archive.read_daily_bars(TRADE_DATE) == []


class TestScans:
    def test_writes_candidates_and_in_play(self, archive):
        archive.write_scan(TRADE_DATE, [_candidate(), _candidate("OTHR")], [_candidate()])
        assert len(archive.read_candidates(TRADE_DATE)) == 2
        rows = archive.read_in_play(TRADE_DATE)
        assert [row["ticker"] for row in rows] == [IN_PLAY_TICKER]

    def test_history_accumulates_across_sessions(self, archive):
        archive.write_scan(TRADE_DATE, [], [_candidate()])
        archive.write_scan(date(2026, 8, 31), [], [_candidate("NEXT")])
        with archive.in_play_history_path.open() as handle:
            rows = list(csv.DictReader(handle))
        assert {row["ticker"] for row in rows} == {IN_PLAY_TICKER, "NEXT"}

    def test_recollecting_a_session_replaces_its_history_rows(self, archive):
        archive.write_scan(TRADE_DATE, [], [_candidate(), _candidate("DUPE")])
        archive.write_scan(TRADE_DATE, [], [_candidate()])
        with archive.in_play_history_path.open() as handle:
            rows = list(csv.DictReader(handle))
        assert [row["ticker"] for row in rows] == [IN_PLAY_TICKER]

    def test_collected_dates_ignores_stray_directories(self, archive):
        archive.write_scan(TRADE_DATE, [], [_candidate()])
        (archive.root / "scans" / "notadate").mkdir(parents=True)
        assert archive.collected_dates() == [TRADE_DATE]


class TestIntraday:
    def test_round_trip_minute_bars(self, archive):
        bars = make_minute_bars(IN_PLAY_TICKER, count=10)
        path = archive.write_minute_bars(TRADE_DATE, IN_PLAY_TICKER, bars)
        assert archive.has_minute_bars(TRADE_DATE, IN_PLAY_TICKER)
        with path.open() as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 10
        assert rows[0]["timestamp"].startswith(f"{TRADE_DATE.isoformat()}T04:00")

    def test_class_share_tickers_get_a_safe_filename(self, archive):
        archive.write_minute_bars(TRADE_DATE, "BRK.B", [])
        assert (archive.intraday_dir(TRADE_DATE) / "BRK.B.csv").exists()


class TestReference:
    def test_reference_log_is_append_only(self, archive):
        ref = TickerReference(
            ticker=IN_PLAY_TICKER, name="Gapper Inc", shares_outstanding=8_000_000
        )
        archive.record_reference(ref)
        archive.record_reference(ref)
        lines = (archive.reference_dir / "tickers.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2

    def test_cache_round_trip(self, archive):
        archive.save_reference_cache({"AAA": {"ticker": "AAA", "as_of": "2026-08-28"}})
        assert archive.load_reference_cache()["AAA"]["ticker"] == "AAA"

    def test_corrupt_cache_is_rebuilt_rather_than_fatal(self, archive):
        archive.reference_dir.mkdir(parents=True, exist_ok=True)
        (archive.reference_dir / "cache.json").write_text("{not json")
        assert archive.load_reference_cache() == {}


class TestRunLog:
    def test_records_one_line_per_run(self, archive):
        archive.record_run({"trade_date": TRADE_DATE, "status": "collected"})
        assert (archive.root / "runs.jsonl").read_text().count("\n") == 1
