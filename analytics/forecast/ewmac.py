"""EWMAC forecast math — raw crossover, vol normalisation, scaling, combination.

Pure functions over a price `pd.Series`. No DB, no IO. Forecasts are continuous
and vol-normalised so a long-run average absolute value of ~10 holds across
instruments (Carver convention); capped to +/-20 before sizing.
"""

from __future__ import annotations

import pandas as pd

from analytics.forecast.vol import price_vol


def raw_ewmac(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """Fast EMA minus slow EMA (price units)."""
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    return fast_ema - slow_ema


def scaled_forecast(
    close: pd.Series,
    fast: int,
    slow: int,
    scalar: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Vol-normalised, scalar-adjusted, capped single-speed forecast."""
    raw = raw_ewmac(close, fast, slow)
    pv = price_vol(close, vol_span)
    vol_adj = raw / pv
    return (vol_adj * scalar).clip(lower=-cap, upper=cap)


def combine_forecasts(
    close: pd.Series,
    speeds: tuple[tuple[int, int, float], ...],
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Equal-weight mean of per-speed forecasts x FDM, re-capped to +/-cap."""
    parts = [
        scaled_forecast(close, fast, slow, scalar, vol_span, cap)
        for fast, slow, scalar in speeds
    ]
    stacked = pd.concat(parts, axis=1)
    mean = stacked.mean(axis=1)
    return (mean * fdm).clip(lower=-cap, upper=cap)
