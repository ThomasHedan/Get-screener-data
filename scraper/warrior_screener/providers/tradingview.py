"""Free, keyless market-wide snapshot from TradingView's public screener API.

This is not an official, documented API. It is the same JSON endpoint
(``scanner.tradingview.com``) that powers the screener page on
tradingview.com, called the same way that page calls it -- there is no
published contract, no SLA, and no guarantee it keeps working tomorrow. Treat
it as a fast, free path for a live check, not something to depend on for
anything unattended.

What it buys you: **the whole US equity market's relative volume, average
volume, float, market cap and sector, in one HTTP request, with no API key**
-- something no free tier of Polygon, Finnhub or Alpha Vantage offers in a
single call. What it does not buy you: history. The endpoint reflects the
*live* session only and cannot serve a past date, which is the whole reason
this project stores every capture: "what was in play at 09:35" has an answer
only if something recorded it at 09:35.

One more wrinkle, confirmed by comparing the numbers rather than assumed:
``relative_volume`` here is **time-of-day normalized** -- it compares volume
traded so far today against the average volume traded *by this same clock
time* over the past 10 sessions, not full-session volume against a full-
session average. During a live gap-and-go that can read in the thousands
(correctly -- a stock trading 40x its usual volume in the first five minutes
really is that extreme relative to "normal by 9:35am"), and it drifts down
over the day as the denominator catches up. It is not a full-session RVOL and
the two should not be compared directly. Read a capture near the close if you
want a figure that approximates one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

SCAN_URL = "https://scanner.tradingview.com/america/scan"
"""Undocumented endpoint backing https://www.tradingview.com/screener/."""

# TradingView reports exchanges by plain name; this codebase speaks MIC codes
# (see Criteria.allowed_exchanges). Translate at the boundary -- without this
# every candidate fails the exchange filter, silently, because "NASDAQ" is not
# "XNAS".
EXCHANGE_TO_MIC = {"NASDAQ": "XNAS", "NYSE": "XNYS", "AMEX": "XASE"}

# Security types worth screening. Preferred shares, ETFs, closed-end funds,
# fund units and mutual funds are excluded here, at the boundary, using
# TradingView's own classification rather than a ticker-suffix heuristic.
TRADEABLE_TYPES = frozenset({"stock", "dr"})  # common stock, depositary receipts

# TradingView's type vocabulary mapped to the "CS"/"ADRC" codes this codebase
# uses, for the same boundary-translation reason as EXCHANGE_TO_MIC above.
SECURITY_TYPE = {"stock": "CS", "dr": "ADRC"}

# One page comfortably clears the whole US market (~11k names) as of 2026;
# paging defends against the universe growing past a single request rather
# than relying on that holding forever.
PAGE_SIZE = 8_000
MAX_PAGES = 5

# The columns the screen itself needs. These are known-good: they are what this
# project has been fetching successfully all along, so the scan cannot break on
# them. Order is the contract -- the response is positional.
CORE_COLUMNS = (
    "name",
    "exchange",
    "type",
    "subtype",
    "open",
    "high",
    "low",
    "close",
    "change",
    "volume",
    "relative_volume_10d_calc",
    "average_volume_10d_calc",
    "market_cap_basic",
    "float_shares_outstanding_current",
    "sector",
)

# Everything else worth having. The screen does not read any of these -- they
# exist so the stored history is rich enough to train on later, which is a
# different job from selecting today's board.
#
# These names are NOT verified against the live endpoint. TradingView's scanner
# is undocumented and rejects the whole request on an unknown column, so
# fetch_market_snapshot asks for CORE + EXTENDED and silently falls back to CORE
# alone if that is refused (see _fetch_page). A wrong name here therefore costs
# the extra data, never the scan. Run `python -m warrior_screener.probe_columns`
# against the live endpoint to find out which of these are real, then prune.
EXTENDED_COLUMNS = (
    # Identity
    "description",
    "industry",
    "country",
    # Price action beyond the session
    "change_abs",
    "gap",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.Y",
    "Perf.YTD",
    "price_52_week_high",
    "price_52_week_low",
    "High.1M",
    "Low.1M",
    "High.3M",
    "Low.3M",
    # Liquidity
    "Value.Traded",
    "average_volume_30d_calc",
    "average_volume_60d_calc",
    "average_volume_90d_calc",
    # Extended hours -- the pre-open picture the 09:25 capture is taken for
    "premarket_change",
    "premarket_volume",
    "premarket_gap",
    "postmarket_change",
    "postmarket_volume",
    # Share structure
    "total_shares_outstanding_current",
    "float_shares_percent_current",
    # Volatility
    "Volatility.D",
    "Volatility.W",
    "Volatility.M",
    "ATR",
    "beta_1_year",
    # Trend
    "SMA20",
    "SMA50",
    "SMA200",
    "EMA20",
    "EMA50",
    # Oscillators
    "RSI",
    "RSI7",
    "Stoch.K",
    "Stoch.D",
    "MACD.macd",
    "MACD.signal",
    "Recommend.All",
    # Fundamentals -- thin for the microcaps this screen looks at, but free
    "price_earnings_ttm",
    "earnings_per_share_diluted_ttm",
    "total_revenue_yoy_growth_ttm",
    "debt_to_equity",
    "number_of_employees",
    "earnings_release_next_date",
)

_COLUMNS = CORE_COLUMNS + EXTENDED_COLUMNS


