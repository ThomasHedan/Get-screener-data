"""Warrior-Trading-style momentum screener and intraday data collector.

Screens the US equity market for the handful of low-float momentum stocks that
are "in play" on a given session (the Ross Cameron / Warrior Trading gap-and-go
profile), then archives an immutable per-day snapshot of the scan plus 1-minute
intraday bars for the selected names.

The archive is append-only and keyed by trade date, so tickers that are later
delisted, renamed or removed from the provider's active universe stay in the
dataset -- which is what makes the history usable for survivorship-bias-free
quant research.
"""

__version__ = "0.1.0"
