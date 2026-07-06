"""Shared brief helpers: timeframe math, completed-bar filter, as-of parsing."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

TF_MS: dict[str, int] = {
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

DAY_MS = 86_400_000

_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class BriefDataError(Exception):
    """A symbol cannot produce a panel (missing / insufficient OHLCV)."""


def completed_bars(df: pd.DataFrame, timeframe: str, as_of_ms: int) -> pd.DataFrame:
    """Rows whose bar fully closed at or before ``as_of_ms``.

    A bar is completed iff ``open_time + tf_ms <= as_of_ms`` (the boundary
    counts as completed). The local sync keeps the still-forming candle in
    ``ohlcv``; every brief consumer must go through this filter except
    ``reference_levels.compute_levels`` (which needs the forming bar's open).
    """
    if df.empty:
        return df
    tf_ms = TF_MS[timeframe]
    out = df[df["open_time"] + tf_ms <= as_of_ms]
    return out.reset_index(drop=True)


def parse_as_of_ms(value: str) -> int:
    """ISO8601 string to Unix ms; naive datetimes are UTC; trailing 'Z' ok."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def day_ahead_dow(as_of_ms: int) -> str:
    """Short weekday name of the UTC day containing as_of (locale-safe)."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    return _DOW[int(ts.weekday())]


def day_ahead_label(as_of_ms: int) -> str:
    """e.g. "Fri 2026-07-04" — the day being prepped."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    return f"{_DOW[int(ts.weekday())]} {ts.strftime('%Y-%m-%d')}"
