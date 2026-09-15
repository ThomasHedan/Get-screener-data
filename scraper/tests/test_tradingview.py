"""Tests for the TradingView snapshot provider, against a faked HTTP layer."""

from __future__ import annotations

import pytest
import requests

from warrior_screener.providers.tradingview import (
    MarketSnapshotRow,
    TradingViewError,
    fetch_market_snapshot,
)

COLUMN_COUNT = 15  # must match _COLUMNS in warrior_screener.providers.tradingview


def _row(ticker: str, exchange: str = "NASDAQ", security_type: str = "stock", **overrides) -> list:
    """Build one TradingView-shaped data row in column order."""
    defaults = {
        "open": 4.0,
        "high": 5.0,
        "low": 3.8,
        "close": 4.8,
        "change": 40.0,
        "volume": 4_000_000,
        "rvol": 20.0,
        "avg_volume": 200_000,
        "market_cap": 30_000_000,
        "float_shares": 8_000_000,
        "sector": "Health Technology",
        "subtype": "common",
    }
    defaults.update(overrides)
    return [
        ticker,
        exchange,
        security_type,
        defaults["subtype"],
        defaults["open"],
        defaults["high"],
        defaults["low"],
        defaults["close"],
        defaults["change"],
        defaults["volume"],
        defaults["rvol"],
        defaults["avg_volume"],
        defaults["market_cap"],
        defaults["float_shares"],
        defaults["sector"],
    ]


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Serves scripted pages, and counts calls so paging can be verified."""

    def __init__(self, pages: list[dict] | None = None, error: Exception | None = None):
        self.pages = pages or []
        self.error = error
        self.calls: list[dict] = []

    def post(self, url, json, headers, timeout):  # noqa: A002 - matches requests' signature
        self.calls.append(json)
        if self.error is not None:
            raise self.error
        index = len(self.calls) - 1
        payload = self.pages[index] if index < len(self.pages) else {"totalCount": 0, "data": []}
        return FakeResponse(payload)


def _single_page(rows: list[list]) -> dict:
    return {"totalCount": len(rows), "data": [{"s": f"X:{r[0]}", "d": r} for r in rows]}


class TestParsing:
    def test_maps_a_row_into_typed_fields(self):
        session = FakeSession(pages=[_single_page([_row("GAPR", change=40.0, rvol=20.0)])])
        result = fetch_market_snapshot(session=session)
        assert len(result) == 1
        row = result[0]
        assert row.ticker == "GAPR"
        assert row.change_pct == 40.0
        assert row.relative_volume == 20.0
        assert row.float_shares == 8_000_000

    def test_exchange_is_translated_to_polygons_mic_vocabulary(self):
        # This is the exact bug this test guards: Criteria.allowed_exchanges is
        # written in MIC codes ("XNAS"), and TradingView reports plain names
        # ("NASDAQ"). Without translation, every candidate silently fails the
        # exchange filter downstream.
        session = FakeSession(pages=[_single_page([_row("GAPR", exchange="NASDAQ")])])
        assert fetch_market_snapshot(session=session)[0].exchange == "XNAS"

    def test_unrecognised_exchange_passes_through_unchanged(self):
        session = FakeSession(pages=[_single_page([_row("GAPR", exchange="OTC")])])
        assert fetch_market_snapshot(session=session)[0].exchange == "OTC"

    def test_null_numeric_fields_become_none_not_zero(self):
        session = FakeSession(
            pages=[_single_page([_row("NEWCO", rvol=None, avg_volume=None, float_shares=None)])]
        )
        row = fetch_market_snapshot(session=session)[0]
        assert row.relative_volume is None
        assert row.average_volume is None
        assert row.float_shares is None

    def test_prev_close_is_derived_from_change_percent(self):
        session = FakeSession(pages=[_single_page([_row("GAPR", close=4.2, change=40.0)])])
        row = fetch_market_snapshot(session=session)[0]
        assert row.prev_close == pytest.approx(3.0)

    def test_prev_close_is_none_on_a_negative_100pct_change(self):
        row = MarketSnapshotRow(
            ticker="X",
            exchange="XNAS",
            security_type="stock",
            security_subtype="common",
            open=0,
            high=0,
            low=0,
            close=0,
            change_pct=-100.0,
            volume=0,
            relative_volume=None,
            average_volume=None,
            market_cap=None,
            float_shares=None,
            sector=None,
        )
        assert row.prev_close is None


class TestUniverseFiltering:
    def test_keeps_common_stock_and_depositary_receipts(self):
        rows = [_row("STOK", security_type="stock"), _row("ADRT", security_type="dr")]
        session = FakeSession(pages=[_single_page(rows)])
        tickers = {row.ticker for row in fetch_market_snapshot(session=session)}
        assert tickers == {"STOK", "ADRT"}

    @pytest.mark.parametrize("bad_type", ["fund", "structured"])
    def test_drops_non_tradeable_types(self, bad_type):
        rows = [_row("STOK", security_type="stock"), _row("FUND", security_type=bad_type)]
        session = FakeSession(pages=[_single_page(rows)])
        tickers = {row.ticker for row in fetch_market_snapshot(session=session)}
        assert tickers == {"STOK"}

    def test_rows_with_missing_data_are_skipped(self):
        payload = {"totalCount": 2, "data": [{"s": "X:BAD"}, {"s": "X:OK", "d": _row("OK")}]}
        session = FakeSession(pages=[payload])
        assert [row.ticker for row in fetch_market_snapshot(session=session)] == ["OK"]


class TestPaging:
    def test_stops_once_total_count_is_reached(self):
        page = _single_page([_row("A"), _row("B")])
        session = FakeSession(pages=[page])
        fetch_market_snapshot(session=session)
        assert len(session.calls) == 1

    def test_pages_when_the_first_response_is_short_of_the_total(self):
        first = {"totalCount": 3, "data": [{"s": "X:A", "d": _row("A")}]}
        second = {
            "totalCount": 3,
            "data": [{"s": "X:B", "d": _row("B")}, {"s": "X:C", "d": _row("C")}],
        }
        session = FakeSession(pages=[first, second])
        result = fetch_market_snapshot(session=session)
        assert {row.ticker for row in result} == {"A", "B", "C"}
        assert len(session.calls) == 2

    def test_an_empty_page_stops_paging_even_if_short_of_the_reported_total(self):
        first = {"totalCount": 100, "data": [{"s": "X:A", "d": _row("A")}]}
        empty = {"totalCount": 100, "data": []}
        session = FakeSession(pages=[first, empty])
        fetch_market_snapshot(session=session)
        assert len(session.calls) == 2  # did not spin forever


class TestErrorHandling:
    def test_a_persistent_failure_raises_tradingviewerror(self):
        session = FakeSession(error=requests.ConnectionError("boom"))
        with pytest.raises(TradingViewError):
            fetch_market_snapshot(session=session, max_retries=1)

    def test_an_http_error_status_is_reported(self):
        session = FakeSession(pages=[{"totalCount": 0, "data": []}])
        session.pages = []  # force raise_for_status on a 500

        class Failing(FakeSession):
            def post(self, url, json, headers, timeout):
                self.calls.append(json)
                return FakeResponse({}, status_code=500)

        with pytest.raises(TradingViewError):
            fetch_market_snapshot(session=Failing(), max_retries=0)

    def test_recovers_after_a_transient_failure(self):
        class FlakyOnce(FakeSession):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def post(self, url, json, headers, timeout):
                self.attempts += 1
                if self.attempts == 1:
                    raise requests.ConnectionError("transient")
                return FakeResponse(_single_page([_row("A")]))

        result = fetch_market_snapshot(session=FlakyOnce(), max_retries=1)
        assert [row.ticker for row in result] == ["A"]
