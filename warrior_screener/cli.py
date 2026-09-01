"""Command line entry point.

python -m warrior_screener collect                 # today's in-play names
python -m warrior_screener backfill --start 2026-06-01 --end 2026-08-31
python -m warrior_screener rescan --date 2026-08-28 --max-float 20000000
python -m warrior_screener show --date 2026-08-28
python -m warrior_screener status
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from warrior_screener import collector
from warrior_screener.config import Settings, load_settings
from warrior_screener.history import load_history
from warrior_screener.providers.base import MarketDataProvider, ProviderError
from warrior_screener.providers.polygon import PolygonProvider
from warrior_screener.scanner import Enricher, run_scan
from warrior_screener.storage import Archive

logger = logging.getLogger("warrior_screener")


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI surface."""
    parser = argparse.ArgumentParser(
        prog="warrior-screener",
        description=(
            "Screen for Warrior-Trading-style in-play stocks and archive their intraday data."
        ),
    )
    parser.add_argument(
        "--config", type=Path, help="Path to criteria YAML (default config/criteria.yml)"
    )
    parser.add_argument("--data-dir", type=Path, help="Archive root (default data/)")
    parser.add_argument("--api-key", help="Provider API key (default $POLYGON_API_KEY)")
    parser.add_argument("--rpm", type=int, help="Provider requests per minute budget")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_cmd = subparsers.add_parser("collect", help="Screen one session and archive it")
    collect_cmd.add_argument("--date", default="today", help="ISO date, 'today' or 'yesterday'")
    collect_cmd.add_argument("--force", action="store_true", help="Re-collect an archived session")
    collect_cmd.add_argument(
        "--no-intraday", action="store_true", help="Skip minute-bar collection"
    )
    _add_criteria_flags(collect_cmd)

    backfill_cmd = subparsers.add_parser("backfill", help="Collect a range of sessions")
    backfill_cmd.add_argument("--start", required=True, help="First session (ISO date)")
    backfill_cmd.add_argument(
        "--end", default="today", help="Last session (ISO date, default today)"
    )
    backfill_cmd.add_argument("--force", action="store_true", help="Re-collect archived sessions")
    backfill_cmd.add_argument(
        "--no-intraday", action="store_true", help="Skip minute-bar collection"
    )
    _add_criteria_flags(backfill_cmd)

    rescan_cmd = subparsers.add_parser(
        "rescan", help="Re-run the screen offline from cached bars (no API calls, nothing written)"
    )
    rescan_cmd.add_argument("--date", default="today", help="ISO date, 'today' or 'yesterday'")
    _add_criteria_flags(rescan_cmd)

    show_cmd = subparsers.add_parser("show", help="Print an archived in-play list")
    show_cmd.add_argument("--date", default="today", help="ISO date, 'today' or 'yesterday'")

    subparsers.add_parser("status", help="Summarise archive coverage")
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
        "api_key": args.api_key,
        "requests_per_minute": args.rpm,
        "criteria": _criteria_overrides(args),
    }
    if getattr(args, "no_intraday", False):
        overrides["collect_intraday"] = False
    return load_settings(args.config, overrides=overrides)


def _resolve_date(text: str) -> date:
    """Parse ``today``, ``yesterday`` or an ISO date."""
    normalised = text.strip().lower()
    if normalised == "today":
        return date.today()
    if normalised == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return date.fromisoformat(normalised)
    except ValueError as exc:
        raise SystemExit(f"Invalid date {text!r}; use YYYY-MM-DD, 'today' or 'yesterday'") from exc


def _make_provider(settings: Settings) -> MarketDataProvider:
    """Instantiate the configured market data provider."""
    if settings.provider != "polygon":
        raise SystemExit(
            f"Unknown provider {settings.provider!r}. Only 'polygon' ships today; "
            f"add a class implementing MarketDataProvider to use another vendor."
        )
    return PolygonProvider(
        settings.api_key,
        requests_per_minute=settings.requests_per_minute,
        max_retries=settings.max_retries,
        timeout=settings.request_timeout,
    )


# ------------------------------------------------------------------ Commands


