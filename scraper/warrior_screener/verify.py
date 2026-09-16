"""Check TradingView's numbers against themselves, and against your own chart.

Every derived metric in this project rests on assumptions about what
TradingView's columns *mean*, and the scanner is undocumented, so none of them
can be confirmed by reading a specification. The load-bearing one:

    ``open`` is the **regular-session** open (the 09:30 print).

The whole intraday-momentum idea depends on it. ``(close - open) / open`` at
the 09:35 capture is the first five minutes only if ``open`` is the 09:30 open;
if it were the pre-market open, the same figure would silently be "the move
since 04:00" instead, and the screen would be selecting something else entirely
than what it claims.

So this prints, for the biggest movers, what TradingView returns next to what
this code derives from it -- meant to be run during a live session and compared
against the chart in front of you. It also flags rows that contradict
themselves (a close outside the day's range, a change that does not reconcile
with open and the reconstructed previous close), which is the part that needs
no chart to judge.

    python -m warrior_screener.verify
"""

from __future__ import annotations

import sys

from warrior_screener.config import load_criteria
from warrior_screener.live_snapshot import _range_position
from warrior_screener.market_calendar import EASTERN, session_phase
from warrior_screener.providers.tradingview import (
    MarketSnapshotRow,
    TradingViewError,
    fetch_market_snapshot,
)

TOP_N = 15


def _inconsistencies(row: MarketSnapshotRow) -> list[str]:
    """Ways this row contradicts itself, judged without any outside reference."""
    problems: list[str] = []
    if row.close <= 0:
        problems.append("close <= 0")
    if row.high < row.low:
        problems.append("high < low")
    if not (row.low <= row.close <= row.high):
        problems.append(f"close {row.close} outside range {row.low}-{row.high}")
    if row.open and not (row.low <= row.open <= row.high):
        problems.append(f"open {row.open} outside range {row.low}-{row.high}")
    if row.volume < 0:
        problems.append("negative volume")
    return problems


def main() -> int:
    from datetime import datetime

    now = datetime.now(EASTERN)
    phase = session_phase(now)
    print(f"{now:%Y-%m-%d %H:%M:%S %Z}  session: {phase}\n")
    if phase == "closed":
        print("Market closed -- these are the last session's figures, and `open`")
        print("cannot be told apart from a stale value. Re-run during a session.\n")

    try:
        rows = fetch_market_snapshot()
    except TradingViewError as exc:
        print(f"Cannot reach TradingView: {exc}", file=sys.stderr)
        return 2

    criteria = load_criteria()
    in_band = [r for r in rows if criteria.min_price <= r.close <= criteria.max_price]
    movers = sorted(in_band, key=lambda r: r.change_pct, reverse=True)[:TOP_N]

    print(
        f"{len(rows)} rows, {len(in_band)} within "
        f"${criteria.min_price:g}-${criteria.max_price:g}. Top {len(movers)} movers:\n"
    )
    print("Compare `since open` against the 5-minute candles on your own chart.\n")
    header = (
        f"{'sym':<7}{'open':>9}{'high':>9}{'low':>9}{'last':>9}"
        f"{'chg%':>8}{'since open%':>13}{'range pos':>11}{'rvol':>9}"
    )
    print(header)
    print("-" * len(header))
    for row in movers:
        open_change = (row.close - row.open) / row.open * 100.0 if row.open else None
        position = _range_position(row)
        print(
            f"{row.ticker:<7}{row.open:>9.2f}{row.high:>9.2f}{row.low:>9.2f}{row.close:>9.2f}"
            f"{row.change_pct:>8.2f}"
            f"{'  n/a (pre-open)' if open_change is None else f'{open_change:>13.2f}'}"
            f"{'        n/a' if position is None else f'{position:>11.2f}'}"
            f"{row.relative_volume or 0:>9.1f}"
        )

    flagged = [(r.ticker, p) for r in rows if (p := _inconsistencies(r))]
    print(f"\nSelf-consistency over all {len(rows)} rows: ", end="")
    if not flagged:
        print("no contradictions.")
    else:
        print(f"{len(flagged)} row(s) contradict themselves")
        for ticker, problems in flagged[:10]:
            print(f"  {ticker}: {'; '.join(problems)}")

    print("\nWhat to check on the chart, at 09:35 ET:")
    print("  * `open` should equal the 09:30 candle's open, not the pre-market open.")
    print("  * `since open%` should equal the move across the 09:30-09:35 candle.")
    print("  * `chg%` should be larger than `since open%` by roughly the gap.")
    print("If `open` turns out to be the pre-market open, say so -- open_change_pct")
    print("then means something different and the screen has to be rewritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
