"""Carver Instrument Diversification Multiplier for the two-sleeve combine.

Pure math, no DB/IO. IDM = 1/√(wᵀ ρ w) scales a diversified combination back up to
the vol target; capped (Carver uses 2.5). `static_idm` uses one full-sample
correlation (a reported sensitivity — mild look-ahead); `causal_idm_series`
estimates the correlation on a trailing window through `d-1` (the headline,
no-look-ahead path).
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
import pandas as pd


def idm_value(w_xs: float, w_trend: float, corr: float, cap: float) -> float:
    """1/√(wᵀρw) for two sleeves, capped at `cap`.

    `var = w_xs² + w_trend² + 2·w_xs·w_trend·corr`. A non-positive `var` (e.g.
    corr ≈ −1 with equal weights) means infinite scale-up — return `cap`.
    """
    var = w_xs**2 + w_trend**2 + 2.0 * w_xs * w_trend * corr
    if var <= 0.0:
        return cap
    return min(math.sqrt(1.0 / var), cap)


def _joint_live_corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0).

    Mirrors `analytics.xsmom.report._aligned_corr`. Degenerate (n<2 or zero
    variance) -> 0.0.
    """
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    x = np.asarray(a[-n:], dtype=np.float64)
    y = np.asarray(b[-n:], dtype=np.float64)
    live = ~((x == 0.0) & (y == 0.0))
    x, y = x[live], y[live]
    if (
        len(x) < 2
        or float(np.std(x, ddof=1)) < 1e-12
        or float(np.std(y, ddof=1)) < 1e-12
    ):
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def static_idm(
    r_xs: npt.ArrayLike,
    r_trend: npt.ArrayLike,
    w_xs: float,
    w_trend: float,
    cap: float,
) -> float:
    """One constant IDM from the full-sample joint-live correlation.

    A reported sensitivity only — uses future data to size early periods (a mild
    look-ahead leak), never the headline.
    """
    a = np.asarray(r_xs, dtype=np.float64)
    b = np.asarray(r_trend, dtype=np.float64)
    return idm_value(w_xs, w_trend, _joint_live_corr(a, b), cap)


def causal_idm_series(
    r_xs: npt.ArrayLike,
    r_trend: npt.ArrayLike,
    w_xs: float,
    w_trend: float,
    window: int,
    min_periods: int,
    cap: float,
    index: pd.DatetimeIndex,
) -> pd.Series:
    """Per-day IDM from a trailing-window correlation, shifted to be causal.

    The correlation each day is over the trailing `window` of joint-live returns;
    the resulting IDM is `.shift(1)` so the position on day `d` uses correlation
    through `d-1`. Before `min_periods` of trailing live data the IDM is the
    neutral 1.0 (the combined return there is ~0 anyway). Joint warm-up rows
    (both returns exactly 0.0) are masked out so they do not pollute the corr.
    """
    idx = pd.DatetimeIndex(index)
    s_xs = pd.Series(np.asarray(r_xs, dtype=np.float64), index=idx)
    s_tr = pd.Series(np.asarray(r_trend, dtype=np.float64), index=idx)
    live = ~((s_xs == 0.0) & (s_tr == 0.0))
    xm = s_xs.where(live)
    tm = s_tr.where(live)
    roll = xm.rolling(window, min_periods=min_periods).corr(tm)

    idm: pd.Series = pd.Series(1.0, index=idx)
    valid = roll.notna()
    idm.loc[valid] = roll.loc[valid].map(
        lambda c: idm_value(w_xs, w_trend, float(c), cap)
    )
    return idm.shift(1).fillna(1.0)
