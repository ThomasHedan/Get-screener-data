"""Screen the live market on a fixed cadence and publish each result to disk.

This process exists because the GitHub Actions cron that used to drive the
board is best-effort. A scheduled run there can be delayed or dropped with no
signal at all, and for a board whose only data source is that run, a dropped
run means a stale board with no recourse. Observed on 2026-09-10: a correctly
configured, active workflow whose schedule simply never fired, not once.

The loop owns its own clock instead. It ticks on a fixed interval, screens
whenever a US session is live -- pre-market through after-hours -- and skips
the network call entirely when it is not, then publishes each result twice:
``today.json`` for the board to read, and a timestamped copy under
``history/`` so a session can be replayed later.

Failure policy: a scan that raises is retried briefly within the same tick,
and if it still fails the previous ``today.json`` is left exactly where it is.
A board showing a correctly-labelled scan from five minutes ago beats an empty
one, and the staleness notice already in the payload is what tells the reader
which of the two they are looking at.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from warrior_screener import market_calendar
from warrior_screener.config import Settings, load_settings
from warrior_screener.live_snapshot import screen_live
from warrior_screener.providers.tradingview import TradingViewError
from warrior_screener.snapshot_payload import build_payload, staleness_notice

logger = logging.getLogger("warrior_screener.scheduler")

DEFAULT_DATA_DIR = Path("/data")
DEFAULT_INTERVAL_SECONDS = 300

# Two quick retries inside the tick. The point is to ride out a blip in an
# undocumented endpoint without waiting a whole interval -- at 09:35 ET, five
# minutes of staleness is the difference between a usable board and a useless
# one. Anything worse than a blip is left to the next tick.
RETRY_BACKOFF_SECONDS = (5, 15)


def publish(payload: dict[str, Any], data_dir: Path) -> Path:
    """Write ``payload`` as the current board, plus a timestamped history copy.

    ``today.json`` is replaced atomically: the board reads this file on every
    request, and a reader that catches a half-written file would render a
    broken screen rather than a stale one.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2) + "\n"

    current = data_dir / "today.json"
    tmp = current.with_suffix(".json.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, current)

    stamp = datetime.fromisoformat(payload["generated_at"])
    archive = data_dir / "history" / payload["trade_date"]
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"{stamp:%H%M%S}.json").write_text(body, encoding="utf-8")
    return current


def run_once(settings: Settings, data_dir: Path) -> bool:
    """Screen the market now and publish it. True if a scan was published."""
    for attempt, backoff in enumerate((*RETRY_BACKOFF_SECONDS, None), start=1):
        try:
            result = screen_live(settings.criteria)
        except TradingViewError as exc:
            if backoff is None:
                logger.error(
                    "Scan failed after %d attempts (%s); keeping the last board", attempt, exc
                )
                return False
            logger.warning("Scan attempt %d failed (%s); retrying in %ds", attempt, exc, backoff)
            time.sleep(backoff)
            continue

        now_et = datetime.now(market_calendar.EASTERN)
        phase = market_calendar.session_phase(now_et)
        payload = build_payload(
            result, now_et, phase, staleness_notice(now_et, phase), settings.criteria
        )
        publish(payload, data_dir)
        logger.info(
            "Published %s scan: %d in play of %d scanned",
            phase,
            result.stats["in_play"],
            result.stats["universe_rows"],
        )
        return True
    return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    data_dir = Path(os.environ.get("SCREENER_DATA_DIR", DEFAULT_DATA_DIR))
    interval = int(os.environ.get("SCAN_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    settings = load_settings()

    # Compose stops containers with SIGTERM; without this the loop is killed
    # mid-write and the deploy log fills with a stack trace instead of a line
    # saying the process shut down cleanly.
    stopping = False

    def stop(signum: int, _frame: Any) -> None:
        nonlocal stopping
        logger.info("Signal %s received; stopping after this tick", signum)
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    logger.info("Scheduler up: every %ds, publishing to %s", interval, data_dir)

    while not stopping:
        phase = market_calendar.session_phase()
        if phase == "closed":
            logger.debug("Market closed; no scan this tick")
        else:
            try:
                run_once(settings, data_dir)
            except Exception:
                # A tick must never take the loop down: tomorrow's open is more
                # important than this scan, and the process is the scheduler.
                logger.exception("Unexpected error during scan; continuing")

        for _ in range(interval):
            if stopping:
                break
            time.sleep(1)

    logger.info("Scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
