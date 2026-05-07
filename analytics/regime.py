"""Regime classifier — §6 of docs/redesign/buibui-redesign.md.

Labels each candle on a (symbol, timeframe) series as `trend`, `range`, or
`high_vol`. The §6 priority is high_vol > trend > range; bars without enough
history to compute either signal are labelled `unknown`.

Used by Phase 0 strategy edge audit (per-trade regime slicing) and intended
to land as the regime gate in Phase 2 of the redesign.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from analytics.strategies._shared import compute_ema

Regime = Literal["trend", "range", "high_vol", "unknown"]

_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

_SLOPE_LOOKBACK = 10
_SLOPE_TREND_THRESHOLD = 0.005
_ATR_PERIOD = 14
_ATR_PERCENTILE = 0.80
_ATR_HISTORY_DAYS = 90
_MIN_HISTORY_DAYS = 7


def _atr_wilder(df: pd.DataFrame, period: int = _ATR_PERIOD) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def classify_series(df: pd.DataFrame, timeframe: str) -> pd.Series:
    """Return a regime label per row.

    df must be sorted ascending by `open_time` and contain `high`, `low`, `close`.
    Returns a string series aligned with df.index.
    """
    bars_per_day = _BARS_PER_DAY.get(timeframe)
    if bars_per_day is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    history_window = bars_per_day * _ATR_HISTORY_DAYS
    min_history = max(50, bars_per_day * _MIN_HISTORY_DAYS)

    close = df["close"].astype(float)
    ema50 = compute_ema(close, 50)
    slope = (ema50 - ema50.shift(_SLOPE_LOOKBACK)) / ema50.shift(_SLOPE_LOOKBACK)

    atr = _atr_wilder(df)
    atr_pct = atr / close
    atr_p80 = atr_pct.rolling(window=history_window, min_periods=min_history).quantile(
        _ATR_PERCENTILE
    )

    regime = pd.Series("range", index=df.index, dtype="object")
    regime[slope.abs() >= _SLOPE_TREND_THRESHOLD] = "trend"
    regime[atr_pct >= atr_p80] = "high_vol"
    regime[atr_pct.isna() | atr_p80.isna() | slope.isna()] = "unknown"
    return regime
