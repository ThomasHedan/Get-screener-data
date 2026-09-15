"""Find out which TradingView columns actually exist.

The scanner endpoint is undocumented: there is no schema to read, and it
rejects the whole request on an unknown column name. EXTENDED_COLUMNS is
therefore a considered guess, and this tells you which parts of it are real.

    python -m warrior_screener.probe_columns

Each candidate is asked for on its own, over a one-row range, so a bad name
costs one tiny request and nothing else. Run it once, paste the surviving list
into EXTENDED_COLUMNS, and the scan stops paying the fallback penalty.
"""

from __future__ import annotations

import logging
import sys

import requests

from warrior_screener.providers.tradingview import (
    CORE_COLUMNS,
    EXTENDED_COLUMNS,
    SCAN_URL,
    TradingViewError,
    _post_with_retries,
)

logger = logging.getLogger("warrior_screener.probe_columns")


def probe(column: str, http: requests.Session) -> bool:
    """True if the endpoint accepts ``column`` alongside the core set."""
    try:
        payload = _post_with_retries(http, 0, 15.0, 0, (*CORE_COLUMNS, column))
    except TradingViewError:
        return False
    # A name can be accepted and still return nothing useful; both count as a
    # column worth keeping out of the list only if the request itself failed.
    return bool(payload.get("data") is not None)


def main() -> int:
    logging.basicConfig(level=logging.ERROR, format="%(message)s")
    http = requests.Session()

    # Without this, an unreachable endpoint reports every column as BAD and the
    # output reads as "delete all 51" -- a broken network must not look like a
    # bad column list.
    if not probe(CORE_COLUMNS[0], http):
        print(
            f"The core column set is refused too, so {SCAN_URL} is unreachable or "
            "blocked from here. Results would be meaningless; fix the connection first.",
            file=sys.stderr,
        )
        return 2

    print(f"Probing {len(EXTENDED_COLUMNS)} candidate columns against {SCAN_URL}\n")
    good: list[str] = []
    bad: list[str] = []
    for column in EXTENDED_COLUMNS:
        ok = probe(column, http)
        (good if ok else bad).append(column)
        print(f"  {'ok  ' if ok else 'BAD '} {column}")

    print(f"\n{len(good)} accepted, {len(bad)} rejected.")
    if bad:
        print("\nRejected — remove these from EXTENDED_COLUMNS:")
        print("  " + ", ".join(bad))
    print("\nEXTENDED_COLUMNS = (")
    for column in good:
        print(f'    "{column}",')
    print(")")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
