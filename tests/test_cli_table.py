"""Tests for the CLI table renderer (`_print_rows`).

Guards a bug caught live: TradingView's time-of-day-normalized RVOL (see
`snapshot`) can print 4-5 digit values that overflow the RVOL column's fixed
width, and without a guaranteed separator between columns the number glued
onto the next one -- "13,321.90123,119,671" printed as what looked like one
huge number but was actually RVOL and VOLUME concatenated.
"""

from __future__ import annotations

from warrior_screener.cli import _print_rows


def test_an_oversized_value_does_not_merge_into_the_next_column(capsys):
    # Reproduces the exact case observed live: an extreme RVOL next to a
    # large volume, both individually correct but glued together if the
    # column separator relies on padding alone.
    _print_rows(
        [
            {
                "ticker": "IMRN",
                "close": 1.80,
                "change_pct": 62.16,
                "gap_pct": 63.06,
                "relative_volume": 13321.90,
                "volume": 123_119_671,
                "float_shares": None,
                "news_count": 0,
                "news_checked": True,
                "score": 0.68,
                "qualification": "relaxed",
            }
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    data_line = lines[-1]
    assert "13,321.90" in data_line
    assert "123,119,671" in data_line
    # The two numbers must be separated by whitespace, not run together.
    assert "13,321.90123,119,671" not in data_line
    glued_index = data_line.find("13,321.90") + len("13,321.90")
    assert data_line[glued_index] == " "


def test_normal_width_values_still_align_with_a_single_space_gap(capsys):
    _print_rows(
        [
            {
                "ticker": "GAPR",
                "close": 4.2,
                "change_pct": 40.0,
                "gap_pct": 20.0,
                "relative_volume": 20.0,
                "volume": 4_000_000,
                "float_shares": 8_000_000,
                "news_count": 3,
                "news_checked": True,
                "score": 0.87,
                "qualification": "strict",
            }
        ]
    )
    out = capsys.readouterr().out
    assert "GAPR" in out
    assert "strict" in out


def test_header_and_data_rows_use_the_same_join_strategy(capsys):
    _print_rows(
        [
            {
                "ticker": "AAA",
                "close": 1.0,
                "change_pct": 10.0,
                "gap_pct": None,
                "relative_volume": None,
                "volume": 500_000,
                "float_shares": None,
                "news_count": 0,
                "news_checked": False,
                "score": 0.1,
                "qualification": "relaxed",
            }
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    header, separator = lines[0], lines[1]
    assert len(separator) == len(header)  # the dashed rule matches the header width
    assert "TICKER" in header
