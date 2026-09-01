"""Shared constants for the test suite."""

from datetime import date

TRADE_DATE = date(2026, 8, 28)
PREV_DATE = date(2026, 8, 27)

# A textbook Warrior setup: $4 stock, up 40%, 8M float, 20x volume, news out.
IN_PLAY_TICKER = "GAPR"
# Right profile but a 300M float -- fails only the float test.
BIG_FLOAT_TICKER = "BIGF"
# Up big on no volume relative to normal.
LOW_RVOL_TICKER = "SLOW"
# Too expensive for the screen.
EXPENSIVE_TICKER = "PRCY"
# A warrant, which the grouped feed mixes in with common stock.
WARRANT_TICKER = "GAPRW"

DEFAULT_PREV_CLOSE = 3.00
PRIOR_SESSION_VOLUME = 200_000
PRIOR_SESSION_COUNT = 20
