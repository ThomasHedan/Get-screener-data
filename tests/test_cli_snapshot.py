"""Tests for the `snapshot` CLI subcommand, against a faked TradingView layer."""

from __future__ import annotations

import json

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

    def test_writes_nothing_unless_asked(self, cli_env, tmp_path):
        cli.main(cli_env + ["snapshot"])
        assert not (tmp_path / "data").exists()

    def test_json_flag_writes_the_dashboard_payload(self, cli_env, tmp_path):
        out = tmp_path / "board.json"
        assert cli.main(cli_env + ["snapshot", "--slot", "t_plus_10", "--json", str(out)]) == 0
        payload = json.loads(out.read_text())
        board = payload["slots"]["t_plus_10"]
        assert [row["ticker"] for row in board["in_play"]] == ["GAPR"]
        assert payload["trade_date"] and board["generated_at"]
        assert payload["criteria"]["min_change_pct"] == 10.0
        assert board["stats"]["universe_rows"] == 2

    def test_json_carries_the_staleness_notice_for_the_page(self, cli_env, tmp_path, monkeypatch):
        # The dashboard refreshes pre-market, so the caveat has to survive the
        # trip into the JSON -- a page that cannot tell live data from
        # yesterday's leftovers is worse than no page.
        monkeypatch.setattr(
            "warrior_screener.market_calendar.session_phase", lambda *a: "pre-market"
        )
        out = tmp_path / "board.json"
        cli.main(cli_env + ["snapshot", "--json", str(out)])
        payload = json.loads(out.read_text())["slots"]["manual"]
        assert payload["session_phase"] == "pre-market"
        assert "may still show yesterday's numbers" in payload["notice"]

    def test_regular_session_json_has_no_notice(self, cli_env, tmp_path, monkeypatch):
        monkeypatch.setattr("warrior_screener.market_calendar.session_phase", lambda *a: "regular")
        out = tmp_path / "board.json"
        cli.main(cli_env + ["snapshot", "--json", str(out)])
        assert json.loads(out.read_text())["slots"]["manual"]["notice"] is None

    def test_quiet_writes_json_without_printing_the_table(self, cli_env, tmp_path, capsys):
        out = tmp_path / "board.json"
        cli.main(cli_env + ["snapshot", "--quiet", "--json", str(out)])
        assert "GAPR" not in capsys.readouterr().out
        assert json.loads(out.read_text())["slots"]["manual"]["in_play"]

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


class TestArchiveSlots:
    """The research archive: one CSV per slot, tickers carried across the day."""

    def test_archive_writes_the_slot_candidates(self, cli_env, tmp_path):
        out = tmp_path / "t_plus_10.csv"
        assert cli.main(cli_env + ["snapshot", "--slot", "t_plus_10", "--archive", str(out)]) == 0
        rows = _read_csv(out)
        assert [row["ticker"] for row in rows] == ["GAPR"]
        assert rows[0]["slot"] == "t_plus_10"
        assert rows[0]["in_play"] == "true"

    def test_a_faded_runner_is_carried_into_the_later_slot(self, cli_env, tmp_path, monkeypatch):
        """The reason carry-forward exists.

        GAPR runs at 09:35 and is archived. By the close it is up 2% on normal
        volume, so it fails the coarse gate -- and without carrying it, the
        closing file would simply not mention it. Morning features with no
        closing row cannot become labels.
        """
        day = tmp_path / "2026-08-28"
        assert (
            cli.main(cli_env + ["snapshot", "--slot", "t_plus_5", "--archive", str(day / "a.csv")])
            == 0
        )

        faded = [make_row("GAPR", change_pct=2.0, relative_volume=1.1), make_row("QUIET")]
        monkeypatch.setattr("warrior_screener.live_snapshot.fetch_market_snapshot", lambda: faded)
        assert (
            cli.main(
                cli_env
                + [
                    "snapshot",
                    "--slot",
                    "close",
                    "--archive",
                    str(day / "close.csv"),
                    "--carry-from",
                    str(day),
                ]
            )
            == 0
        )

        closing = {row["ticker"]: row for row in _read_csv(day / "close.csv")}
        assert "GAPR" in closing, "the morning's runner must still have a closing row"
        assert closing["GAPR"]["qualification"] == "carried"
        assert closing["GAPR"]["change_pct"] == "2.0"
        # Carried, so outside the live pool that the score ranks against.
        assert closing["GAPR"]["score"] == ""
        assert closing["GAPR"]["in_play"] == "false"

    def test_carrying_does_not_change_the_board(self, cli_env, tmp_path):
        """A carried ticker must never re-enter the dashboard's selection."""
        day = tmp_path / "day"
        cli.main(cli_env + ["snapshot", "--slot", "t_plus_5", "--archive", str(day / "a.csv")])
        board = tmp_path / "today.json"
        cli.main(
            cli_env
            + [
                "snapshot",
                "--slot",
                "close",
                "--archive",
                str(day / "close.csv"),
                "--carry-from",
                str(day),
                "--json",
                str(board),
            ]
        )
        rows = json.loads(board.read_text())["slots"]["close"]["in_play"]
        assert [row["ticker"] for row in rows] == ["GAPR"]
        assert all(row["qualification"] != "carried" for row in rows)

    def test_no_archive_flag_writes_no_csv(self, cli_env, tmp_path):
        cli.main(cli_env + ["snapshot"])
        assert not list(tmp_path.glob("**/*.csv"))


