"""Polygon.io implementation of :class:`MarketDataProvider`.

Polygon is the default vendor for two reasons that matter for this project:

1. ``/v2/aggs/grouped`` prices the entire US equity universe for one session in
   a single request, so a daily scan is cheap enough to run on the free tier.
2. Its historical endpoints keep serving tickers after they are delisted, which
   is what lets the archive stay free of survivorship bias.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from warrior_screener.models import DailyBar, MinuteBar, TickerReference
from warrior_screener.providers.base import ProviderError, RateLimitError
from warrior_screener.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"
EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Polygon caps aggregate responses at 50k rows; a full 04:00-20:00 session is
# 960 minutes, so one page is always enough for minute bars.
MAX_AGG_LIMIT = 50_000


class PolygonProvider:
    """Rate-limited, retrying HTTP client for the Polygon REST API."""

    name = "polygon"

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_minute: int = 5,
        max_retries: int = 4,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "A Polygon API key is required. Set POLYGON_API_KEY or pass --api-key."
            )
        self._api_key = api_key
        self._limiter = RateLimiter(requests_per_minute)
        self._max_retries = max_retries
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "warrior-screener/0.1",
            }
        )

    # ------------------------------------------------------------------ HTTP

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Polygon endpoint with pacing, retries and typed errors."""
        url = f"{BASE_URL}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            try:
                response = self._session.get(url, timeout=self._timeout)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("Request to %s failed (%s), attempt %d", path, exc, attempt + 1)
                self._backoff(attempt)
                continue

            if response.status_code == 429:
                last_error = RateLimitError(f"429 from Polygon for {path}")
                logger.warning("Rate limited by Polygon on %s, backing off", path)
                self._backoff(attempt, minimum=15.0)
                continue

            if response.status_code in (401, 403):
                raise ProviderError(
                    f"Polygon rejected the request for {path} with HTTP "
                    f"{response.status_code}. Check the API key and that your plan "
                    f"covers this endpoint."
                )

            if response.status_code >= 500:
                last_error = ProviderError(f"HTTP {response.status_code} from {path}")
                self._backoff(attempt)
                continue

            if response.status_code == 404:
                return {}

            if not response.ok:
                raise ProviderError(
                    f"HTTP {response.status_code} from {path}: {response.text[:300]}"
                )

            return response.json()

        raise ProviderError(f"Giving up on {path} after {self._max_retries + 1} attempts") from (
            last_error
        )

    def _backoff(self, attempt: int, *, minimum: float = 1.0) -> None:
        """Sleep for an exponentially growing delay between retries."""
        delay = max(minimum, 2.0**attempt)
        time.sleep(delay)

    # -------------------------------------------------------------- Endpoints

    def grouped_daily(self, trade_date: date) -> list[DailyBar]:
        """Return every US stock's daily bar for ``trade_date`` in one call."""
        payload = self._get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{trade_date.isoformat()}",
            {"adjusted": "true"},
        )
        results = payload.get("results") or []
        bars: list[DailyBar] = []
        for row in results:
            ticker = row.get("T")
            if not ticker or row.get("c") is None:
                continue
            bars.append(
                DailyBar(
                    ticker=ticker,
                    trade_date=trade_date,
                    open=float(row.get("o") or 0.0),
                    high=float(row.get("h") or 0.0),
                    low=float(row.get("l") or 0.0),
                    close=float(row["c"]),
                    volume=int(row.get("v") or 0),
                    vwap=_opt_float(row.get("vw")),
                    trade_count=_opt_int(row.get("n")),
                )
            )
        logger.info("Grouped daily for %s returned %d bars", trade_date, len(bars))
        return bars

    def ticker_details(self, ticker: str, as_of: date | None = None) -> TickerReference | None:
        """Return reference data for ``ticker`` as of a date (default: latest)."""
        params: dict[str, Any] = {}
        if as_of is not None:
            params["date"] = as_of.isoformat()
        payload = self._get(f"/v3/reference/tickers/{ticker}", params)
        result = payload.get("results")
        if not result:
            return None

        # Polygon exposes two share counts; the share-class figure is the closer
        # proxy for tradable float on a single listed class.
        shares = result.get("share_class_shares_outstanding") or result.get(
            "weighted_shares_outstanding"
        )
        return TickerReference(
            ticker=result.get("ticker", ticker),
            name=result.get("name"),
            security_type=result.get("type"),
            primary_exchange=result.get("primary_exchange"),
            shares_outstanding=_opt_int(shares),
            market_cap=_opt_float(result.get("market_cap")),
            is_active=result.get("active"),
            list_date=_parse_date(result.get("list_date")),
            as_of=as_of or date.today(),
        )

    def news(
        self, ticker: str, published_after: datetime, published_before: datetime
    ) -> list[tuple[datetime, str]]:
        """Return headlines published for ``ticker`` inside the given window."""
        payload = self._get(
            "/v2/reference/news",
            {
                "ticker": ticker,
                "published_utc.gte": _to_utc_iso(published_after),
                "published_utc.lt": _to_utc_iso(published_before),
                "order": "desc",
                "limit": 50,
            },
        )
        articles: list[tuple[datetime, str]] = []
        for row in payload.get("results") or []:
            published = _parse_datetime(row.get("published_utc"))
            if published is None:
                continue
            articles.append((published, row.get("title") or ""))
        return articles

    def minute_bars(self, ticker: str, trade_date: date) -> list[MinuteBar]:
        """Return 1-minute bars for ``trade_date`` including extended hours."""
        payload = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/minute/"
            f"{trade_date.isoformat()}/{trade_date.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": MAX_AGG_LIMIT},
        )
        bars: list[MinuteBar] = []
        for row in payload.get("results") or []:
            if row.get("t") is None or row.get("c") is None:
                continue
            timestamp = datetime.fromtimestamp(row["t"] / 1000, tz=UTC).astimezone(EASTERN)
            bars.append(
                MinuteBar(
                    ticker=ticker,
                    timestamp=timestamp,
                    open=float(row.get("o") or 0.0),
                    high=float(row.get("h") or 0.0),
                    low=float(row.get("l") or 0.0),
                    close=float(row["c"]),
                    volume=int(row.get("v") or 0),
                    vwap=_opt_float(row.get("vw")),
                    trade_count=_opt_int(row.get("n")),
                )
            )
        return bars


# --------------------------------------------------------------- Parsing helpers


def _opt_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.debug("Unparseable date from provider: %r", value)
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text).astimezone(EASTERN)
    except ValueError:
        logger.debug("Unparseable timestamp from provider: %r", value)
        return None


def _to_utc_iso(moment: datetime) -> str:
    """Format a datetime as the UTC ISO-8601 string Polygon expects."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=EASTERN)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def eastern_session_window(trade_date: date) -> tuple[datetime, datetime]:
    """Return the catalyst window: previous day's close through this day's close.

    News that moves a gapper lands overnight or pre-market, so the window that
    matters starts at the prior session's 16:00 ET close.
    """
    end = datetime.combine(trade_date, datetime.min.time(), tzinfo=EASTERN) + timedelta(hours=16)
    return end - timedelta(days=1), end
