"""Fixtures and factories for the screener tests.

The fake provider makes the whole pipeline -- history, screen, enrichment,
intraday collection, storage -- runnable with no network and no API key.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.test_constants import (
    BIG_FLOAT_TICKER,
    DEFAULT_PREV_CLOSE,
    EXPENSIVE_TICKER,
    IN_PLAY_TICKER,
    LOW_RVOL_TICKER,
    PRIOR_SESSION_COUNT,
    PRIOR_SESSION_VOLUME,
    TRADE_DATE,
    WARRANT_TICKER,
)
from warrior_screener.config import Criteria, Settings
from warrior_screener.history import SessionHistory
from warrior_screener.models import DailyBar, MinuteBar, TickerReference
from warrior_screener.storage import Archive

EASTERN = ZoneInfo("America/New_York")


def make_daily_bar(ticker: str, trade_date: date = TRADE_DATE, **overrides) -> DailyBar:
    """Build a daily bar, defaulting to a $4.20 close on 4M shares."""
    defaults = {
        "open": 3.60,
        "high": 4.80,
        "low": 3.40,
        "close": 4.20,
        "volume": 4_000_000,
        "vwap": 4.10,
        "trade_count": 21_000,
    }
    defaults.update(overrides)
    return DailyBar(ticker=ticker, trade_date=trade_date, **defaults)


def make_minute_bars(
    ticker: str, trade_date: date = TRADE_DATE, *, count: int = 400
) -> list[MinuteBar]:
    """Build a plausible session: pre-market ramp, 09:30 open, high at 09:45."""
    start = datetime.combine(trade_date, datetime.min.time(), tzinfo=EASTERN) + timedelta(hours=4)
    bars = []
    for index in range(count):
        timestamp = start + timedelta(minutes=index)
        # Peak 345 minutes in (09:45 ET), then fade.
        distance = abs(index - 345)
        price = 4.80 - distance * 0.002
        volume = 200_000 if index >= 330 else 20_000
        bars.append(
            MinuteBar(
                ticker=ticker,
                timestamp=timestamp,
                open=price,
                high=price + 0.05,
                low=price - 0.05,
                close=price,
                volume=volume,
                vwap=price,
                trade_count=max(volume // 100, 1),
            )
        )
    return bars


class FakeProvider:
    """In-memory :class:`MarketDataProvider` for tests.

    Counts calls so the tests can assert that the screen keeps its API usage
    bounded, which is the whole point of the two-pass design.
    """

    name = "fake"

    def __init__(
        self,
        bars_by_date: dict[date, list[DailyBar]],
        references: dict[str, TickerReference],
        news: dict[str, int],
    ) -> None:
        self.bars_by_date = bars_by_date
        self.references = references
        self.news_counts = news
        self.calls: dict[str, int] = {"grouped": 0, "reference": 0, "news": 0, "minute": 0}

    def grouped_daily(self, trade_date: date) -> list[DailyBar]:
        self.calls["grouped"] += 1
        return list(self.bars_by_date.get(trade_date, []))

    def ticker_details(self, ticker: str, as_of: date | None = None) -> TickerReference | None:
        self.calls["reference"] += 1
        return self.references.get(ticker)

    def news(self, ticker, published_after, published_before):
        self.calls["news"] += 1
        count = self.news_counts.get(ticker, 0)
        return [
            (published_after + timedelta(hours=1), f"{ticker} headline {index}")
            for index in range(count)
        ]

    def minute_bars(self, ticker: str, trade_date: date) -> list[MinuteBar]:
        self.calls["minute"] += 1
        return make_minute_bars(ticker, trade_date)


@pytest.fixture
def criteria() -> Criteria:
    """Default Warrior criteria, with the min_in_play padding switched off."""
    return replace(Criteria(), fill_to_min=False)


@pytest.fixture
def archive(tmp_path) -> Archive:
    return Archive(tmp_path / "data")


@pytest.fixture
def settings(tmp_path, criteria) -> Settings:
    return Settings(api_key="test-key", data_dir=tmp_path / "data", criteria=criteria)


@pytest.fixture
def scan_bars() -> list[DailyBar]:
    """The day's full-market bars: one clean setup plus assorted rejects."""
    return [
        make_daily_bar(IN_PLAY_TICKER),
        make_daily_bar(BIG_FLOAT_TICKER),
        make_daily_bar(LOW_RVOL_TICKER, volume=600_000),
        make_daily_bar(EXPENSIVE_TICKER, close=48.0, open=44.0, high=50.0, low=43.0),
        make_daily_bar(WARRANT_TICKER),
        make_daily_bar("FLAT", close=3.05),  # up 1.7%, not a gapper
        make_daily_bar("THIN", volume=1_000),  # no liquidity
    ]


