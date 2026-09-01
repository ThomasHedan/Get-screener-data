"""The Warrior-Trading screen: from the full market to the day's 5-10 in-play names.

Ross Cameron's momentum scan is a small set of hard filters applied to the whole
US market, then a judgement call about which handful of survivors are worth
watching. This module implements the filters literally and replaces the
judgement call with a transparent, reproducible score.

The scan runs in two passes to keep API usage bounded:

1. **Coarse pass** -- price, percentage change and traded volume, computed from
   the one grouped-daily request that prices the whole market. Free.
2. **Enrichment pass** -- share structure and news catalyst, one request per
   ticker, run only on the strongest coarse survivors (``max_enrich``).
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from warrior_screener.config import Criteria, Settings
from warrior_screener.history import SessionHistory
from warrior_screener.models import Candidate, DailyBar, TickerReference
from warrior_screener.providers.base import MarketDataProvider, ProviderError
from warrior_screener.providers.polygon import eastern_session_window
from warrior_screener.storage import Archive

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Everything one session's screen produced."""

    trade_date: date
    candidates: list[Candidate] = field(default_factory=list)
    in_play: list[Candidate] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------- Coarse pass


def coarse_candidates(
    bars: Sequence[DailyBar],
    history: SessionHistory,
    criteria: Criteria,
    *,
    limit: int,
) -> list[Candidate]:
    """Apply the free filters and return the strongest survivors.

    Survivors are ranked by ``change% x dollar volume`` -- the practical proxy
    for "which gappers actually had money moving through them" -- and truncated
    to ``limit`` so the enrichment pass has a bounded API cost.
    """
    survivors: list[Candidate] = []

    for bar in bars:
        if not _passes_ticker_hygiene(bar.ticker, criteria):
            continue
        if not criteria.min_price <= bar.close <= criteria.max_price:
            continue
        if bar.volume < criteria.min_day_volume:
            continue

        prev_close = history.prev_close.get(bar.ticker)
        if not prev_close:
            # No prior close: either a first day of trading or a gap in the
            # archive. Either way percentage change is undefined, so it cannot
            # clear a "+10% today" filter.
            continue

        change_pct = (bar.close - prev_close) / prev_close * 100.0
        if change_pct < criteria.min_change_pct:
            continue

        gap_pct = (bar.open - prev_close) / prev_close * 100.0 if bar.open else None
        if criteria.min_gap_pct is not None and (gap_pct is None or gap_pct < criteria.min_gap_pct):
            continue

        candidate = Candidate(
            ticker=bar.ticker,
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            prev_close=prev_close,
            gap_pct=_round(gap_pct),
            change_pct=_round(change_pct),
            range_pct=_round((bar.high - bar.low) / bar.low * 100.0 if bar.low else None),
            avg_volume=_round(history.average_volume(bar.ticker), 1),
            relative_volume=_round(history.relative_volume(bar.ticker, bar.volume)),
            dollar_volume=_round(bar.close * bar.volume, 0),
        )
        survivors.append(candidate)

    survivors.sort(key=_coarse_rank, reverse=True)
    if len(survivors) > limit:
        logger.info("Coarse pass found %d candidates; enriching the top %d", len(survivors), limit)
    return survivors[:limit]


def _coarse_rank(candidate: Candidate) -> float:
    """Rank key for the coarse pass: momentum weighted by money traded."""
    return (candidate.change_pct or 0.0) * (candidate.dollar_volume or 0.0)


def _passes_ticker_hygiene(ticker: str, criteria: Criteria) -> bool:
    """Drop warrants, rights and units, which the grouped feed mixes in.

    Polygon appends a suffix after a dot (``ABCD.WS``) or, on Nasdaq, a fifth
    letter (``ABCDW``) for these. The fifth-letter rule is a heuristic -- some
    genuine four-letter tickers end in W -- so it is only applied to five-letter
    Nasdaq-style symbols, and the security-type check in the enrichment pass is
    the authoritative filter.
    """
    if "." in ticker:
        suffix = ticker.split(".", 1)[1].upper()
        return suffix not in criteria.exclude_ticker_suffixes
    return not (len(ticker) == 5 and ticker.isalpha() and ticker[-1].upper() in {"W", "R", "U"})


# ----------------------------------------------------------- Enrichment pass


