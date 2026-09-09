"""Command line entry point.

python -m warrior_screener snapshot                      # what's in play right now
python -m warrior_screener snapshot --no-news            # structural qualifiers only
python -m warrior_screener snapshot --json data/today.json   # feed the dashboard
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from warrior_screener import market_calendar
from warrior_screener.config import Settings, load_settings
from warrior_screener.providers.tradingview import TradingViewError

logger = logging.getLogger("warrior_screener")

DEFAULT_JSON_PATH = Path("data/today.json")


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI surface."""
    parser = argparse.ArgumentParser(
        prog="warrior-screener",
        description="Screen the US market for Warrior-Trading-style in-play stocks, live.",
    )
    parser.add_argument(
        "--config", type=Path, help="Path to criteria YAML (default config/criteria.yml)"
    )
    parser.add_argument("--data-dir", type=Path, help="Output root (default data/)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_cmd = subparsers.add_parser(
        "snapshot",
        help="Screen the live TradingView snapshot (no API key required)",
    )
    snapshot_cmd.add_argument(
        "--json",
        nargs="?",
        const=str(DEFAULT_JSON_PATH),
        metavar="PATH",
        help=f"Also write the result as JSON (default {DEFAULT_JSON_PATH}) for the dashboard",
    )
    snapshot_cmd.add_argument(
        "--quiet", action="store_true", help="Write the JSON without printing the table"
    )
    snapshot_cmd.add_argument(
        "--archive",
        metavar="PATH",
        help="Also write this slot's candidates to a CSV for the research archive",
    )
    snapshot_cmd.add_argument(
        "--slot",
        default="manual",
        help="Label for the archived measurement (e.g. pre_open, t_plus_5); default 'manual'",
    )
    snapshot_cmd.add_argument(
        "--carry-from",
        metavar="DIR",
        help=(
            "Directory of this day's earlier slot CSVs; every ticker in them is "
            "archived again even if it no longer qualifies"
        ),
    )
    _add_criteria_flags(snapshot_cmd)

    return parser


def _add_criteria_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the criteria overrides worth tuning from the command line."""
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--min-change", type=float, help="Minimum %% change on the day")
    parser.add_argument("--min-rvol", type=float, help="Minimum relative volume")
    parser.add_argument("--min-volume", type=int, help="Minimum shares traded on the day")
    parser.add_argument("--max-float", type=int, help="Maximum float in shares (0 disables)")
    parser.add_argument("--max-in-play", type=int)
    parser.add_argument("--min-in-play", type=int)
    parser.add_argument("--no-news", action="store_true", help="Do not require a news catalyst")
    parser.add_argument("--strict-only", action="store_true", help="Never pad with relaxed names")


def _criteria_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Translate CLI flags into a criteria override mapping."""
    numeric = {
        "min_price": getattr(args, "min_price", None),
        "max_price": getattr(args, "max_price", None),
        "min_change_pct": getattr(args, "min_change", None),
        "min_relative_volume": getattr(args, "min_rvol", None),
        "min_day_volume": getattr(args, "min_volume", None),
        "max_in_play": getattr(args, "max_in_play", None),
        "min_in_play": getattr(args, "min_in_play", None),
    }
    # Strip unset flags first: after this point a None is a deliberate value,
    # not an absent flag.
    overrides: dict[str, Any] = {key: val for key, val in numeric.items() if val is not None}

    max_float = getattr(args, "max_float", None)
    if max_float is not None:
        # 0 is the documented way to switch the float filter off entirely.
        overrides["max_float_shares"] = max_float if max_float > 0 else None
    if getattr(args, "no_news", False):
        overrides["require_news_catalyst"] = False
    if getattr(args, "strict_only", False):
        overrides["fill_to_min"] = False
    return overrides


def _settings_from_args(args: argparse.Namespace) -> Settings:
    """Assemble settings from the config file, environment and CLI flags."""
    overrides: dict[str, Any] = {
        "data_dir": args.data_dir,
        "criteria": _criteria_overrides(args),
    }
    return load_settings(args.config, overrides=overrides)


