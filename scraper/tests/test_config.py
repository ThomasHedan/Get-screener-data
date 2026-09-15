"""Tests for loading the screen from YAML."""

from __future__ import annotations

from dataclasses import replace

import pytest

from warrior_screener.config import Criteria, load_criteria


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "criteria.yml"
    path.write_text(
        """
min_price: 2.0
max_price: 10.0
max_float_shares: 20000000
max_market_cap: null
allowed_exchanges: ["XNAS"]
""",
        encoding="utf-8",
    )
    return path


class TestDefaults:
    def test_defaults_match_the_published_warrior_criteria(self):
        criteria = Criteria()
        assert criteria.min_price == 1.0
        assert criteria.max_price == 20.0
        assert criteria.min_change_pct == 10.0
        assert criteria.min_relative_volume == 5.0
        assert criteria.max_float_shares == 10_000_000
        assert criteria.require_news_catalyst is True
        assert (criteria.min_in_play, criteria.max_in_play) == (5, 10)


class TestLoading:
    def test_reads_yaml(self, config_file):
        criteria = load_criteria(config_file)
        assert criteria.min_price == 2.0
        assert criteria.max_float_shares == 20_000_000
        assert criteria.allowed_exchanges == ("XNAS",)

    def test_null_is_preserved_as_none(self, config_file):
        assert load_criteria(config_file).max_market_cap is None

    def test_untouched_keys_keep_their_defaults(self, config_file):
        assert load_criteria(config_file).min_relative_volume == 5.0

    def test_missing_default_config_warns_instead_of_failing_silently(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.chdir(tmp_path)  # no config/criteria.yml here
        with caplog.at_level("WARNING"):
            criteria = load_criteria()
        assert criteria.min_price == 1.0  # fell back to defaults
        assert "built-in defaults" in caplog.text

    def test_missing_explicit_config_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_criteria(tmp_path / "nope.yml")

    def test_unknown_key_is_rejected_loudly(self, tmp_path):
        # A silently ignored typo would leave the screen on a default the user
        # believes they changed -- the failure mode this check exists for.
        path = tmp_path / "bad.yml"
        path.write_text("min_prices: 2.0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="min_prices"):
            load_criteria(path)

    def test_the_shipped_criteria_file_loads(self):
        # Guards the file the deployment actually reads, not just a fixture.
        assert load_criteria().min_gap_pct == 0.0


class TestValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"min_price": 0.0},
            {"min_price": 30.0, "max_price": 20.0},
            {"max_in_play": 0},
            {"min_in_play": 20, "max_in_play": 10},
            {"max_float_shares": -1},
        ],
    )
    def test_inconsistent_criteria_are_rejected(self, overrides):
        with pytest.raises(ValueError):
            replace(Criteria(), **overrides).validate()
