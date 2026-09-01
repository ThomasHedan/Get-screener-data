"""A small token-bucket limiter for provider rate limits.

Free market-data tiers are metered in requests per minute, and blowing through
that budget mid-scan corrupts a day's snapshot. Pacing locally is cheaper than
retrying after a 429.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """Blocks callers so that at most ``rpm`` requests start in any 60s window."""

    def __init__(self, requests_per_minute: int, *, window_seconds: float = 60.0) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be >= 1")
        self._rpm = requests_per_minute
        self._window = window_seconds
        self._starts: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Wait until a request slot is free. Returns seconds spent waiting."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                while self._starts and now - self._starts[0] >= self._window:
                    self._starts.popleft()
                if len(self._starts) < self._rpm:
                    self._starts.append(now)
                    return waited
                sleep_for = self._window - (now - self._starts[0])
            sleep_for = max(sleep_for, 0.01)
            logger.debug("Rate limit reached, sleeping %.1fs", sleep_for)
            time.sleep(sleep_for)
            waited += sleep_for