class Enricher:
    """Fetches share structure and news for candidates, with an on-disk cache."""

    def __init__(
        self,
        provider: MarketDataProvider | None,
        archive: Archive,
        settings: Settings,
        *,
        news_cache: dict[str, tuple[int, str | None]] | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        """``provider=None`` puts the enricher in cache-only mode: it answers
        from the on-disk reference cache and never makes a request, which is how
        the criteria can be re-tuned offline against an existing archive.

        ``news_cache`` supplies headline counts recorded by an earlier online
        scan, so an offline rescan can still evaluate the catalyst criterion.

        ``snapshot`` is a ticker-keyed dict of TradingView
        ``MarketSnapshotRow`` (see ``warrior_screener.providers.tradingview``),
        fetched once for free with no API key. When a ticker appears in it,
        that reference data is used instead of a Polygon lookup -- exchange,
        security type and share count change rarely enough that TradingView's
        *current* view is a reasonable proxy for a historical date, and this
        is normally the largest single cut to a backfill's API-call budget
        (up to ``max_enrich`` Polygon calls a day, down to zero for any ticker
        still listed). A ticker missing from the snapshot -- typically because
        it has since been delisted -- falls back to the historical provider
        exactly as before, so the archive's delisted-ticker coverage is
        unaffected.
        """
        self._provider = provider
        self._archive = archive
        self._settings = settings
        self._news_cache = news_cache or {}
        self._snapshot = snapshot or {}
        self._cache = archive.load_reference_cache()
        self._float_overrides = load_float_overrides(archive.reference_dir / "float_overrides.csv")
        self._api_calls = 0
        self._snapshot_hits = 0

    @property
    def api_calls(self) -> int:
        """Number of provider requests this enricher has issued."""
        return self._api_calls

    @property
    def snapshot_hits(self) -> int:
        """Reference lookups answered from the TradingView snapshot instead."""
        return self._snapshot_hits

    def enrich(self, candidate: Candidate, criteria: Criteria) -> None:
        """Fill in reference and catalyst fields on ``candidate`` in place."""
        reference = self._reference(candidate.ticker, candidate.trade_date)
        if reference is not None:
            candidate.security_type = reference.security_type
            candidate.primary_exchange = reference.primary_exchange
            candidate.shares_outstanding = reference.shares_outstanding
            candidate.market_cap = reference.market_cap

        override = self._float_overrides.get(candidate.ticker)
        candidate.float_shares = override if override is not None else candidate.shares_outstanding

        # News costs a request, so only ask for names that could still qualify.
        if criteria.require_news_catalyst and not _structural_rejects(candidate, criteria):
            self._attach_news(candidate)

    def _reference(self, ticker: str, trade_date: date) -> TickerReference | None:
        """Return reference data: on-disk cache, then TradingView, then the
        historical provider -- in that order, cheapest first."""
        cached = self._cache.get(ticker)
        if cached and _cache_is_fresh(cached, trade_date, self._settings.reference_cache_days):
            return _reference_from_cache(cached)

        snapshot_row = self._snapshot.get(ticker)
        if snapshot_row is not None:
            from warrior_screener.providers.tradingview import to_ticker_reference

            self._snapshot_hits += 1
            return self._remember(to_ticker_reference(snapshot_row, as_of=trade_date), trade_date)

        if self._provider is None:
            return _reference_from_cache(cached) if cached else None

        try:
            reference = self._provider.ticker_details(ticker, as_of=trade_date)
            self._api_calls += 1
        except ProviderError:
            logger.exception("Reference lookup failed for %s", ticker)
            return _reference_from_cache(cached) if cached else None

        if reference is None:
            # Delisted or unknown to the provider (and not in the snapshot
            # either). Cache the miss so a backfill does not re-request it for
            # every session in the range.
            self._cache[ticker] = {
                "ticker": ticker,
                "missing": True,
                "as_of": trade_date.isoformat(),
            }
            return None

        return self._remember(reference, trade_date)

    def _remember(self, reference: TickerReference, trade_date: date) -> TickerReference:
        """Cache a resolved reference (from either source) and log it."""
        self._cache[reference.ticker] = {
            "ticker": reference.ticker,
            "name": reference.name,
            "security_type": reference.security_type,
            "primary_exchange": reference.primary_exchange,
            "shares_outstanding": reference.shares_outstanding,
            "market_cap": reference.market_cap,
            "is_active": reference.is_active,
            "list_date": reference.list_date.isoformat() if reference.list_date else None,
            "as_of": trade_date.isoformat(),
        }
        self._archive.record_reference(reference)
        return reference

    def _attach_news(self, candidate: Candidate) -> None:
        """Count catalyst headlines in the overnight-through-close window."""
        cached = self._news_cache.get(candidate.ticker)
        if cached is not None:
            candidate.news_count, candidate.news_headline = cached
            candidate.news_checked = True
            return
        if self._provider is None:
            return
        start, end = eastern_session_window(candidate.trade_date)
        try:
            articles = self._provider.news(candidate.ticker, start, end)
            self._api_calls += 1
        except ProviderError:
            logger.exception("News lookup failed for %s", candidate.ticker)
            return
        candidate.news_count = len(articles)
        candidate.news_checked = True
        if articles:
            candidate.news_headline = max(articles, key=lambda item: item[0])[1][:200]

    def flush(self) -> None:
        """Persist the reference cache."""
        self._archive.save_reference_cache(self._cache)


def _cache_is_fresh(entry: dict[str, Any], trade_date: date, ttl_days: int) -> bool:
    """True if a cached reference entry is recent enough to reuse."""
    as_of = entry.get("as_of")
    if not as_of:
        return False
    try:
        return abs((trade_date - date.fromisoformat(as_of)).days) <= ttl_days
    except ValueError:
        return False


def _reference_from_cache(entry: dict[str, Any] | None) -> TickerReference | None:
    """Rebuild a ``TickerReference`` from a cache entry."""
    if not entry or entry.get("missing"):
        return None
    list_date = entry.get("list_date")
    return TickerReference(
        ticker=entry["ticker"],
        name=entry.get("name"),
        security_type=entry.get("security_type"),
        primary_exchange=entry.get("primary_exchange"),
        shares_outstanding=entry.get("shares_outstanding"),
        market_cap=entry.get("market_cap"),
        is_active=entry.get("is_active"),
        list_date=date.fromisoformat(list_date) if list_date else None,
        as_of=date.fromisoformat(entry["as_of"]) if entry.get("as_of") else None,
    )


def load_float_overrides(path: Any) -> dict[str, int]:
    """Load a ``ticker,float_shares`` CSV of hand-checked free floats.

    Providers publish shares outstanding, not free float; insiders and locked-up
    shares can make the difference several-fold on exactly the small-cap names
    this screen targets. Dropping a CSV here overrides the proxy per ticker.
    """
    import csv
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return {}
    overrides: dict[str, int] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("ticker") or "").strip().upper()
            raw = (row.get("float_shares") or "").strip()
            if not ticker or not raw:
                continue
            try:
                overrides[ticker] = int(float(raw))
            except ValueError:
                logger.warning("Ignoring unparseable float override for %s: %r", ticker, raw)
    logger.info("Loaded %d float overrides from %s", len(overrides), path)
    return overrides


