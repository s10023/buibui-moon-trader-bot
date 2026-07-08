"""Reference-level gauge: ATR-normalised distances + sweep flags."""

from __future__ import annotations

import pandas as pd

from analytics.brief.types import LevelRow
from analytics.reference_levels import compute_levels

# Extreme levels get sweep flags; sweep of a LOW is a long-side reclaim,
# sweep of a HIGH is a short-side rejection.
_SWEEP_DIRECTION: dict[str, str] = {
    "PDH": "short",
    "PWH": "short",
    "MonH": "short",
    "PDL": "long",
    "PWL": "long",
    "MonL": "long",
}

# Sweep-check window per level: the period the level is ACTIVE as a
# reference, never the period that defines it (a bar cannot sweep the
# level it defines — its own extreme IS the level).
_SWEEP_WINDOW: dict[str, str] = {
    "PDH": "day",
    "PDL": "day",
    "PWH": "week",
    "PWL": "week",
    "MonH": "week_after_monday",
    "MonL": "week_after_monday",
}


def _window_start_ms(as_of_ms: int, kind: str) -> int:
    """UTC-ms start of the sweep window containing ``as_of_ms``."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    day_start = ts.normalize()
    if kind == "day":
        start = day_start
    else:
        week_start = day_start - pd.Timedelta(days=int(day_start.weekday()))
        start = week_start if kind == "week" else week_start + pd.Timedelta(days=1)
    return int(start.value // 1_000_000)


def _swept_current_period(
    daily_df: pd.DataFrame,
    as_of_ms: int,
    name: str,
    level_price: float,
    as_of_price: float,
) -> bool:
    """Did current-period bars pierce ``level_price`` with price now back?

    Windows are sliced from ``daily_df`` (incl. the forming bar). Strict
    inequalities: touching a level is not piercing it.
    """
    direction = _SWEEP_DIRECTION.get(name)
    kind = _SWEEP_WINDOW.get(name)
    if direction is None or kind is None or daily_df.empty:
        return False
    start_ms = _window_start_ms(as_of_ms, kind)
    window = daily_df[
        (daily_df["open_time"] >= start_ms) & (daily_df["open_time"] <= as_of_ms)
    ]
    if window.empty:
        return False
    if direction == "short":
        pierced = bool((window["high"].astype(float) > level_price).any())
        return pierced and as_of_price < level_price
    pierced = bool((window["low"].astype(float) < level_price).any())
    return pierced and as_of_price > level_price


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
    as_of_ms: int,
    ref_price: float,
    atr14: float,
    max_per_side: int,
) -> tuple[list[LevelRow], list[LevelRow]]:
    """(above, below) LevelRows — nearest-first, capped per side.

    ``daily_df`` must include the (possibly forming) bar containing as_of so
    the current-period opens (DO/WO/MO) resolve AND so sweep flags can see
    today's/this week's action. ``dist_atr <= 0`` lands on the below side.
    """
    if atr14 <= 0:
        return [], []
    levels = compute_levels(daily_df, as_of_ms)
    rows: list[LevelRow] = []
    for name, price in levels.items():
        if price is None:
            continue
        dist = (float(price) - ref_price) / atr14
        swept = _swept_current_period(daily_df, as_of_ms, name, float(price), ref_price)
        rows.append(LevelRow(name=name, price=float(price), dist_atr=dist, swept=swept))
    above = sorted((r for r in rows if r.dist_atr > 0), key=lambda r: r.dist_atr)
    below = sorted((r for r in rows if r.dist_atr <= 0), key=lambda r: -r.dist_atr)
    return above[:max_per_side], below[:max_per_side]
