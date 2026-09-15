"""Persistence: a capture and the names it selected, written as one transaction.

All-or-nothing on purpose. A half-written scan would show the board a selection
that never existed, which is worse than showing the previous capture a few
minutes longer.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import psycopg

_INSERT_SCAN = """
    INSERT INTO scan (captured_at, trade_date, phase, universe_rows, notice)
    VALUES (%s, %s, %s, %s, %s)
    RETURNING id
"""

# A symbol's exchange, type and sector are whatever the latest scan saw. They
# change rarely, and the current value is the one the board renders.
_UPSERT_TICKER = """
    INSERT INTO ticker (symbol, exchange, security_type, sector)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (symbol) DO UPDATE
       SET exchange = EXCLUDED.exchange,
           security_type = EXCLUDED.security_type,
           sector = EXCLUDED.sector,
           last_seen = now()
"""

_INSERT_IN_PLAY = """
    INSERT INTO in_play (scan_id, symbol, close, change_pct, gap_pct, relative_volume,
                         volume, average_volume, float_shares, market_cap,
                         score, qualification)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def connect() -> psycopg.Connection:
    """Open a connection from DATABASE_URL, failing loudly when it is unset."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set; the scraper has nowhere to write")
    return psycopg.connect(dsn, autocommit=True)


def save_scan(
    conn: psycopg.Connection,
    *,
    captured_at: datetime,
    trade_date: date,
    phase: str,
    universe_rows: int,
    notice: str | None,
    candidates: list[Any],
) -> int:
    """Record one capture. Returns the new scan's id."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_INSERT_SCAN, (captured_at, trade_date, phase, universe_rows, notice))
        scan_id = cur.fetchone()[0]
        cur.executemany(_UPSERT_TICKER, [_ticker_row(c) for c in candidates])
        cur.executemany(_INSERT_IN_PLAY, [_in_play_row(scan_id, c) for c in candidates])
    return scan_id


def _ticker_row(candidate: Any) -> tuple[Any, ...]:
    # The provider only ever yields common stock and ADRs on known exchanges, so
    # these are never absent in practice -- but the columns are NOT NULL, and a
    # placeholder beats a crash that would cost the whole capture.
    return (
        candidate.ticker,
        candidate.primary_exchange or "unknown",
        candidate.security_type or "unknown",
        candidate.sector,
    )


def _in_play_row(scan_id: int, candidate: Any) -> tuple[Any, ...]:
    return (
        scan_id,
        candidate.ticker,
        candidate.close,
        candidate.change_pct,
        candidate.gap_pct,
        candidate.relative_volume,
        candidate.volume,
        round(candidate.avg_volume) if candidate.avg_volume is not None else None,
        candidate.float_shares,
        candidate.market_cap,
        candidate.score,
        candidate.qualification,
    )