# ----------------------------------------------------------------- Filtering


def _structural_rejects(candidate: Candidate, criteria: Criteria) -> list[str]:
    """Reject reasons that do not depend on news, for gating the news call."""
    reasons: list[str] = []

    # A missing security type or exchange means the provider no longer describes
    # the ticker (typically because it has been delisted). Judge it on what is
    # known rather than dropping it for the gap.
    security_type = candidate.security_type
    if (
        criteria.allowed_security_types
        and security_type is not None
        and security_type not in criteria.allowed_security_types
    ):
        reasons.append("security_type")

    exchange = candidate.primary_exchange
    if (
        criteria.allowed_exchanges
        and exchange is not None
        and exchange not in criteria.allowed_exchanges
    ):
        reasons.append("exchange")

    # An unknown float, by contrast, is a rejection: "low float" is the core of
    # this screen and cannot be assumed.
    max_float = criteria.max_float_shares
    if max_float is not None and (
        candidate.float_shares is None or candidate.float_shares > max_float
    ):
        reasons.append("float")

    max_cap = criteria.max_market_cap
    if max_cap is not None and candidate.market_cap is not None and candidate.market_cap > max_cap:
        reasons.append("market_cap")

    rvol = candidate.relative_volume
    if rvol is None or rvol < criteria.min_relative_volume:
        reasons.append("relative_volume")

    return reasons


def evaluate(candidate: Candidate, criteria: Criteria) -> list[str]:
    """Return the names of every criterion ``candidate`` fails."""
    reasons = _structural_rejects(candidate, criteria)
    if criteria.require_news_catalyst and candidate.news_count < criteria.min_news_articles:
        # Only claim a missing catalyst when the lookup actually ran. A skipped
        # lookup means the candidate is already rejected on structure, and
        # labelling it "news" would poison the reject statistics.
        reasons.append("news" if candidate.news_checked else "news_unknown")
    return reasons


# ------------------------------------------------------------------- Scoring


