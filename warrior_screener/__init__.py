"""Warrior-Trading-style live momentum screener.

Screens the whole US equity market for the handful of low-float momentum
stocks that are "in play" right now -- the Ross Cameron / Warrior Trading
gap-and-go profile -- from a single keyless TradingView request, and writes
the result as JSON for the Warrior Trading Screener dashboard.

The screen itself lives in :mod:`warrior_screener.scanner` and is pure: the
filters, the composite score and the strict/relaxed selection are the same
code regardless of where the candidates came from.
"""

__version__ = "0.2.0"
