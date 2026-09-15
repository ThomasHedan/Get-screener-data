"""Screen the live market on a fixed cadence and record every capture.

The service owns its own clock. An earlier version of this project drove the
scan from a GitHub Actions cron, which is best-effort: on 2026-09-10 an active,
correctly configured schedule never fired once and the board silently served
the previous day. A loop that ticks on its own has no such failure mode.

It screens only while a US session is live -- pre-market through after-hours,
trading days only -- so an idle overnight tick costs nothing and makes no
network call.

Failure policy: a scan that raises is retried briefly inside the tick, and if
it still fails nothing is written. The board then keeps showing the previous
capture, correctly timestamped, rather than going blank; the staleness notice
recorded with every scan is what tells the reader which they are looking at.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Any

from warrior_screener import db, market_calendar
from warrior_screener.config import Criteria, load_criteria
from warrior_screener.live_snapshot import screen_live
from warrior_screener.providers.tradingview import TradingViewError

logger = logging.getLogger("warrior_screener.scraper")

DEFAULT_INTERVAL_SECONDS = 300

# Times-of-day (ET) that are always captured, whatever the interval. The open
# drive is decided in the first minutes and 09:31 is not reachable from a
# 5-minute grid: 09:30 is the bell itself, 09:35 is already four minutes late.
DEFAULT_SCAN_AT = "09:31,09:35"

# Two quick retries inside the tick. The point is to ride out a blip in an
# undocumented endpoint without waiting a whole interval: five minutes of
# staleness just after the open is the difference between a usable board and a
# useless one. Anything worse than a blip is left to the next tick.
RETRY_BACKOFF_SECONDS = (5, 15)


def capture(conn: Any, criteria: Criteria, *, store_market: bool = True) -> int | None:
    """Screen the market now and record it. Returns the scan id, or None.

    ``store_market`` False keeps the board fresh while skipping the corpus row
    for this tick -- the lever for trading disk against training resolution.
    """
    for attempt, backoff in enumerate((*RETRY_BACKOFF_SECONDS, None), start=1):
        try:
            result = screen_live(criteria)
            break
        except TradingViewError as exc:
            if backoff is None:
                logger.error("Scan failed after %d attempts (%s); nothing written", attempt, exc)
                return None
            logger.warning("Scan attempt %d failed (%s); retrying in %ds", attempt, exc, backoff)
            time.sleep(backoff)
    else:  # pragma: no cover - the loop always breaks or returns
        return None

    now_et = datetime.now(market_calendar.EASTERN)
    phase = market_calendar.session_phase(now_et)
    scan_id = db.save_scan(
        conn,
        captured_at=now_et,
        trade_date=result.trade_date,
        phase=phase,
        universe_rows=result.stats["universe_rows"],
        notice=market_calendar.staleness_notice(now_et, phase),
        candidates=result.in_play,
        market=result.market if store_market else (),
    )
    logger.info(
        "scan %d | %s %s | %d in play of %d %s | %s",
        scan_id,
        now_et.strftime("%H:%M ET"),
        phase,
        len(result.in_play),
        result.stats["universe_rows"],
        "stored" if store_market else "scanned (corpus skipped)",
        ", ".join(c.ticker for c in result.in_play) or "nothing selected",
    )
    return scan_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="capture once and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    interval = int(os.environ.get("SCAN_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    # Store the whole market every Nth capture. 1 keeps everything (~870 MB a
    # day); raising it trades training resolution for disk without making the
    # board any staler, since the board is written on every tick regardless.
    market_every = max(1, int(os.environ.get("STORE_MARKET_EVERY", 1)))
    pinned = market_calendar.parse_times(os.environ.get("SCAN_AT", DEFAULT_SCAN_AT))
    criteria = load_criteria()

    with db.connect() as conn:
        if args.once:
            return 0 if capture(conn, criteria) else 1

        # Compose stops containers with SIGTERM. Without this the loop dies
        # mid-tick and the deploy log gets a stack trace instead of a line
        # saying the service shut down cleanly.
        stopping = False

        def stop(signum: int, _frame: Any) -> None:
            nonlocal stopping
            logger.info("signal %s received; stopping after this tick", signum)
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        logger.info(
            "scraper up: every %ds on the clock (always at %s ET) while a US session is "
            "live, corpus every %d capture(s)",
            interval,
            ", ".join(moment.strftime("%H:%M") for moment in pinned) or "no pinned time",
            market_every,
        )

        tick = 0
        while not stopping:
            if market_calendar.session_phase() == "closed":
                logger.debug("market closed; no scan this tick")
            else:
                try:
                    capture(conn, criteria, store_market=tick % market_every == 0)
                    tick += 1
                except Exception:
                    # A tick must never take the loop down: tomorrow's open
                    # matters more than this scan, and this process is the clock.
                    logger.exception("unexpected error during capture; continuing")
            # Wait for a wall-clock moment rather than a duration, so a slow
            # scan cannot push every later capture off its mark.
            wake_at = market_calendar.next_capture(
                datetime.now(market_calendar.EASTERN), interval, pinned
            )
            logger.debug("next capture at %s", wake_at.strftime("%H:%M:%S %Z"))
            while not stopping and datetime.now(market_calendar.EASTERN) < wake_at:
                time.sleep(1)

    logger.info("scraper stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