def score_candidates(candidates: Sequence[Candidate], criteria: Criteria) -> None:
    """Assign each candidate a 0-1 composite score, in place.

    Components are percentile ranks *within the day's own candidate pool*, not
    absolute values. That keeps the score comparable across a sleepy Tuesday and
    a small-cap frenzy, and stops one 400x-RVOL outlier from flattening the rest.
    """
    if not candidates:
        return

    rvol_ranks = _percentile_ranks([c.relative_volume for c in candidates])
    change_ranks = _percentile_ranks([c.change_pct for c in candidates])
    # Negated: a smaller float should rank higher.
    float_ranks = _percentile_ranks(
        [(-float(c.float_shares) if c.float_shares else None) for c in candidates]
    )
    weights = (
        criteria.weight_relative_volume,
        criteria.weight_change_pct,
        criteria.weight_float,
        criteria.weight_news,
    )
    total_weight = sum(weights) or 1.0

    for index, candidate in enumerate(candidates):
        news_score = min(candidate.news_count, 3) / 3.0
        raw = (
            criteria.weight_relative_volume * rvol_ranks[index]
            + criteria.weight_change_pct * change_ranks[index]
            + criteria.weight_float * float_ranks[index]
            + criteria.weight_news * news_score
        )
        candidate.score = round(raw / total_weight, 4)


def _percentile_ranks(values: Sequence[float | None]) -> list[float]:
    """Percentile rank of each value within the non-missing values (ties averaged).

    Missing values rank 0.0 -- unknown share structure or volume history should
    never be rewarded.
    """
    present = sorted(value for value in values if value is not None)
    count = len(present)
    if count == 0:
        return [0.0] * len(values)
    if count == 1:
        return [0.5 if value is not None else 0.0 for value in values]

    ranks: list[float] = []
    for value in values:
        if value is None:
            ranks.append(0.0)
            continue
        low = bisect_left(present, value)
        high = bisect_right(present, value)
        ranks.append(((low + high) / 2.0) / count)
    return ranks


# ----------------------------------------------------------------- Selection


def select_in_play(candidates: Sequence[Candidate], criteria: Criteria) -> list[Candidate]:
    """Pick the day's in-play names, tagging each as ``strict`` or ``relaxed``.

    Strict names clear every criterion. If fewer than ``min_in_play`` do -- which
    is normal on a quiet session, since a sub-10M float running 5x volume is not
    an everyday event -- the list is topped up with the best names that fail only
    the relaxable criteria, tagged so research can exclude them.
    """
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)

    strict = [c for c in ranked if not c.rejected_by]
    for candidate in strict:
        candidate.qualification = "strict"
    selected = strict[: criteria.max_in_play]

    if criteria.fill_to_min and len(selected) < criteria.min_in_play:
        relaxable = set(criteria.relaxed_drop_filters)
        chosen = {c.ticker for c in selected}
        for candidate in ranked:
            if len(selected) >= criteria.min_in_play:
                break
            if candidate.ticker in chosen:
                continue
            if candidate.rejected_by and set(candidate.rejected_by) <= relaxable:
                candidate.qualification = "relaxed"
                selected.append(candidate)
                chosen.add(candidate.ticker)

    return selected


# ------------------------------------------------------------------ Pipeline


def run_scan(
    bars: Sequence[DailyBar],
    history: SessionHistory,
    settings: Settings,
    enricher: Enricher | None,
    trade_date: date,
) -> ScanResult:
    """Run the full screen for one session and return the result.

    Pure with respect to the archive: it reads no files and writes none, so the
    criteria can be re-tested against cached bars without touching the network.
    """
    criteria = settings.criteria
    candidates = coarse_candidates(bars, history, criteria, limit=settings.max_enrich)

    for candidate in candidates:
        if enricher is not None:
            enricher.enrich(candidate, criteria)
        candidate.rejected_by = evaluate(candidate, criteria)

    score_candidates(candidates, criteria)
    in_play = select_in_play(candidates, criteria)

    stats = {
        "universe_bars": len(bars),
        "prior_sessions": history.sessions_loaded,
        "coarse_candidates": len(candidates),
        "strict_qualifiers": sum(1 for c in candidates if not c.rejected_by),
        "in_play": len(in_play),
        "relaxed_in_play": sum(1 for c in in_play if c.qualification == "relaxed"),
        "enrichment_api_calls": enricher.api_calls if enricher else 0,
        "enrichment_snapshot_hits": enricher.snapshot_hits if enricher else 0,
    }
    logger.info("Scan %s: %s", trade_date, stats)
    return ScanResult(
        trade_date=trade_date,
        candidates=sorted(candidates, key=lambda c: c.score, reverse=True),
        in_play=in_play,
        stats=stats,
    )


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None
