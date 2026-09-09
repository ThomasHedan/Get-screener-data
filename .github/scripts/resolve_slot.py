"""Emit this run's slot decision as GitHub Actions step outputs.

Thin wrapper: the logic and its tests live in warrior_screener.slots, because
the daylight-saving handling is exactly the kind of thing that breaks silently
twice a year and a YAML `run:` block cannot be tested.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# `python .github/scripts/resolve_slot.py` puts *this* directory on sys.path,
# not the repository root, so warrior_screener would not import. Prepend the
# root explicitly rather than depending on the caller's PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from warrior_screener.market_calendar import EASTERN  # noqa: E402
from warrior_screener.slots import resolve  # noqa: E402


def main() -> None:
    schedule = os.environ.get("SCHEDULE") or None
    if os.environ.get("EVENT") != "schedule":
        schedule = None  # a manual run reports no cron expression

    now_et = datetime.now(EASTERN)
    decision = resolve(
        schedule,
        now_et,
        manual_slot=os.environ.get("MANUAL_SLOT") or None,
        manual_board=os.environ.get("MANUAL_BOARD", "true") == "true",
    )

    for key, value in {
        "run": "true" if decision.run else "false",
        "slot": decision.slot,
        "refresh_board": "true" if decision.refresh_board else "false",
        "why": decision.why,
        "trade_date": now_et.strftime("%Y-%m-%d"),
        "day_path": now_et.strftime("%Y/%m/%d"),
    }.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
