"""Find out which TradingView columns exist, and which are actually populated.

The scanner endpoint is undocumented: there is no schema to read, and it
rejects the whole request on an unknown column name. EXTENDED_COLUMNS is
therefore a considered guess, and this settles it.

    python -m warrior_screener.probe_columns

Two questions, not one. "Accepted" only means the name exists; a column that is
accepted but empty for every microcap still costs storage on every row forever.
So fill rates are measured over the names the screen actually looks at -- the
configured price band -- rather than over the mega-caps that top the volume
sort and have every fundamental filled in.

One request when nothing is wrong. Only if the endpoint refuses the full list
does it fall back to probing names one at a time to find the culprits.
"""

from __future__ import annotations

import sys

import requests

from warrior_screener.config import load_criteria
from warrior_screener.providers.tradingview import (
    CORE_COLUMNS,
    EXTENDED_COLUMNS,
    SCAN_URL,
    TradingViewError,
    _post_with_retries,
)

# Below this share of the sampled universe a column is not worth its bytes.
USEFUL_FILL_PCT = 5.0

# Under this many rows in the price band, a fill rate is noise rather than
# evidence -- one absent value moves it by whole percentage points.
MIN_SAMPLE = 100


def _fetch(http: requests.Session, columns: tuple[str, ...]) -> list[list]:
    """One page of the market with ``columns``. Raises if the endpoint refuses."""
    payload = _post_with_retries(http, 0, 30.0, 0, columns)
    return [entry["d"] for entry in (payload.get("data") or []) if entry.get("d")]


def _accepted_columns(http: requests.Session) -> tuple[list[str], list[str]]:
    """Split EXTENDED_COLUMNS into accepted and rejected, cheaply when possible."""
    try:
        _fetch(http, (*CORE_COLUMNS, *EXTENDED_COLUMNS))
    except TradingViewError:
        pass
    else:
        return list(EXTENDED_COLUMNS), []

    print("The full list was refused; probing one name at a time.\n", file=sys.stderr)
    good, bad = [], []
    for column in EXTENDED_COLUMNS:
        try:
            _fetch(http, (*CORE_COLUMNS, column))
        except TradingViewError:
            bad.append(column)
        else:
            good.append(column)
    return good, bad


def main() -> int:
    http = requests.Session()
    criteria = load_criteria()

    # Without this, an unreachable endpoint reports every column as missing and
    # the output reads as "delete all of them".
    try:
        _fetch(http, CORE_COLUMNS)
    except TradingViewError as exc:
        print(
            f"{SCAN_URL} is unreachable ({exc}), so every column would look broken. "
            "Fix the connection first -- these results would be meaningless.",
            file=sys.stderr,
        )
        return 2

    accepted, rejected = _accepted_columns(http)
    columns = (*CORE_COLUMNS, *accepted)
    rows = _fetch(http, columns)

    # Fill rates over the screen's own universe, not the whole market: a column
    # that is populated only for mega-caps is useless to this screen.
    close_at = columns.index("close")
    sample = [
        r for r in rows if r[close_at] and criteria.min_price <= r[close_at] <= criteria.max_price
    ]
    if not sample:
        print(
            "No row in the configured price band came back; cannot measure fill.", file=sys.stderr
        )
        return 1

    fill = {
        name: 100.0 * sum(1 for r in sample if r[i] is not None) / len(sample)
        for i, name in enumerate(columns)
        if name in accepted
    }
    keep = sorted((c for c in accepted if fill[c] >= USEFUL_FILL_PCT), key=lambda c: -fill[c])
    empty = [c for c in accepted if fill[c] < USEFUL_FILL_PCT]

    print(
        f"{len(rows)} rows fetched, {len(sample)} in the "
        f"${criteria.min_price:g}-${criteria.max_price:g} band the screen uses.\n"
    )
    print(f"POPULATED ({len(keep)}) -- worth storing")
    for column in keep:
        print(f"  {fill[column]:5.1f}%  {column}")
    if empty:
        print(f"\nACCEPTED BUT EMPTY ({len(empty)}) -- drop, they cost bytes on every row")
        for column in sorted(empty, key=lambda c: -fill[c]):
            print(f"  {fill[column]:5.1f}%  {column}")
    if rejected:
        print(f"\nREJECTED ({len(rejected)}) -- these names do not exist")
        for column in rejected:
            print(f"         {column}")

    print("\nEXTENDED_COLUMNS = (")
    for column in keep:
        print(f'    "{column}",')
    print(")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