def _read_csv(path):
    import csv

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class TestBoardSlots:
    """The page carries the day's slots side by side, not just the latest."""

    def test_a_second_slot_does_not_erase_the_first(self, cli_env, tmp_path):
        out = tmp_path / "board.json"
        cli.main(cli_env + ["snapshot", "--slot", "pre_open", "--json", str(out)])
        cli.main(cli_env + ["snapshot", "--slot", "t_plus_10", "--json", str(out)])
        slots = json.loads(out.read_text())["slots"]
        assert sorted(slots) == ["pre_open", "t_plus_10"]

    def test_re_running_a_slot_replaces_only_that_slot(self, cli_env, tmp_path, monkeypatch):
        out = tmp_path / "board.json"
        cli.main(cli_env + ["snapshot", "--slot", "pre_open", "--json", str(out)])
        cli.main(cli_env + ["snapshot", "--slot", "t_plus_10", "--json", str(out)])

        monkeypatch.setattr(
            "warrior_screener.live_snapshot.fetch_market_snapshot",
            lambda: [make_row("LATER", change_pct=55.0)],
        )
        cli.main(cli_env + ["snapshot", "--slot", "t_plus_10", "--json", str(out)])

        slots = json.loads(out.read_text())["slots"]
        assert [r["ticker"] for r in slots["pre_open"]["in_play"]] == ["GAPR"]
        assert [r["ticker"] for r in slots["t_plus_10"]["in_play"]] == ["LATER"]

    def test_a_new_session_replaces_the_file_rather_than_merging(self, cli_env, tmp_path):
        # Yesterday's pre-open board sitting in a tab beside today's would be
        # indistinguishable from a live one.
        out = tmp_path / "board.json"
        out.write_text(
            json.dumps({"trade_date": "1999-01-04", "slots": {"close": {"in_play": []}}}),
            encoding="utf-8",
        )
        cli.main(cli_env + ["snapshot", "--slot", "pre_open", "--json", str(out)])
        payload = json.loads(out.read_text())
        assert sorted(payload["slots"]) == ["pre_open"]
        assert payload["trade_date"] != "1999-01-04"

    def test_a_corrupt_payload_does_not_take_the_next_run_down(self, cli_env, tmp_path):
        out = tmp_path / "board.json"
        out.write_text("{ this is not json", encoding="utf-8")
        assert cli.main(cli_env + ["snapshot", "--slot", "t_plus_5", "--json", str(out)]) == 0
        assert json.loads(out.read_text())["slots"]["t_plus_5"]["in_play"]
