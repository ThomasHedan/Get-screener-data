"""Orchestration: run one session end to end, or backfill a range of them.

This is the layer that touches the network and the disk. Everything it calls is
either pure (``scanner``, ``intraday``) or a thin I/O wrapper (``storage``,
``providers``), which is what makes the criteria testable offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from warrior_screener.config import Settings
from warrior_screener.history import load_history
from warrior_screener.intraday import compute_features
from warrior_screener.providers.base import MarketDataProvider, ProviderError
from warrior_screener.scanner import Enricher, ScanResult, run_scan
from warrior_screener.storage import Archive

logger = logging.getLogger(__name__)


@dataclass
class CollectionOutcome:
    """What one session's collection did, for logging and CLI exit status."""

    trade_date: date
    status: str  # "collected" | "skipped" | "market_closed" | "failed"
    in_play: int = 0
    intraday_tickers: int = 0
    detail: str = ""


def collect_day(
    settings: Settings,
    provider: MarketDataProvider,
    archive: Archive,
    trade_date: date,
    *,
    force: bool = False,
) -> CollectionOutcome:
    """Screen ``trade_date`` and archive the scan, intraday bars and features."""
    if not force and archive.scan_dir(trade_date).joinpath("in_play.csv").exists():
        logger.info("Scan for %s already archived; skipping (use --force to redo)", trade_date)
        return CollectionOutcome(trade_date, "skipped")

    bars = archive.read_daily_bars(trade_date)
    if not bars and not archive.has_daily_bars(trade_date):
        bars = list(provider.grouped_daily(trade_date))
        archive.write_daily_bars(trade_date, bars)

    if not bars:
        logger.info("No market data for %s -- weekend or holiday", trade_date)
        archive.record_run({"trade_date": trade_date, "status": "market_closed"})
        return CollectionOutcome(trade_date, "market_closed")

    history = load_history(
        provider,
        archive,
        trade_date,
        settings.criteria.rvol_lookback_days,
        refresh_previous_after_days=settings.refresh_previous_after_days,
    )
    enricher = Enricher(provider, archive, settings)
    try:
        result = run_scan(bars, history, settings, enricher, trade_date)
    finally:
        # The cache is worth keeping even if the scan blew up mid-way; those
        # lookups were paid for against the rate limit.
        enricher.flush()

    archive.write_scan(trade_date, result.candidates, result.in_play)

    collected = 0
    if settings.collect_intraday:
        collected = _collect_intraday(settings, provider, archive, result, force=force)

    archive.record_run(
        {
            "trade_date": trade_date,
            "status": "collected",
            "intraday_tickers": collected,
            **result.stats,
        }
    )
    logger.info(
        "%s: %d in play (%s)",
        trade_date,
        len(result.in_play),
        ", ".join(c.ticker for c in result.in_play) or "none",
    )
    return CollectionOutcome(
        trade_date, "collected", in_play=len(result.in_play), intraday_tickers=collected
    )


def _collect_intraday(
    settings: Settings,
    provider: MarketDataProvider,
    archive: Archive,
    result: ScanResult,
    *,
    force: bool,
) -> int:
    """Fetch and store minute bars plus the feature row for each in-play name."""
    feature_rows = []
    collected = 0

    for candidate in result.in_play:
        bars = []
        if not force and archive.has_minute_bars(result.trade_date, candidate.ticker):
            logger.debug("Minute bars for %s already archived", candidate.ticker)
        else:
            try:
                bars = list(provider.minute_bars(candidate.ticker, result.trade_date))
            except ProviderError:
                # One bad symbol must not cost the whole session's snapshot.
                logger.exception(
                    "Intraday fetch failed for %s on %s", candidate.ticker, result.trade_date
                )
            else:
                archive.write_minute_bars(result.trade_date, candidate.ticker, bars)
                collected += 1
                if not bars:
                    logger.warning(
                        "No intraday bars returned for %s on %s",
                        candidate.ticker,
                        result.trade_date,
                    )
        feature_rows.append(compute_features(candidate, bars, result.trade_date))

    if feature_rows:
        archive.write_features(result.trade_date, feature_rows)
    return collected


def backfill(
    settings: Settings,
    provider: MarketDataProvider,
    archive: Archive,
    start: date,
    end: date,
    *,
    force: bool = False,
) -> list[CollectionOutcome]:
    """Collect every session in ``[start, end]``, oldest first.

    Runs oldest-first so each day's RVOL window is already cached by the time
    the next day needs it. A failure on one session is recorded and the walk
    continues -- a single bad day should not abandon a month of backfill.
    """
    if start > end:
        raise ValueError("start date must not be after end date")

    outcomes: list[CollectionOutcome] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() >= 5:
            cursor += timedelta(days=1)
            continue
        try:
            outcomes.append(collect_day(settings, provider, archive, cursor, force=force))
        except ProviderError as exc:
            logger.exception("Collection failed for %s", cursor)
            archive.record_run({"trade_date": cursor, "status": "failed", "error": str(exc)})
            outcomes.append(CollectionOutcome(cursor, "failed", detail=str(exc)))
        cursor += timedelta(days=1)
    return outcomes
