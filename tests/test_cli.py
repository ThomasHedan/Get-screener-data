"""Tests for the command line surface."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.test_constants import IN_PLAY_TICKER, TRADE_DATE
from warrior_screener import cli


@pytest.fixture
def cli_env(tmp_path, provider, monkeypatch):
    """Point the CLI at a temp archive and the fake provider."""
    config = tmp_path / "criteria.yml"
    config.write_text("criteria:\n  min_price: 1.0\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_make_provider", lambda settings: provider)
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    return ["--config", str(config), "--data-dir", str(tmp_path / "data")]


class TestDateParsing:
    def test_iso(self):
        assert cli._resolve_date("2026-08-28") == TRADE_DATE

    def test_keywords(self):
        assert cli._resolve_date("today") == date.today()
        assert cli._resolve_date("yesterday") == date.today() - timedelta(days=1)

    def test_garbage_is_a_clear_error(self):
        with pytest.raises(SystemExit, match="Invalid date"):
            cli._resolve_date("last tuesday")


class TestCriteriaFlags:
    def _overrides(self, argv):
        return cli._criteria_overrides(cli.build_parser().parse_args(argv))

    def test_thresholds_map_to_criteria_fields(self):
        overrides = self._overrides(["rescan", "--min-change", "20", "--min-rvol", "8"])
        assert overrides == {"min_change_pct": 20.0, "min_relative_volume": 8.0}

    def test_zero_max_float_disables_the_filter(self):
        assert self._overrides(["rescan", "--max-float", "0"])["max_float_shares"] is None

    def test_no_news_flag(self):
        assert self._overrides(["rescan", "--no-news"])["require_news_catalyst"] is False

    def test_unset_flags_produce_no_overrides(self):
        assert self._overrides(["rescan"]) == {}


class TestCommands:
    def test_collect_then_show(self, cli_env, capsys):
        assert cli.main(cli_env + ["collect", "--date", TRADE_DATE.isoformat()]) == 0
        capsys.readouterr()

        assert cli.main(cli_env + ["show", "--date", TRADE_DATE.isoformat()]) == 0
        assert IN_PLAY_TICKER in capsys.readouterr().out

    def test_show_without_data_reports_it(self, cli_env, capsys):
        assert cli.main(cli_env + ["show", "--date", "2026-01-05"]) == 1
        assert "No archived in-play list" in capsys.readouterr().out

    def test_status_on_an_empty_archive(self, cli_env, capsys):
        assert cli.main(cli_env + ["status"]) == 0
        assert "empty" in capsys.readouterr().out

    def test_status_after_collection(self, cli_env, capsys):
        cli.main(cli_env + ["collect", "--date", TRADE_DATE.isoformat()])
        capsys.readouterr()
        assert cli.main(cli_env + ["status"]) == 0
        assert "Sessions collected: 1" in capsys.readouterr().out

    def test_rescan_is_offline_and_read_only(self, cli_env, capsys, provider):
        cli.main(cli_env + ["collect", "--date", TRADE_DATE.isoformat()])
        capsys.readouterr()
        calls_before = dict(provider.calls)

        assert cli.main(cli_env + ["rescan", "--date", TRADE_DATE.isoformat()]) == 0
        out = capsys.readouterr().out
        assert IN_PLAY_TICKER in out
        assert "nothing was written" in out
        assert provider.calls == calls_before

    def test_rescan_reuses_archived_news_so_the_catalyst_still_counts(self, cli_env, capsys):
        cli.main(cli_env + ["collect", "--date", TRADE_DATE.isoformat()])
        capsys.readouterr()
        assert cli.main(cli_env + ["rescan", "--date", TRADE_DATE.isoformat()]) == 0
        # Still tagged strict: without the recovered news counts it would have
        # been downgraded to a relaxed pick.
        assert "strict" in capsys.readouterr().out

    def test_rescan_without_cached_bars_explains_itself(self, cli_env, capsys):
        assert cli.main(cli_env + ["rescan", "--date", "2026-01-05"]) == 1
        assert "collect --date" in capsys.readouterr().out

    def test_rescan_honours_loosened_criteria(self, cli_env, capsys):
        cli.main(cli_env + ["collect", "--date", TRADE_DATE.isoformat()])
        capsys.readouterr()
        cli.main(cli_env + ["rescan", "--date", TRADE_DATE.isoformat(), "--max-float", "0"])
        # With the float filter off, the 300M-float name qualifies too.
        assert "BIGF" in capsys.readouterr().out

    def test_backfill_reports_a_summary(self, cli_env, capsys):
        exit_code = cli.main(cli_env + ["backfill", "--start", "2026-08-27", "--end", "2026-08-28"])
        assert exit_code == 0
        assert "2 sessions collected" in capsys.readouterr().out