# ------------------------------------------------------------------ Commands


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Screen the live TradingView snapshot: print it, write JSON, or both."""
    settings = _settings_from_args(args)
    from warrior_screener import live_snapshot

    try:
        # One fetch feeds both outputs, so the board and the archived slot
        # describe the same instant rather than two requests seconds apart.
        # Resolved through the module rather than imported by name, so there is
        # a single place to patch the network out.
        rows = live_snapshot.fetch_market_snapshot()
        result = live_snapshot.screen_live(settings.criteria, rows=rows)
    except TradingViewError as exc:
        logger.error("%s", exc)
        return 1

    now_et = datetime.now(market_calendar.EASTERN)
    phase = market_calendar.session_phase(now_et)
    notice = _staleness_notice(now_et, phase)

    if not args.quiet:
        if notice:
            print(f"** {notice} **\n")
        print(
            f"Live snapshot, {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')} "
            f"({result.stats['universe_rows']} tickers scanned)"
        )
        _print_candidates(result.in_play)
        print(
            "\n(TradingView relative volume is time-of-day normalized and no free "
            "news source backs this path, so every row is 'relaxed' unless\n"
            " --no-news is passed -- see the README.)"
        )

    if args.json is not None:
        path = _write_json(Path(args.json), result, now_et, phase, notice, settings, args.slot)
        print(f"Wrote {path}")

    if args.archive:
        path = _write_archive(args, rows, result, now_et, settings)
        print(f"Archived {path}")
    return 0


def _write_archive(
    args: argparse.Namespace,
    rows: list[Any],
    result: Any,
    now_et: datetime,
    settings: Settings,
) -> Path:
    """Write this slot's candidates, plus anything carried from earlier today.

    The carried names are the point of the exercise: a ticker that ran at 09:35
    and faded by the close only has a usable outcome if the closing slot still
    records it. See warrior_screener.archive for the full rationale.
    """
    from warrior_screener import archive
    from warrior_screener.live_snapshot import build_candidate
    from warrior_screener.scanner import evaluate

    trade_date = result.trade_date
    archived = list(result.candidates)
    already = {candidate.ticker for candidate in archived}

    carried_tickers = archive.tickers_seen(Path(args.carry_from)) if args.carry_from else set()
    for row in rows:
        if row.ticker not in carried_tickers or row.ticker in already:
            continue
        # Anything reaching here failed the price/change/volume gate at this
        # instant -- a ticker that passed it is already in result.candidates.
        # Keep its reject reasons, but never let it carry a score: the score is
        # a percentile rank inside the live pool, which this row is not part of.
        candidate = build_candidate(row, trade_date)
        candidate.rejected_by = evaluate(candidate, settings.criteria)
        candidate.qualification = archive.CARRIED
        archived.append(candidate)

    return archive.write_slot(
        Path(args.archive),
        archived,
        slot=args.slot,
        captured_at=now_et,
        in_play={candidate.ticker for candidate in result.in_play},
        sectors={row.ticker: row.sector for row in rows},
    )


def _staleness_notice(now_et: datetime, phase: str) -> str | None:
    """The warning that belongs on this result, or None when it is live.

    TradingView has no "market closed" or "no trades yet" signal of its own --
    it always answers with the most recent session, which reads exactly like
    live data unless something says otherwise. Caught live twice: once run on a
    market holiday (silently showed Friday's numbers), then again at 04:52 ET
    the following trading day -- a real trading day, so that check passed, but
    pre-market had barely started and lower-volume names still showed the
    *same* stale numbers. "Is today a trading day" and "has trading actually
    happened yet" are different questions; session_phase answers the second.

    This matters more now than it did as a terminal warning: the dashboard is
    refreshed two hours before the open, squarely inside the pre-market case,
    so the notice travels into the JSON and onto the page.
    """
    stamp = now_et.strftime("%H:%M %Z")
    if phase == "closed":
        return (
            f"Market closed ({now_et.strftime('%Y-%m-%d %H:%M %Z')}) -- these figures "
            "are from the last session, not today."
        )
    if phase == "pre-market":
        return (
            f"Pre-market ({stamp}, regular open is 09:30 ET) -- lower-volume names may "
            "still show yesterday's numbers until they actually trade today."
        )
    if phase == "after-hours":
        return (
            f"After-hours ({stamp}, regular close was 16:00 ET) -- figures include "
            "today's regular session plus after-hours activity."
        )
    return None


def _write_json(
    path: Path,
    result: Any,
    now_et: datetime,
    phase: str,
    notice: str | None,
    settings: Settings,
    slot: str,
) -> Path:
    """Merge this slot's board into the dashboard's payload.

    The page shows the day's slots side by side -- the pre-open watchlist and
    the ten-minutes-in board are different questions and a trader wants both --
    so each run updates its own slot and leaves the others alone. When the file
    is from an earlier session it is replaced wholesale rather than merged:
    yesterday's pre-open board sitting in a tab next to today's would be
    indistinguishable from a live one.

    Everything the page needs to render honestly lives in here, including when
    each slot ran and whether the market was open at the time. A dashboard that
    cannot tell a live board from yesterday's leftovers is worse than none.
    """
    criteria = settings.criteria
    trade_date = result.trade_date.isoformat()

    payload = _existing_payload(path)
    if payload.get("trade_date") != trade_date:
        payload = {"trade_date": trade_date, "slots": {}}

    payload["updated_at"] = now_et.isoformat(timespec="seconds")
    payload["criteria"] = {
        "min_change_pct": criteria.min_change_pct,
        "min_price": criteria.min_price,
        "max_price": criteria.max_price,
        "min_relative_volume": criteria.min_relative_volume,
        "max_float_shares": criteria.max_float_shares,
        "min_day_volume": criteria.min_day_volume,
        "require_news_catalyst": criteria.require_news_catalyst,
    }
    payload.setdefault("slots", {})[slot] = {
        "generated_at": now_et.isoformat(timespec="seconds"),
        "session_phase": phase,
        "notice": notice,
        "stats": result.stats,
        "in_play": [_json_row(candidate) for candidate in result.in_play],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _existing_payload(path: Path) -> dict[str, Any]:
    """Today's payload as already written, or an empty one.

    A corrupt or half-written file must not take the next slot down with it:
    the run that would have fixed the board is exactly the one being asked to
    parse it. Start fresh instead.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_row(candidate: Any) -> dict[str, Any]:
    """One in-play name, flattened to the fields the dashboard renders."""
    return {
        "ticker": candidate.ticker,
        "close": candidate.close,
        "change_pct": candidate.change_pct,
        "gap_pct": candidate.gap_pct,
        "relative_volume": candidate.relative_volume,
        "volume": candidate.volume,
        "avg_volume": candidate.avg_volume,
        "float_shares": candidate.float_shares,
        "market_cap": candidate.market_cap,
        "primary_exchange": candidate.primary_exchange,
        "score": candidate.score,
        "qualification": candidate.qualification,
        "rejected_by": list(candidate.rejected_by),
        # False means nothing checked for news, not that there is no catalyst.
        "news_checked": candidate.news_checked,
        "news_count": candidate.news_count,
    }


# ------------------------------------------------------------------ Printing

# (source key, header, column width, rendering kind)
_COLUMNS = (
    ("ticker", "TICKER", 8, "text"),
    ("close", "CLOSE", 9, "price"),
    ("change_pct", "CHG%", 8, "pct"),
    ("gap_pct", "GAP%", 8, "pct"),
    ("relative_volume", "RVOL", 8, "pct"),
    ("volume", "VOLUME", 12, "count"),
    ("float_shares", "FLOAT", 12, "count"),
    ("news_count", "NEWS", 5, "count"),
    ("score", "SCORE", 7, "pct"),
    ("qualification", "QUAL", 8, "text"),
)


def _print_candidates(candidates: Any) -> None:
    _print_rows([candidate.to_row() for candidate in candidates])


def _print_rows(rows: list[dict[str, Any]]) -> None:
    """Print an in-play table to stdout.

    Columns are joined with an explicit space rather than relying on ``width``
    alone -- TradingView's time-of-day-normalized RVOL can print 4-5 digit
    values that overflow a narrow column, and without a guaranteed separator
    that number silently glues onto the next one (observed live:
    "13,321.90123,119,671" reads as one number but is RVOL and VOLUME
    concatenated).
    """
    header = " ".join(label.ljust(width) for _, label, width, _kind in _COLUMNS)
    print(header)
    print("-" * len(header))
    for row in rows:
        cells = []
        for key, _label, width, kind in _COLUMNS:
            value = row.get(key)
            # "0 headlines" and "we never looked" must not print the same.
            if key == "news_count" and not _is_true(row.get("news_checked")):
                value = None
            cells.append(_format_cell(value, kind).ljust(width))
        print(" ".join(cells))


def _is_true(value: Any) -> bool:
    """Interpret a bool or its CSV string form."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1")


def _format_cell(value: Any, kind: str = "text") -> str:
    """Render one table cell, coercing numeric strings read back from CSV."""
    if value is None or value == "":
        return "-"
    if kind == "text":
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if kind == "count":
        return f"{int(number):,}"
    return f"{number:,.2f}"


_COMMANDS = {
    "snapshot": _cmd_snapshot,
}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the command, and translate errors into exit codes."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return _COMMANDS[args.command](args)
    except (TradingViewError, ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
