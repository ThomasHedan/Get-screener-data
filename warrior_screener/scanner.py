"""The Warrior-Trading screen: from the full market to the day's 5-10 in-play names.

Ross Cameron's momentum scan is a small set of hard filters applied to the whole
US market, then a judgement call about which handful of survivors are worth
watching. This module implements the filters literally and replaces the
judgement call with a transparent, reproducible score.

It is deliberately free of any I/O: it takes candidates that somebody else has
already built (today, :mod:`warrior_screener.live_snapshot`, from one
TradingView request) and does three things to them -- ``evaluate`` marks why
each fails, ``score_candidates`` ranks them against that day's own pool, and
``select_in_play`` picks the final list. Keeping it pure is what lets the same
screen definition be re-run against a different data source without touching
this file.
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from warrior_screener.config import Criteria
from warrior_screener.models import Candidate

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Everything one session's screen produced."""

    trade_date: date
    candidates: list[Candidate] = field(default_factory=list)
    in_play: list[Candidate] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


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
