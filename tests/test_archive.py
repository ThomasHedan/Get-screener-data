"""Tests for the per-slot research archive."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_constants import TRADE_DATE
from warrior_screener import archive
from warrior_screener.models import Candidate

CAPTURED_AT = datetime(2026, 8, 28, 9, 35, 12)


def make_candidate(ticker: str = "GAPR", **overrides) -> Candidate:
    defaults = dict(
        open=3.6,
        high=4.8,
        low=3.4,
        close=4.2,
        volume=4_000_000,
        prev_close=3.0,
        gap_pct=20.0,
        change_pct=40.0,
        relative_volume=20.0,
        float_shares=8_000_000,
        market_cap=33_600_000.0,
        primary_exchange="XNAS",
        security_type="CS",
        score=0.81,
        qualification="relaxed",
    )
    defaults.update(overrides)
    return Candidate(ticker=ticker, trade_date=TRADE_DATE, **defaults)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class TestWriteSlot:
    def test_writes_the_documented_columns_in_order(self, tmp_path):
        path = archive.write_slot(
            tmp_path / "t_plus_5.csv",
            [make_candidate()],
            slot="t_plus_5",
            captured_at=CAPTURED_AT,
            in_play={"GAPR"},
            sectors={"GAPR": "Health Technology"},
        )
        with path.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        assert tuple(header) == archive.FIELDNAMES

    def test_carries_the_slot_and_the_real_timestamp(self, tmp_path):
        # Both, deliberately: GitHub's scheduler runs late, so "which
        # measurement was this meant to be" and "when did it actually happen"
        # are different questions the dataset has to answer separately.
        path = archive.write_slot(
            tmp_path / "s.csv",
            [make_candidate()],
            slot="t_plus_5",
            captured_at=CAPTURED_AT,
            in_play=set(),
            sectors={},
        )
        row = read_rows(path)[0]
        assert row["slot"] == "t_plus_5"
        assert row["captured_at"] == "2026-08-28T09:35:12"

    def test_records_gap_and_the_metrics_a_study_needs(self, tmp_path):
        path = archive.write_slot(
            tmp_path / "s.csv",
            [make_candidate()],
            slot="t_plus_10",
            captured_at=CAPTURED_AT,
            in_play={"GAPR"},
            sectors={"GAPR": "Health Technology"},
        )
        row = read_rows(path)[0]
        assert row["gap_pct"] == "20.0"
        assert row["change_pct"] == "40.0"
        assert row["relative_volume"] == "20.0"
        assert row["float_shares"] == "8000000"
        assert row["prev_close"] == "3.0"
        assert row["sector"] == "Health Technology"
        assert row["in_play"] == "true"

    def test_missing_values_are_blank_not_the_string_none(self, tmp_path):
        path = archive.write_slot(
            tmp_path / "s.csv",
            [make_candidate(float_shares=None, relative_volume=None, gap_pct=None)],
            slot="pre_open",
            captured_at=CAPTURED_AT,
            in_play=set(),
            sectors={},
        )
        row = read_rows(path)[0]
        assert row["float_shares"] == ""
        assert row["relative_volume"] == ""
        # Pre-open the open is not yet today's, so a blank gap is the honest
        # value -- "None" would read as a number to half the CSV parsers.
        assert row["gap_pct"] == ""

    def test_a_carried_row_never_reports_a_score(self, tmp_path):
        # The score is a percentile rank inside the live candidate pool. A
        # carried row is not in that pool, so any number here would be a
        # comparison against a set it never competed in.
        path = archive.write_slot(
            tmp_path / "s.csv",
            [make_candidate(qualification=archive.CARRIED, score=0.77)],
            slot="close",
            captured_at=CAPTURED_AT,
            in_play=set(),
            sectors={},
        )
        row = read_rows(path)[0]
        assert row["score"] == ""
        assert row["qualification"] == "carried"
        assert row["in_play"] == "false"

    def test_reject_reasons_are_pipe_joined(self, tmp_path):
        path = archive.write_slot(
            tmp_path / "s.csv",
            [make_candidate(rejected_by=["float", "news_unknown"])],
            slot="close",
            captured_at=CAPTURED_AT,
            in_play=set(),
            sectors={},
        )
        assert read_rows(path)[0]["rejected_by"] == "float|news_unknown"

    def test_creates_missing_parent_directories(self, tmp_path):
        path = archive.write_slot(
            tmp_path / "history" / "2026" / "08" / "28" / "close.csv",
            [make_candidate()],
            slot="close",
            captured_at=CAPTURED_AT,
            in_play=set(),
            sectors={},
        )
        assert path.exists()


class TestTickersSeen:
    def _write(self, directory: Path, name: str, tickers: list[str]) -> None:
        archive.write_slot(
            directory / name,
            [make_candidate(ticker=ticker) for ticker in tickers],
            slot=name.removesuffix(".csv"),
            captured_at=CAPTURED_AT,
            in_play=set(),
            sectors={},
        )

    def test_unions_every_slot_written_so_far(self, tmp_path):
        self._write(tmp_path, "pre_open.csv", ["AAA", "BBB"])
        self._write(tmp_path, "t_plus_5.csv", ["BBB", "CCC"])
        assert archive.tickers_seen(tmp_path) == {"AAA", "BBB", "CCC"}

    def test_a_missing_directory_is_empty_not_an_error(self, tmp_path):
        # The first slot of the day has nothing to carry; that is normal, and
        # must not fail the run.
        assert archive.tickers_seen(tmp_path / "nope") == set()

    def test_ignores_non_csv_files(self, tmp_path):
        self._write(tmp_path, "pre_open.csv", ["AAA"])
        (tmp_path / "notes.txt").write_text("BBB", encoding="utf-8")
        assert archive.tickers_seen(tmp_path) == {"AAA"}

    @pytest.mark.parametrize("content", ["", "ticker\n", "not,a,screener,file\n1,2,3,4\n"])
    def test_survives_a_file_it_cannot_make_sense_of(self, tmp_path, content):
        (tmp_path / "broken.csv").write_text(content, encoding="utf-8")
        assert archive.tickers_seen(tmp_path) == set()
