"""Persistence: a capture and the names it selected, written as one transaction.

All-or-nothing on purpose. A half-written scan would show the board a selection
that never existed, which is worse than showing the previous capture a few
minutes longer.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

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


# Written with COPY rather than INSERT: a capture is ~10,000 rows, and COPY is
# the difference between a fraction of a second and several seconds per tick.
_SNAPSHOT_COLUMNS = (
    "trade_date",
    "scan_id",
    "symbol",
    "exchange",
    "security_type",
    "sector",
    "open",
    "high",
    "low",
    "close",
    "change_pct",
    "volume",
    "relative_volume",
    "average_volume",
    "market_cap",
    "float_shares",
    "extra",
)


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
    market: list[Any] = (),
) -> int:
    """Record one capture -- the whole market, plus the screen's verdict.

    One transaction: a half-written capture would leave the corpus claiming a
    market state that the scan header does not vouch for.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_INSERT_SCAN, (captured_at, trade_date, phase, universe_rows, notice))
        scan_id = cur.fetchone()[0]
        cur.executemany(_UPSERT_TICKER, [_ticker_row(c) for c in candidates])
        cur.executemany(_INSERT_IN_PLAY, [_in_play_row(scan_id, c) for c in candidates])
        if market:
            _ensure_partition(cur, trade_date)
            _copy_market(cur, scan_id, trade_date, market)
    return scan_id


def _ensure_partition(cur: psycopg.Cursor, trade_date: date) -> None:
    """Create this month's snapshot partition if it is missing.

    Idempotent and cheap, so it runs on every capture rather than needing a
    separate scheduled job that could be forgotten and silently break writes.
    """
    start = trade_date.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    # DDL takes no bound parameters, so the bounds are composed as literals --
    # sql.Literal quotes them, which a format string would not.
    cur.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {} PARTITION OF snapshot FOR VALUES FROM ({}) TO ({})"
        ).format(
            sql.Identifier(f"snapshot_{start:%Y_%m}"),
            sql.Literal(start),
            sql.Literal(end),
        )
    )


def _copy_market(cur: psycopg.Cursor, scan_id: int, trade_date: date, market: list[Any]) -> None:
    columns = ", ".join(f'"{name}"' for name in _SNAPSHOT_COLUMNS)
    with cur.copy(f"COPY snapshot ({columns}) FROM STDIN") as copy:
        for row in market:
            copy.write_row(
                (
                    trade_date,
                    scan_id,
                    row.ticker,
                    row.exchange,
                    row.security_type,
                    row.sector,
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    row.change_pct,
                    row.volume,
                    row.relative_volume,
                    row.average_volume,
                    row.market_cap,
                    row.float_shares,
                    Jsonb(row.extra) if row.extra else None,
                )
            )


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
