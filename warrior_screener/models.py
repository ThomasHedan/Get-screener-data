"""Typed records passed between the provider, scanner and storage layers.

These are plain frozen dataclasses rather than pydantic models: every instance
is built inside this package from data that has already been parsed at the
provider boundary, so there is nothing left to re-validate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class DailyBar:
    """One session of OHLCV for one ticker."""

    ticker: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None
    trade_count: int | None = None


@dataclass(frozen=True)
class MinuteBar:
    """One minute of OHLCV, including extended-hours minutes."""

    ticker: str
    timestamp: datetime  # timezone-aware, US/Eastern
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None
    trade_count: int | None = None


@dataclass(frozen=True)
class TickerReference:
    """Slow-moving reference data for a ticker.

    ``shares_outstanding`` is the best public proxy the market-data providers
    expose for free float; see ``docs`` in the README for why it is only a
    proxy and how to override it with a real float file.
    """

    ticker: str
    name: str | None = None
    security_type: str | None = None  # e.g. "CS" (common stock), "ADRC", "ETF"
    primary_exchange: str | None = None
    shares_outstanding: int | None = None
    market_cap: float | None = None
    is_active: bool | None = None
    list_date: date | None = None
    as_of: date | None = None


@dataclass
class Candidate:
    """A ticker under evaluation for a given session, with its scan metrics.

    Mutable by design: the scanner builds it from the daily bar and then fills
    in the fields that cost extra API calls (reference data, news) only for the
    names that survive the cheap filters.
    """

    ticker: str
    trade_date: date

    # Price action
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    prev_close: float | None = None
    gap_pct: float | None = None
    change_pct: float | None = None
    range_pct: float | None = None

    # Volume context
    avg_volume: float | None = None
    relative_volume: float | None = None
    dollar_volume: float | None = None

    # Reference / catalyst
    security_type: str | None = None
    primary_exchange: str | None = None
    shares_outstanding: int | None = None
    float_shares: int | None = None
    market_cap: float | None = None
    news_count: int = 0
    news_headline: str | None = None
    news_checked: bool = False
    """False means the catalyst lookup was skipped (the candidate was already
    rejected on structure, or the scan ran offline) -- not that there was no
    news. Research must not read ``news_count == 0`` as "no catalyst" unless
    this is True."""

    # Verdict
    score: float = 0.0
    qualification: str = "rejected"  # "strict" | "relaxed" | "rejected"
    rejected_by: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        """Flatten to a CSV-writable row (lists collapsed to a `|`-joined string)."""
        row = asdict(self)
        row["trade_date"] = self.trade_date.isoformat()
        row["rejected_by"] = "|".join(self.rejected_by)
        return row
