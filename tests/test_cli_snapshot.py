"""Tests for the `snapshot` CLI subcommand, against a faked TradingView layer."""

from __future__ import annotations

import csv

import pytest

from tests.test_live_snapshot import make_row
from warrior_screener import cli
from warrior_screener.providers.tradingview import TradingViewError


@pytest.fixture
def fake_rows():
    return [make_row("GAPR"), make_row("QUIET", change_pct=1.0)]


@pytest.fixture
def cli_env(tmp_path, fake_rows, monkeypatch):
    monkeypatch.setattr("warrior_screener.live_snapshot.fetch_market_snapshot", lambda: fake_rows)
    config = tmp_path / "criteria.yml"
    config.write_text("criteria:\n  min_price: 1.0\n", encoding="utf-8")
    return ["--config", str(config), "--data-dir", str(tmp_path / "data")]


class TestSnapshotCommand:
    def test_prints_the_in_play_table_and_requires_no_api_key(self, cli_env, capsys, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        assert cli.main(cli_env + ["snapshot"]) == 0
        out = capsys.readouterr().out
        assert "GAPR" in out
        assert "QUIET" not in out  # only 1% change, never clears min_change_pct

    def test_does_not_write_to_the_archive_by_default(self, cli_env, tmp_path):
        cli.main(cli_env + ["snapshot"])
        assert not (tmp_path / "data" / "live_snapshots").exists()
        assert not (tmp_path / "data" / "scans").exists()

    def test_save_writes_a_timestamped_file_outside_scans(self, cli_env, tmp_path):
        assert cli.main(cli_env + ["snapshot", "--save"]) == 0
        saved_dir = tmp_path / "data" / "live_snapshots"
        files = list(saved_dir.glob("*.csv"))
        assert len(files) == 1
        with files[0].open() as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0]["ticker"] == "GAPR"

    def test_no_news_flag_reaches_the_shared_criteria(self, cli_env, capsys):
        cli.main(cli_env + ["snapshot", "--no-news"])
        assert "strict" in capsys.readouterr().out

    def test_a_provider_failure_is_a_clean_exit_not_a_traceback(self, cli_env, monkeypatch):
        def explode():
            raise TradingViewError("scanner unreachable")

        monkeypatch.setattr("warrior_screener.live_snapshot.fetch_market_snapshot", explode)
        assert cli.main(cli_env + ["snapshot"]) == 1

    def test_criteria_overrides_apply(self, cli_env, capsys):
        cli.main(cli_env + ["snapshot", "--min-change", "50"])
        # GAPR is only +40%, so raising the bar to 50% should drop it too.
        assert "GAPR" not in capsys.readouterr().out


class TestSessionPhaseNotice:
    """Regression coverage for two cases caught live in a row:

    1. 2026-09-07 (Labor Day): `snapshot` silently presented the prior
       session's numbers with nothing on screen to say they were not today's.
    2. 2026-09-08, 04:52 ET (the next trading day): the holiday check alone
       passed -- it *was* a trading day -- but pre-market had barely started,
       so lower-volume names still showed the exact same stale numbers with,
       again, no on-screen indication.

    `_cmd_snapshot` calls `market_calendar.session_phase` via a local import,
    so that is what these patch -- patching `cli.date` (the old mechanism)
    no longer has any effect on it.
    """

    def test_closed_market_prints_a_warning(self, cli_env, capsys, monkeypatch):
        monkeypatch.setattr("warrior_screener.market_calendar.session_phase", lambda *a: "closed")
        cli.main(cli_env + ["snapshot"])
        assert "Market closed" in capsys.readouterr().out

    def test_premarket_prints_the_not_yet_traded_caveat(self, cli_env, capsys, monkeypatch):
        monkeypatch.setattr(
            "warrior_screener.market_calendar.session_phase", lambda *a: "pre-market"
        )
        cli.main(cli_env + ["snapshot"])
        out = capsys.readouterr().out
        assert "Pre-market" in out
        assert "may still show yesterday's numbers" in out

    def test_after_hours_prints_a_neutral_note(self, cli_env, capsys, monkeypatch):
        monkeypatch.setattr(
            "warrior_screener.market_calendar.session_phase", lambda *a: "after-hours"
        )
        cli.main(cli_env + ["snapshot"])
        assert "After-hours" in capsys.readouterr().out

    def test_regular_session_prints_no_caveat_at_all(self, cli_env, capsys, monkeypatch):
        monkeypatch.setattr("warrior_screener.market_calendar.session_phase", lambda *a: "regular")
        cli.main(cli_env + ["snapshot"])
        out = capsys.readouterr().out
        for phrase in ("Market closed", "Pre-market", "After-hours"):
            assert phrase not in out
