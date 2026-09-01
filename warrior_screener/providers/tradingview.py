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
*live* session only, so it drives a real-time "what's in play right now"
check (see ``warrior_screener.live_snapshot``), but it cannot serve a past
date -- the archive that ``warrior_screener.collector`` builds still needs
Polygon (or another historical provider).

One more wrinkle, confirmed by comparing the numbers rather than assumed:
``relative_volume`` here is **time-of-day normalized** -- it compares volume
traded so far today against the average volume traded *by this same clock
time* over the past 10 sessions, not full-session volume against a full-
session average. During a live gap-and-go that can read in the thousands
(correctly -- a stock trading 40x its usual volume in the first five minutes
really is that extreme relative to "normal by 9:35am"), and it drifts down
over the day as the denominator catches up. It is not the same number the
archived Polygon-based pipeline computes, and the two should not be compared
directly. Run this near or after the close if you want a figure that
approximates a full-day RVOL.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

SCAN_URL = "https://scanner.tradingview.com/america/scan"
"""Undocumented endpoint backing https://www.tradingview.com/screener/."""

# TradingView reports exchanges by plain name; the rest of this codebase
# (warrior_screener.config.Criteria.allowed_exchanges) uses Polygon's MIC
# codes, since that is the vocabulary the archived pipeline was built around.
# Translate here so a candidate's `primary_exchange` means the same thing
# regardless of which provider produced it -- without this, every TradingView
# candidate fails the exchange filter, silently, because "NASDAQ" != "XNAS".
EXCHANGE_TO_MIC = {"NASDAQ": "XNAS", "NYSE": "XNYS", "AMEX": "XASE"}

# Security types worth screening. Preferred shares, ETFs, closed-end funds,
# fund units and mutual funds are excluded here rather than downstream --
# TradingView's own classification is more reliable than Polygon's
# ticker-suffix heuristic (warrior_screener.scanner._passes_ticker_hygiene),
# so there is no equivalent heuristic to replicate on this path.
TRADEABLE_TYPES = frozenset({"stock", "dr"})  # common stock, depositary receipts

# One page comfortably clears the whole US market (~11k names) as of 2026;
# paging defends against the universe growing past a single request rather
# than relying on that holding forever.
PAGE_SIZE = 8_000
MAX_PAGES = 5

_COLUMNS = (
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

    One call typically covers the whole market; pagination only engages if
    the universe has grown past ``PAGE_SIZE`` since this was written.
    """
    http = session or requests.Session()
    rows: list[MarketSnapshotRow] = []
    total_count: int | None = None
    start = 0

    for _ in range(MAX_PAGES):
        payload = _post_with_retries(http, start, timeout, max_retries)
        total_count = payload.get("totalCount", total_count)
        page = payload.get("data") or []
        rows.extend(_parse_row(entry) for entry in page if entry.get("d"))

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

    tradeable = [row for row in rows if row.security_type in TRADEABLE_TYPES]
    logger.info(
        "TradingView snapshot: %d rows fetched (reported total %s), %d tradeable",
        len(rows),
        total_count,
        len(tradeable),
    )
    return tradeable


def _post_with_retries(
    http: requests.Session, start: int, timeout: float, max_retries: int
) -> dict[str, Any]:
    """POST one page of the scan, retrying transient failures."""
    exchanges = ["AMEX", "NASDAQ", "NYSE"]
    body = {
        "filter": [{"left": "exchange", "operation": "in_range", "right": exchanges}],
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": list(_COLUMNS),
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


def _parse_row(entry: dict[str, Any]) -> MarketSnapshotRow:
    """Convert one ``{"s": "<exchange>:<ticker>", "d": [...]}`` entry.

    Field order in ``d`` matches ``_COLUMNS`` exactly -- TradingView has no
    named-field response mode, so this positional unpack is the contract.
    """
    (
        ticker,
        exchange,
        security_type,
        subtype,
        open_,
        high,
        low,
        close,
        change_pct,
        volume,
        relative_volume,
        average_volume,
        market_cap,
        float_shares,
        sector,
    ) = entry["d"]
    return MarketSnapshotRow(
        ticker=ticker,
        exchange=EXCHANGE_TO_MIC.get(exchange, exchange or ""),
        security_type=security_type or "",
        security_subtype=subtype or "",
        open=float(open_ or 0.0),
        high=float(high or 0.0),
        low=float(low or 0.0),
        close=float(close or 0.0),
        change_pct=float(change_pct or 0.0),
        volume=int(volume or 0),
        relative_volume=_opt_float(relative_volume),
        average_volume=_opt_float(average_volume),
        market_cap=_opt_float(market_cap),
        float_shares=_opt_float(float_shares),
        sector=sector or None,
    )


def _opt_float(value: Any) -> float | None:
    return float(value) if value is not None else None
