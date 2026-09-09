"""Screener configuration: the Warrior-Trading criteria and runtime settings.

Defaults encode the criteria Ross Cameron publicly describes for his gap-and-go
momentum scans. Every threshold is overridable from ``config/criteria.yml`` so
the strategy research can be re-run under different assumptions without
touching the code.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/criteria.yml")


@dataclass(frozen=True)
class Criteria:
    """Thresholds a ticker must clear to be considered "in play".

    The defaults are the widely published Warrior Trading momentum criteria:
    up 10%+, priced $1-$20, 5x relative volume, float under 10M shares, and a
    news catalyst.
    """

    # --- Price ---
    min_price: float = 1.0
    max_price: float = 20.0

    # --- Momentum ---
    min_change_pct: float = 10.0  # close vs previous close, in percent
    min_gap_pct: float | None = None  # optional open-vs-prev-close filter

    # --- Volume ---
    min_day_volume: int = 500_000
    min_relative_volume: float = 5.0

    # --- Share structure ---
    max_float_shares: int | None = 10_000_000
    max_market_cap: float | None = None

    # --- Catalyst ---
    require_news_catalyst: bool = True
    min_news_articles: int = 1

    # --- Universe hygiene ---
    allowed_security_types: tuple[str, ...] = ("CS", "ADRC")
    allowed_exchanges: tuple[str, ...] = ("XNAS", "XNYS", "XASE", "ARCX", "BATS")

    # --- Selection ---
    max_in_play: int = 10
    min_in_play: int = 5
    fill_to_min: bool = True
    """Backfill the list with relaxed-criteria names when fewer than
    ``min_in_play`` tickers pass every filter. Quiet sessions genuinely do not
    have five stocks with a sub-10M float running 5x volume; those rows are
    tagged ``relaxed`` so research can drop them."""

    # Filters dropped when falling back to relaxed selection.
    relaxed_drop_filters: tuple[str, ...] = (
        "float",
        "news",
        "news_unknown",
        "relative_volume",
    )

    # --- Ranking weights (applied to percentile ranks within the day's pool) ---
    weight_relative_volume: float = 0.40
    weight_change_pct: float = 0.30
    weight_float: float = 0.20  # smaller float ranks higher
    weight_news: float = 0.10

    def validate(self) -> None:
        """Raise ``ValueError`` if the thresholds are internally inconsistent."""
        if self.min_price <= 0 or self.max_price <= self.min_price:
            raise ValueError("Require 0 < min_price < max_price")
        if self.max_in_play < 1:
            raise ValueError("max_in_play must be >= 1")
        if self.min_in_play > self.max_in_play:
            raise ValueError("min_in_play must be <= max_in_play")
        if self.max_float_shares is not None and self.max_float_shares <= 0:
            raise ValueError("max_float_shares must be positive when set")


@dataclass(frozen=True)
class Settings:
    """Runtime settings: where output goes, and the screen itself."""

    data_dir: Path = Path("data")
    request_timeout: float = 30.0

    criteria: Criteria = field(default_factory=Criteria)

    def validate(self) -> None:
        """Raise ``ValueError`` if runtime settings are unusable."""
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        self.criteria.validate()


def _coerce(target_type: Any, value: Any) -> Any:
    """Coerce a YAML scalar/sequence to the dataclass field's declared type."""
    if value is None:
        return None
    type_name = str(target_type)
    if "tuple" in type_name:
        return tuple(value)
    if "Path" in type_name:
        return Path(value)
    if "int" in type_name and "float" not in type_name:
        return int(value)
    if "float" in type_name:
        return float(value)
    if "bool" in type_name:
        return bool(value)
    return value


def _apply_overrides(instance: Any, overrides: dict[str, Any]) -> Any:
    """Return a copy of ``instance`` with known keys from ``overrides`` applied."""
    valid = {f.name: f.type for f in fields(instance)}
    unknown = set(overrides) - set(valid)
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")
    patch = {key: _coerce(valid[key], val) for key, val in overrides.items()}
    return replace(instance, **patch)


def load_settings(
    config_path: Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Build ``Settings`` from a YAML file, environment variables and overrides.

    Precedence, lowest to highest: dataclass defaults, YAML file, environment
    (``SCREENER_DATA_DIR``), then explicit ``overrides`` (which is where CLI
    flags land).
    """
    settings = Settings()
    path = config_path or DEFAULT_CONFIG_PATH

    if path.exists():
        raw = _read_yaml(path)
        criteria_raw = raw.pop("criteria", None) or {}
        settings = _apply_overrides(settings, raw)
        settings = replace(settings, criteria=_apply_overrides(Criteria(), criteria_raw))
        logger.debug("Loaded configuration from %s", path)
    elif config_path is not None:
        raise FileNotFoundError(f"Config file not found: {path}")
    else:
        # Paths are relative to the working directory, so running from outside
        # the repo silently ignores an edited criteria file. Say so rather than
        # screening on defaults the user thinks they changed.
        logger.warning(
            "No %s relative to %s -- screening with built-in defaults. "
            "Run from the repository root, or pass --config.",
            path,
            Path.cwd(),
        )

    env_dir = os.environ.get("SCREENER_DATA_DIR")
    if env_dir:
        settings = replace(settings, data_dir=Path(env_dir))

    if overrides:
        clean = {key: val for key, val in overrides.items() if val is not None}
        criteria_patch = clean.pop("criteria", None) or {}
        settings = _apply_overrides(settings, clean)
        if criteria_patch:
            settings = replace(
                settings, criteria=_apply_overrides(settings.criteria, criteria_patch)
            )

    settings.validate()
    return settings


def _read_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML config file, failing clearly if PyYAML is missing."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            f"Reading {path} requires PyYAML. Install it with `pip install pyyaml`."
        ) from exc

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data
