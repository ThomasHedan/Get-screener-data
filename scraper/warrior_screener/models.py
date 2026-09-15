"""The record the scanner passes around: one ticker under evaluation.

A plain dataclass rather than a pydantic model -- every instance is built
inside this package from data already parsed at the provider boundary, so
there is nothing left to re-validate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


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
    sector: str | None = None
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
