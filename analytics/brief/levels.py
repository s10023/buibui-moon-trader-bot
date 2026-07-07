"""Reference-level gauge: ATR-normalised distances + sweep flags."""

from __future__ import annotations

import pandas as pd

from analytics.brief.types import LevelRow
from analytics.reference_levels import compute_levels, sweep_flag

# Extreme levels get sweep flags; sweep of a LOW is a long-side reclaim,
# sweep of a HIGH is a short-side rejection (reference_levels convention).
_SWEEP_DIRECTION: dict[str, str] = {
    "PDH": "short",
    "PWH": "short",
    "MonH": "short",
    "PDL": "long",
    "PWL": "long",
    "MonL": "long",
}


def atr14_wilder(df: pd.DataFrame, period: int = 14) -> float:
    """Last Wilder ATR value over the frame (0.0 when fewer than 2 rows).

    Same TR + ``ewm(alpha=1/period)`` formula as ``analytics/regime.py`` (that
    helper is private, so the brief carries its own copy).
    """
    if len(df) < 2:
        return 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return float(atr.iloc[-1])


def adr_pct_14(completed_1d: pd.DataFrame) -> float | None:
    """Mean (high-low)/open over the last 14 completed 1d bars."""
    if completed_1d.empty:
        return None
    tail = completed_1d.tail(14)
    opens = tail["open"].astype(float)
    valid = opens > 0
    if not bool(valid.any()):
        return None
    highs = tail["high"].astype(float)
    lows = tail["low"].astype(float)
    ranges = (highs - lows)[valid] / opens[valid]
    return float(ranges.mean())


def build_level_rows(
    daily_df: pd.DataFrame,
    completed_1d: pd.DataFrame,
    as_of_ms: int,
    ref_close: float,
    atr14: float,
    max_per_side: int,
) -> tuple[list[LevelRow], list[LevelRow]]:
    """(above, below) LevelRows — nearest-first, capped per side.

    ``daily_df`` must include the (possibly forming) bar containing as_of so
    the current-period opens (DO/WO/MO) resolve; ``completed_1d`` drives the
    sweep flags (no look-ahead). ``dist_atr <= 0`` lands on the below side.
    """
    if atr14 <= 0:
        return [], []
    levels = compute_levels(daily_df, as_of_ms)
    entry_idx = len(completed_1d) - 1
    rows: list[LevelRow] = []
    for name, price in levels.items():
        if price is None:
            continue
        dist = (float(price) - ref_close) / atr14
        swept = False
        direction = _SWEEP_DIRECTION.get(name)
        if direction is not None and entry_idx >= 0:
            swept = sweep_flag(completed_1d, entry_idx, float(price), direction)
        rows.append(LevelRow(name=name, price=float(price), dist_atr=dist, swept=swept))
    above = sorted((r for r in rows if r.dist_atr > 0), key=lambda r: r.dist_atr)
    below = sorted((r for r in rows if r.dist_atr <= 0), key=lambda r: -r.dist_atr)
    return above[:max_per_side], below[:max_per_side]
