"""Capture the screen at fixed points in a session, for after-the-fact analysis.

Separate from :mod:`warrior_screener.scheduler` on purpose. The scheduler feeds
the live board and runs forever inside a container; this is a one-session tool
you run by hand when you want to compare the same screen at a few chosen
moments and look at the results yourself.

It exists because TradingView's screener has no notion of a past instant -- it
always answers about the live session, so "what was in play at 09:35" only has
an answer if something captured it at 09:35. This captures it.

    python -m warrior_screener.checkpoints --test    # is the endpoint reachable?
    python -m warrior_screener.checkpoints           # run today's checkpoints
    python -m warrior_screener.checkpoints --now     # capture once, right now

Default checkpoints, in Eastern -- the market's own clock, so they stay correct
across both DST changes rather than drifting when Paris and New York switch on
different dates:

    09:25 ET (15:25 Paris)  pre-open
    09:35 ET (15:35 Paris)  open+5, the gap-and-go window
    10:30 ET (16:30 Paris)  open+60
    16:00 ET (22:00 Paris)  close

Output lands in ``data/checkpoints/<date>/``: the full payload per checkpoint,
plus ``summary.csv`` flattening every in-play row across all of them, which is
the file to open when comparing 09:35 against the close.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time as time_module
from datetime import datetime, time
from pathlib import Path
from typing import Any, NamedTuple

from warrior_screener import market_calendar
from warrior_screener.config import Settings, load_settings
from warrior_screener.live_snapshot import screen_live
from warrior_screener.providers.tradingview import TradingViewError, fetch_market_snapshot
from warrior_screener.snapshot_payload import build_payload, staleness_notice

logger = logging.getLogger("warrior_screener.checkpoints")

DEFAULT_OUTPUT_DIR = Path("data/checkpoints")


class Checkpoint(NamedTuple):
    at: time
    label: str


DEFAULT_CHECKPOINTS = (
    Checkpoint(time(9, 25), "pre-open"),
    Checkpoint(time(9, 35), "open+5"),
    Checkpoint(time(10, 30), "open+60"),
    # 16:00 sharp is the close, so the day's regular-session volume is complete.
    # session_phase calls that moment "after-hours" (the regular session runs up
    # to but not through 16:00) -- the label is cosmetic, the data is the full
    # session. Pass --at to shift it if you would rather capture just before.
    Checkpoint(time(16, 0), "close"),
)

CSV_COLUMNS = (
    "checkpoint",
    "captured_at_et",
    "session_phase",
    "ticker",
    "close",
    "change_pct",
    "gap_pct",
    "relative_volume",
    "volume",
    "float_shares",
    "score",
    "qualification",
)


# ------------------------------------------------------------------ test probe


def probe() -> int:
    """One request to TradingView, reporting what came back. 0 if it worked.

    Run this before relying on a session's captures: the endpoint is
    undocumented and unauthenticated, so the failure modes are a blocked
    network, a changed response shape, or an endpoint that has simply stopped
    answering -- and all three are cheaper to find out about now than at 09:35.
    """
    logger.info("Requesting a full market snapshot...")
    started = time_module.monotonic()
    try:
        rows = fetch_market_snapshot()
    except TradingViewError as exc:
        logger.error("FAILED: %s", exc)
        logger.error("The endpoint is unreachable or refused the request; captures would fail too.")
        return 1

    elapsed = time_module.monotonic() - started
    logger.info("OK: %d tradeable rows in %.1fs", len(rows), elapsed)
    if not rows:
        logger.error(
            "The request worked but returned nothing tradeable -- treat this as a failure."
        )
        return 1

    for row in rows[:3]:
        logger.info(
            "  %-6s close=%-8s vol=%-12s rvol=%-8s float=%s",
            row.ticker,
            row.close,
            row.volume,
            row.relative_volume,
            row.float_shares,
        )
    logger.info("Endpoint healthy.")
    return 0


# ------------------------------------------------------------------ capturing


def capture(settings: Settings, label: str, output_dir: Path) -> dict[str, Any] | None:
    """Screen the market now and write it under ``label``. None if the scan failed."""
    try:
        result = screen_live(settings.criteria)
    except TradingViewError as exc:
        logger.error("[%s] scan failed: %s", label, exc)
        return None

    now_et = datetime.now(market_calendar.EASTERN)
    phase = market_calendar.session_phase(now_et)
    payload = build_payload(
        result, now_et, phase, staleness_notice(now_et, phase), settings.criteria
    )
    payload["checkpoint"] = label

    day_dir = output_dir / now_et.date().isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{now_et:%H%M}_{label}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    logger.info(
        "[%s] %s %s -- %d in play of %d scanned",
        label,
        now_et.strftime("%H:%M ET"),
        phase,
        len(payload["in_play"]),
        payload["stats"]["universe_rows"],
    )
    if payload["notice"]:
        logger.warning("  %s", payload["notice"])
    _log_in_play(payload)
    logger.info("  -> %s", path)

    _append_summary(payload, day_dir / "summary.csv")
    return payload


_ROW_FORMAT = "  %-6s %8s %8s %8s %9s %13s %12s %6s  %s"


def _fmt(value: Any, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value:+.1f}%"
    if kind == "mult":
        # Time-of-day-normalized RVOL runs to four digits just after the open.
        return f"{value:,.0f}x" if value >= 100 else f"{value:.1f}x"
    if kind == "count":
        return f"{int(value):,}"
    return f"{value:.2f}"


def _log_in_play(payload: dict[str, Any]) -> None:
    """Log every in-play name and the figures it qualified on.

    The count on its own is the one number that never tells you whether the
    screen did something sensible. At 09:35 what you actually read is the
    names -- so they go in the log, not only in the file.
    """
    rows = payload["in_play"]
    if not rows:
        logger.info("  (nothing in play: the screen ran and selected no candidate)")
        return

    logger.info(
        _ROW_FORMAT, "TICKER", "CLOSE", "CHG%", "GAP%", "RVOL", "VOLUME", "FLOAT", "SCORE", "QUAL"
    )
    for row in rows:
        logger.info(
            _ROW_FORMAT,
            row["ticker"],
            _fmt(row["close"], "price"),
            _fmt(row["change_pct"], "pct"),
            _fmt(row["gap_pct"], "pct"),
            _fmt(row["relative_volume"], "mult"),
            _fmt(row["volume"], "count"),
            _fmt(row["float_shares"], "count"),
            _fmt(row["score"], "price"),
            row["qualification"],
        )


def _append_summary(payload: dict[str, Any], path: Path) -> None:
    """Flatten this capture's in-play rows into the day's comparison CSV."""
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        if new_file:
            writer.writeheader()
        for row in payload["in_play"]:
            writer.writerow(
                {
                    "checkpoint": payload["checkpoint"],
                    "captured_at_et": payload["generated_at"],
                    "session_phase": payload["session_phase"],
                    **{key: row.get(key) for key in CSV_COLUMNS[3:]},
                }
            )


def wait_until(target: datetime) -> bool:
    """Sleep until ``target``. False if interrupted."""
    while True:
        remaining = (target - datetime.now(market_calendar.EASTERN)).total_seconds()
        if remaining <= 0:
            return True
        if remaining > 60:
            logger.info(
                "Next capture in %d min (at %s ET)", round(remaining / 60), target.strftime("%H:%M")
            )
        try:
            time_module.sleep(min(remaining, 60))
        except KeyboardInterrupt:
            return False


def run_session(settings: Settings, checkpoints: tuple[Checkpoint, ...], output_dir: Path) -> int:
    now_et = datetime.now(market_calendar.EASTERN)
    if not market_calendar.is_trading_day(now_et.date()):
        logger.error("%s is not a trading day; nothing to capture.", now_et.date())
        return 1

    # Probe before settling in to wait. The first checkpoint can be hours out,
    # and finding out then that the endpoint is gone wastes the session.
    logger.info("--- endpoint check ---")
    if probe() != 0:
        logger.warning(
            "Endpoint check failed. Staying up anyway: the first capture may be hours "
            "away and the endpoint may recover by then, and each capture reports its "
            "own failure. Stop with Ctrl-C if you would rather fix it first."
        )
    logger.info("--- checkpoints ---")

    captured = 0
    for checkpoint in checkpoints:
        target = now_et.replace(
            hour=checkpoint.at.hour, minute=checkpoint.at.minute, second=0, microsecond=0
        )
        if target < datetime.now(market_calendar.EASTERN):
            logger.warning("[%s] %s ET already passed; skipping", checkpoint.label, checkpoint.at)
            continue
        if not wait_until(target):
            logger.info("Interrupted; stopping after %d capture(s)", captured)
            return 0
        if capture(settings, checkpoint.label, output_dir) is not None:
            captured += 1

    if not captured:
        logger.error("No checkpoint captured. Started too late in the day?")
        return 1
    logger.info("Done: %d capture(s) in %s", captured, output_dir)
    return 0


# ------------------------------------------------------------------ entry point


def _parse_checkpoints(raw: str) -> tuple[Checkpoint, ...]:
    """Parse "09:25,09:35" into checkpoints labelled by their own time."""
    out = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        hour, _, minute = chunk.partition(":")
        out.append(Checkpoint(time(int(hour), int(minute or 0)), chunk.replace(":", "")))
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the screen at fixed points in a session, for later analysis.",
        epilog="Times are Eastern -- the market's clock. 09:35 ET is 15:35 in Paris.",
    )
    parser.add_argument(
        "--test", action="store_true", help="one request to check the endpoint, then exit"
    )
    parser.add_argument("--now", action="store_true", help="capture once immediately, then exit")
    parser.add_argument("--at", help='checkpoints in ET, e.g. "09:25,09:35,10:30,16:00"')
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if args.test:
        return probe()

    settings = load_settings()
    if args.now:
        return 0 if capture(settings, "now", args.output_dir) else 1

    checkpoints = _parse_checkpoints(args.at) if args.at else DEFAULT_CHECKPOINTS
    logger.info("Checkpoints (ET): %s", ", ".join(f"{c.at:%H:%M} {c.label}" for c in checkpoints))
    return run_session(settings, checkpoints, args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