@dataclass(frozen=True)
class MarketSnapshotRow:
    """One ticker's live state, as TradingView's screener currently sees it."""

    ticker: str
    exchange: str
    security_type: str
    security_subtype: str
    open: float
    high: float
    low: float
    close: float
    change_pct: float
    volume: int
    relative_volume: float | None
    average_volume: float | None
    market_cap: float | None
    float_shares: float | None
    sector: str | None

    # Every column beyond CORE_COLUMNS, keyed by TradingView's own name. The
    # screen never reads this; it exists so the stored history keeps what the
    # screen happens not to need today.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def prev_close(self) -> float | None:
        """Reconstruct the previous close from today's close and % change.

        TradingView does not return the previous close directly; it returns
        the percentage change, which is all the screen actually needs.
        """
        denominator = 1.0 + self.change_pct / 100.0
        if denominator == 0:
            return None
        return self.close / denominator


class TradingViewError(RuntimeError):
    """The TradingView scanner endpoint could not be reached or parsed."""


def fetch_market_snapshot(
    *,
    session: requests.Session | None = None,
    timeout: float = 20.0,
    max_retries: int = 2,
) -> list[MarketSnapshotRow]:
    """Fetch every US common stock and ADR TradingView currently tracks.

    Asks for CORE + EXTENDED columns and falls back to CORE alone if the
    endpoint refuses. The extended names are unverified by construction -- the
    scanner is undocumented and rejects the whole request on an unknown column
    -- so losing the extra data is an acceptable outcome and losing the scan is
    not.
    """
    http = session or requests.Session()
    try:
        rows = _scan(http, _COLUMNS, timeout, max_retries)
    except TradingViewError as exc:
        logger.warning(
            "Scan with %d columns failed (%s); falling back to the %d core columns. "
            "Run `python -m warrior_screener.probe_columns` to find the bad name.",
            len(_COLUMNS),
            exc,
            len(CORE_COLUMNS),
        )
        rows = _scan(http, CORE_COLUMNS, timeout, max_retries)

    tradeable = [row for row in rows if row.security_type in TRADEABLE_TYPES]
    logger.info(
        "TradingView snapshot: %d rows fetched, %d tradeable, %d columns",
        len(rows),
        len(tradeable),
        len(_COLUMNS),
    )
    return tradeable


def _scan(
    http: requests.Session, columns: tuple[str, ...], timeout: float, max_retries: int
) -> list[MarketSnapshotRow]:
    """Page through the whole market with one fixed column list."""
    rows: list[MarketSnapshotRow] = []
    total_count: int | None = None
    start = 0

    for _ in range(MAX_PAGES):
        payload = _post_with_retries(http, start, timeout, max_retries, columns)
        total_count = payload.get("totalCount", total_count)
        page = payload.get("data") or []
        rows.extend(_parse_row(entry, columns) for entry in page if entry.get("d"))

        start += len(page)
        if not page or (total_count is not None and start >= total_count):
            break
    else:
        logger.warning(
            "Stopped after %d pages with %d/%s rows; the universe may have outgrown MAX_PAGES",
            MAX_PAGES,
            len(rows),
            total_count,
        )
    return rows


def _post_with_retries(
    http: requests.Session,
    start: int,
    timeout: float,
    max_retries: int,
    columns: tuple[str, ...],
) -> dict[str, Any]:
    """POST one page of the scan, retrying transient failures."""
    exchanges = ["AMEX", "NASDAQ", "NYSE"]
    body = {
        "filter": [{"left": "exchange", "operation": "in_range", "right": exchanges}],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": list(columns),
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [start, start + PAGE_SIZE],
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; warrior-screener research tool)",
    }

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = http.post(SCAN_URL, json=body, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            logger.warning("TradingView scan request failed (%s), attempt %d", exc, attempt + 1)
            if attempt < max_retries:
                time.sleep(2.0**attempt)

    raise TradingViewError(f"TradingView scan failed after {max_retries + 1} attempts") from (
        last_error
    )


def _parse_row(entry: dict[str, Any], columns: tuple[str, ...]) -> MarketSnapshotRow:
    """Convert one ``{"s": "<exchange>:<ticker>", "d": [...]}`` entry.

    TradingView has no named-field response mode: ``d`` is positional and
    matches ``columns`` exactly. Zipping the two rather than unpacking a fixed
    tuple is what lets the column list grow without this function knowing.
    """
    values = dict(zip(columns, entry["d"], strict=False))
    return MarketSnapshotRow(
        ticker=values.get("name") or "",
        exchange=EXCHANGE_TO_MIC.get(values.get("exchange"), values.get("exchange") or ""),
        security_type=values.get("type") or "",
        security_subtype=values.get("subtype") or "",
        open=float(values.get("open") or 0.0),
        high=float(values.get("high") or 0.0),
        low=float(values.get("low") or 0.0),
        close=float(values.get("close") or 0.0),
        change_pct=float(values.get("change") or 0.0),
        volume=int(values.get("volume") or 0),
        relative_volume=_opt_float(values.get("relative_volume_10d_calc")),
        average_volume=_opt_float(values.get("average_volume_10d_calc")),
        market_cap=_opt_float(values.get("market_cap_basic")),
        float_shares=_opt_float(values.get("float_shares_outstanding_current")),
        sector=values.get("sector") or None,
        # Drop nulls: a market-wide capture is mostly absent fundamentals, and
        # storing thousands of explicit nulls per row buys nothing.
        extra={k: v for k, v in values.items() if k not in CORE_COLUMNS and v is not None},
    )


def _opt_float(value: Any) -> float | None:
    return float(value) if value is not None else None