@pytest.fixture
def references() -> dict[str, TickerReference]:
    def ref(ticker: str, shares: int, security_type: str = "CS") -> TickerReference:
        return TickerReference(
            ticker=ticker,
            name=f"{ticker} Inc",
            security_type=security_type,
            primary_exchange="XNAS",
            shares_outstanding=shares,
            market_cap=shares * 4.2,
            is_active=True,
            list_date=date(2021, 5, 4),
        )

    return {
        IN_PLAY_TICKER: ref(IN_PLAY_TICKER, 8_000_000),
        BIG_FLOAT_TICKER: ref(BIG_FLOAT_TICKER, 300_000_000),
        LOW_RVOL_TICKER: ref(LOW_RVOL_TICKER, 5_000_000),
        EXPENSIVE_TICKER: ref(EXPENSIVE_TICKER, 9_000_000),
        WARRANT_TICKER: ref(WARRANT_TICKER, 2_000_000, security_type="WARRANT"),
        "FLAT": ref("FLAT", 4_000_000),
        "THIN": ref("THIN", 4_000_000),
    }


@pytest.fixture
def history(scan_bars) -> SessionHistory:
    """Prior-session context: every ticker closed at $3.00 on 200k shares."""
    tickers = [bar.ticker for bar in scan_bars]
    return SessionHistory(
        trade_date=TRADE_DATE,
        previous_session=TRADE_DATE - timedelta(days=1),
        prev_close={ticker: DEFAULT_PREV_CLOSE for ticker in tickers} | {EXPENSIVE_TICKER: 40.0},
        volume_history={ticker: [PRIOR_SESSION_VOLUME] * PRIOR_SESSION_COUNT for ticker in tickers}
        # 600k on a 500k average is only 1.2x -- fails the 5x RVOL test.
        | {LOW_RVOL_TICKER: [500_000] * PRIOR_SESSION_COUNT},
        sessions_loaded=PRIOR_SESSION_COUNT,
    )


@pytest.fixture
def news_counts() -> dict[str, int]:
    return {IN_PLAY_TICKER: 3, BIG_FLOAT_TICKER: 2, LOW_RVOL_TICKER: 0, EXPENSIVE_TICKER: 1}


@pytest.fixture
def provider(scan_bars, references, news_counts) -> FakeProvider:
    """A provider serving the scan date plus 25 identical prior sessions."""
    bars_by_date: dict[date, list[DailyBar]] = {TRADE_DATE: scan_bars}
    cursor = TRADE_DATE - timedelta(days=1)
    while len(bars_by_date) <= 40:
        if cursor.weekday() < 5:
            bars_by_date[cursor] = [
                make_daily_bar(
                    bar.ticker,
                    cursor,
                    close=DEFAULT_PREV_CLOSE,
                    open=DEFAULT_PREV_CLOSE,
                    high=DEFAULT_PREV_CLOSE,
                    low=DEFAULT_PREV_CLOSE,
                    volume=PRIOR_SESSION_VOLUME,
                )
                for bar in scan_bars
            ]
        cursor -= timedelta(days=1)
    return FakeProvider(bars_by_date, references, news_counts)