def _cmd_collect(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    archive = Archive(settings.data_dir)
    outcome = collector.collect_day(
        settings, _make_provider(settings), archive, _resolve_date(args.date), force=args.force
    )
    print(f"{outcome.trade_date}: {outcome.status} ({outcome.in_play} in play)")
    if outcome.status == "collected":
        _print_in_play(archive, outcome.trade_date)
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    archive = Archive(settings.data_dir)
    outcomes = collector.backfill(
        settings,
        _make_provider(settings),
        archive,
        _resolve_date(args.start),
        _resolve_date(args.end),
        force=args.force,
    )
    collected = sum(1 for o in outcomes if o.status == "collected")
    failed = [o for o in outcomes if o.status == "failed"]
    print(f"Backfill finished: {collected} sessions collected, {len(failed)} failed")
    for outcome in failed:
        print(f"  FAILED {outcome.trade_date}: {outcome.detail}")
    return 1 if failed else 0


def _cmd_rescan(args: argparse.Namespace) -> int:
    """Re-run the screen against cached data only, printing without writing."""
    settings = _settings_from_args(args)
    archive = Archive(settings.data_dir)
    trade_date = _resolve_date(args.date)

    bars = archive.read_daily_bars(trade_date)
    if not bars:
        print(f"No cached daily bars for {trade_date}. Run `collect --date {trade_date}` first.")
        return 1

    history = load_history(
        None, archive, trade_date, settings.criteria.rvol_lookback_days, allow_fetch=False
    )
    enricher = Enricher(None, archive, settings, news_cache=_archived_news(archive, trade_date))
    result = run_scan(bars, history, settings, enricher, trade_date)
    _print_candidates(result.in_play)
    print(f"\n(offline rescan of {trade_date}; nothing was written)")
    return 0


def _archived_news(archive: Archive, trade_date: date) -> dict[str, tuple[int, str | None]]:
    """Recover headline counts from a previous online scan of the same session.

    Without this an offline rescan cannot judge the catalyst criterion at all,
    and every candidate would come back tagged ``news_unknown``.
    """
    news: dict[str, tuple[int, str | None]] = {}
    for row in archive.read_candidates(trade_date):
        if row.get("news_checked", "").strip().lower() not in ("true", "1"):
            continue
        try:
            count = int(row.get("news_count") or 0)
        except ValueError:
            continue
        news[row["ticker"]] = (count, row.get("news_headline") or None)
    return news


def _cmd_show(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    archive = Archive(settings.data_dir)
    trade_date = _resolve_date(args.date)
    rows = archive.read_in_play(trade_date)
    if not rows:
        print(f"No archived in-play list for {trade_date}")
        return 1
    _print_rows(rows)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    archive = Archive(settings.data_dir)
    dates = archive.collected_dates()
    if not dates:
        print(f"Archive at {archive.root} is empty. Start with `collect` or `backfill`.")
        return 0
    total_rows = sum(len(archive.read_in_play(day)) for day in dates)
    print(f"Archive: {archive.root}")
    print(f"Sessions collected: {len(dates)}  ({dates[0]} -> {dates[-1]})")
    print(f"In-play rows:       {total_rows}  (avg {total_rows / len(dates):.1f} per session)")
    print(f"History file:       {archive.in_play_history_path}")
    return 0


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


def _print_in_play(archive: Archive, trade_date: date) -> None:
    rows = archive.read_in_play(trade_date)
    if rows:
        _print_rows(rows)


def _print_candidates(candidates: Any) -> None:
    _print_rows([candidate.to_row() for candidate in candidates])


def _print_rows(rows: list[dict[str, Any]]) -> None:
    """Print an in-play table to stdout.

    Rows arrive either as ``Candidate`` dicts (typed) or straight from a CSV
    (all strings), so cells are coerced rather than formatted per source.
    """
    header = "".join(label.ljust(width) for _, label, width, _kind in _COLUMNS)
    print(header)
    print("-" * len(header))
    for row in rows:
        line = ""
        for key, _label, width, kind in _COLUMNS:
            value = row.get(key)
            # "0 headlines" and "we never looked" must not print the same.
            if key == "news_count" and not _is_true(row.get("news_checked")):
                value = None
            line += _format_cell(value, kind).ljust(width)
        print(line)


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
    "collect": _cmd_collect,
    "backfill": _cmd_backfill,
    "rescan": _cmd_rescan,
    "show": _cmd_show,
    "status": _cmd_status,
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
    except (ProviderError, ValueError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted; the archive is consistent up to the last completed session")
        return 130


if __name__ == "__main__":
    sys.exit(main())
