"""Tests for configuration loading and precedence."""

from __future__ import annotations

import pytest

from warrior_screener.config import Criteria, Settings, load_settings


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "criteria.yml"
    path.write_text(
        """
provider: polygon
max_enrich: 12
criteria:
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
        settings = load_settings(config_file)
        assert settings.max_enrich == 12
        assert settings.criteria.min_price == 2.0
        assert settings.criteria.max_float_shares == 20_000_000
        assert settings.criteria.allowed_exchanges == ("XNAS",)

    def test_null_is_preserved_as_none(self, config_file):
        assert load_settings(config_file).criteria.max_market_cap is None

    def test_untouched_keys_keep_their_defaults(self, config_file):
        assert load_settings(config_file).criteria.min_relative_volume == 5.0

    def test_missing_explicit_config_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "nope.yml")

    def test_unknown_key_is_rejected_loudly(self, tmp_path):
        path = tmp_path / "bad.yml"
        path.write_text("criteria:\n  min_prices: 2.0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="min_prices"):
            load_settings(path)


class TestPrecedence:
    def test_environment_supplies_the_api_key(self, config_file, monkeypatch):
        monkeypatch.setenv("POLYGON_API_KEY", "from-env")
        assert load_settings(config_file).api_key == "from-env"

    def test_overrides_beat_the_file(self, config_file):
        settings = load_settings(config_file, overrides={"criteria": {"min_price": 5.0}})
        assert settings.criteria.min_price == 5.0
        assert settings.criteria.max_price == 10.0  # still from the file

    def test_none_overrides_are_ignored(self, config_file):
        settings = load_settings(config_file, overrides={"max_enrich": None})
        assert settings.max_enrich == 12


class TestValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"min_price": 0.0},
            {"min_price": 30.0, "max_price": 20.0},
            {"rvol_lookback_days": 0},
            {"max_in_play": 0},
            {"min_in_play": 20, "max_in_play": 10},
            {"max_float_shares": -1},
        ],
    )
    def test_inconsistent_criteria_are_rejected(self, overrides):
        from dataclasses import replace

        with pytest.raises(ValueError):
            replace(Criteria(), **overrides).validate()

    def test_zero_rpm_is_rejected(self):
        from dataclasses import replace

        with pytest.raises(ValueError):
            replace(Settings(), requests_per_minute=0).validate()
