"""Fixtures for the screener tests.

The whole suite runs with no network and no API key: the live screen's only
input is a list of TradingView snapshot rows, which the tests build directly
(see ``tests/test_live_snapshot.make_row``).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from warrior_screener.config import Criteria


@pytest.fixture
def criteria() -> Criteria:
    """Default Warrior criteria, with the min_in_play padding switched off."""
    return replace(Criteria(), fill_to_min=False)
