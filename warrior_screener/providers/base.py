"""Provider-agnostic interface for the market data the screener needs.

Keeping the scanner behind this protocol means the Warrior criteria and the
storage layer are testable without a network call, and a different vendor can
be swapped in by implementing four methods.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from warrior_screener.models import DailyBar, MinuteBar, TickerReference


class ProviderError(RuntimeError):
    """A market data request failed in a way the caller cannot retry away."""


class RateLimitError(ProviderError):
    """The provider rejected the request for exceeding its rate limit."""


@runtime_checkable
class MarketDataProvider(Protocol):
    """The minimum surface the screener needs from a market data vendor."""

    name: str

    def grouped_daily(self, trade_date: date) -> list[DailyBar]:
        """Return one daily bar per ticker for ``trade_date``.

        Returns an empty list when the market was closed. This is the workhorse
        call: it prices the entire US equity universe for one session in a
        single request, so the daily scan costs one API call plus enrichment.
        """
        ...

    def ticker_details(self, ticker: str, as_of: date | None = None) -> TickerReference | None:
        """Return reference data for ``ticker``, or ``None`` if it is unknown."""
        ...

    def news(
        self, ticker: str, published_after: datetime, published_before: datetime
    ) -> list[tuple[datetime, str]]:
        """Return ``(published_at, headline)`` pairs in the given window."""
        ...

    def minute_bars(self, ticker: str, trade_date: date) -> list[MinuteBar]:
        """Return 1-minute bars for ``trade_date``, including extended hours."""
        ...
