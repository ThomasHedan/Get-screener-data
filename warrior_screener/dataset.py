"""Pandas loaders for the archive -- the research-side entry point.

Kept in its own module so the collector never imports pandas: the cron job must
keep running even if the science environment is mid-upgrade.

    from warrior_screener.dataset import load_features, load_intraday

    features = load_features("data")                 # one row per ticker per day
    bars = load_intraday("data", "2026-08-28", "ABCD")
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def _require_pandas():  # noqa: ANN202 - the return type is pandas itself
    """Import pandas with an actionable error if the research extra is missing."""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "warrior_screener.dataset needs pandas. Install it with "
            "`pip install 'warrior-screener[research]'` or `pip install pandas`."
        ) from exc
    return pd


def load_in_play_history(data_dir: Path | str = "data"):
    """Load every in-play selection ever made, as one DataFrame.

    This is the survivorship-free spine of the dataset: tickers that have since
    been delisted, renamed or acquired stay in it, because it is only ever
    appended to.
    """
    pd = _require_pandas()
    path = Path(data_dir) / "in_play_history.csv"
    if not path.exists():
        raise FileNotFoundError(f"No in-play history at {path}; run a collection first.")
    frame = pd.read_csv(path, parse_dates=["trade_date"])
    return frame.sort_values(["trade_date", "score"], ascending=[True, False])


def load_features(
    data_dir: Path | str = "data",
    *,
    start: date | str | None = None,
    end: date | str | None = None,
    strict_only: bool = False,
):
    """Load the per-day intraday feature rows for the in-play names.

    Args:
        data_dir: Archive root.
        start: Optional first trade date (inclusive).
        end: Optional last trade date (inclusive).
        strict_only: Drop rows selected under relaxed criteria on quiet days.
    """
    pd = _require_pandas()
    paths = sorted((Path(data_dir) / "features").glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No feature files under {Path(data_dir) / 'features'}")

    frames = [pd.read_csv(path) for path in paths]
    frame = pd.concat(frames, ignore_index=True)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])

    if start is not None:
        frame = frame[frame["trade_date"] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame["trade_date"] <= pd.Timestamp(end)]
    if strict_only:
        frame = frame[frame["qualification"] == "strict"]
    ordered = frame.sort_values(["trade_date", "score"], ascending=[True, False])
    return ordered.reset_index(drop=True)


def load_intraday(data_dir: Path | str, trade_date: date | str, ticker: str):
    """Load one ticker's 1-minute bars for one session, indexed by timestamp."""
    pd = _require_pandas()
    day = trade_date if isinstance(trade_date, str) else trade_date.isoformat()
    path = Path(data_dir) / "intraday" / day / f"{ticker}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No intraday bars at {path}")
    frame = pd.read_csv(path, parse_dates=["timestamp"])
    return frame.set_index("timestamp").sort_index()


def load_intraday_panel(data_dir: Path | str, trade_dates: Iterable[date | str] | None = None):
    """Load every archived minute bar into one long DataFrame.

    Adds ``ticker`` and ``trade_date`` columns. Sized for a few years of 5-10
    names a day (a few million rows); for a larger archive, iterate sessions
    with :func:`load_intraday` instead.
    """
    pd = _require_pandas()
    root = Path(data_dir) / "intraday"
    wanted = (
        {d if isinstance(d, str) else d.isoformat() for d in trade_dates}
        if trade_dates is not None
        else None
    )

    frames = []
    for day_dir in sorted(root.glob("*")):
        if not day_dir.is_dir() or (wanted is not None and day_dir.name not in wanted):
            continue
        for path in sorted(day_dir.glob("*.csv")):
            frame = pd.read_csv(path, parse_dates=["timestamp"])
            if frame.empty:
                continue
            frame["ticker"] = path.stem
            frame["trade_date"] = day_dir.name
            frames.append(frame)

    if not frames:
        raise FileNotFoundError(f"No intraday bars under {root}")
    panel = pd.concat(frames, ignore_index=True)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    return panel.sort_values(["trade_date", "ticker", "timestamp"]).reset_index(drop=True)
