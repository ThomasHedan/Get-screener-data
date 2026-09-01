"""Tests for the prior-session walk that powers relative volume."""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

from tests.test_constants import IN_PLAY_TICKER, PRIOR_SESSION_VOLUME, TRADE_DATE
from warrior_screener.history import SessionHistory, load_history


class TestRelativeVolume:
    def test_average_over_the_window(self):
        history = SessionHistory(TRADE_DATE, volume_history={"AAA": [100, 200, 300]})
        assert history.average_volume("AAA") == 200
        assert history.relative_volume("AAA", 2_000) == 10.0

    def test_too_few_sessions_is_unknown_not_zero(self):
        # A freshly listed ticker has no meaningful "normal" volume; saying so
        # is safer than inventing a huge RVOL off a two-day average.
        history = SessionHistory(TRADE_DATE, volume_history={"IPO": [100, 200]})
        assert history.average_volume("IPO") is None
        assert history.relative_volume("IPO", 5_000_000) is None

    def test_unknown_ticker_is_unknown(self):
        assert SessionHistory(TRADE_DATE).relative_volume("NOPE", 1_000) is None


class TestLoadHistory:
    def test_loads_the_requested_number_of_sessions(self, provider, archive):
        history = load_history(provider, archive, TRADE_DATE, lookback_days=5)
        assert history.sessions_loaded == 5
        assert history.previous_session == TRADE_DATE - timedelta(days=1)  # Thu 27 Aug 2026
        assert history.average_volume(IN_PLAY_TICKER) == PRIOR_SESSION_VOLUME

    def test_sessions_are_cached_on_disk(self, provider, archive):
        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        calls = provider.calls["grouped"]
        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        assert provider.calls["grouped"] == calls

    def test_weekends_are_never_requested(self, provider, archive):
        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        for cached in archive.root.joinpath("daily_bars").glob("*.csv"):
            assert date.fromisoformat(cached.stem).weekday() < 5

    def test_offline_mode_makes_no_requests(self, provider, archive):
        history = load_history(provider, archive, TRADE_DATE, 5, allow_fetch=False)
        assert provider.calls["grouped"] == 0
        assert history.sessions_loaded == 0

    def test_stale_previous_session_is_refetched_for_split_consistency(self, provider, archive):
        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        calls = provider.calls["grouped"]

        # Age the cached previous session past the refresh threshold. A close
        # cached before a reverse split would otherwise read as a huge gap.
        stale = archive.daily_bars_path(TRADE_DATE - timedelta(days=1))
        old = time.time() - 10 * 86_400
        os.utime(stale, (old, old))

        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        assert provider.calls["grouped"] == calls + 1

    def test_a_fresh_previous_session_is_not_refetched(self, provider, archive):
        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        calls = provider.calls["grouped"]
        load_history(provider, archive, TRADE_DATE, lookback_days=5)
        assert provider.calls["grouped"] == calls

    def test_gives_up_gracefully_when_history_runs_out(self, provider, archive):
        provider.bars_by_date = {TRADE_DATE: provider.bars_by_date[TRADE_DATE]}
        history = load_history(provider, archive, TRADE_DATE, lookback_days=20)
        assert history.sessions_loaded == 0
        assert history.previous_session is None
