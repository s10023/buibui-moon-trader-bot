"""Cross-sectional reversal forecast math — sign-flipped short-horizon return.

Pure functions over per-instrument close Series. No DB, no IO. Mirrors
``analytics.carry.forecast``: a vol-adjusted, scalar-adjusted, capped single-window
forecast, then an equal-weight combine over the window family x FDM, re-capped.
The forecast is NEGATIVE for a recent winner (reversal: short winners, long losers).
Causal within the series (``ew_return_vol`` is shifted); the position-level
``.shift(1)`` that makes sizing causal lives downstream in ``xs_leverage``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.forecast.vol import ew_return_vol
from analytics.xsmom.book import _union_index
from analytics.xsrev.config import ReversalConfig


def _return_zscore(close: pd.Series, window: int, vol_span: int) -> pd.Series:
    """Horizon-normalized recent return: ``r_k / (sigma_daily * sqrt(k))``.

    ``sigma_daily`` is the causal EW daily return vol; ``* sqrt(window)`` scales it
    to the window's horizon so windows are comparable in the equal-weight combine.
    """
    r = close / close.shift(window) - 1.0
    sigma = ew_return_vol(close, vol_span)
    z = r / (sigma * math.sqrt(window))
    return z.replace([np.inf, -np.inf], np.nan)


def scaled_reversal_forecast(
    close: pd.Series, window: int, scalar: float, vol_span: int, cap: float
) -> pd.Series:
    """Vol-adjusted, scalar-adjusted, capped single-window reversal forecast.

    ``forecast = clip(-scalar * zscore, +/-cap)`` — the minus is the reversal
    (short recent winners, long recent losers).
    """
    z = _return_zscore(close, window, vol_span)
    return (-scalar * z).clip(lower=-cap, upper=cap)


def combine_reversal_forecasts(
    close: pd.Series,
    windows: tuple[int, ...],
    scalar: float,
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Equal-weight mean of per-window reversal forecasts x FDM, re-capped."""
    parts = [scaled_reversal_forecast(close, w, scalar, vol_span, cap) for w in windows]
    mean = pd.concat(parts, axis=1).mean(axis=1)
    return (mean * fdm).clip(lower=-cap, upper=cap)


def reversal_forecast_matrix(
    closes: dict[str, pd.Series], cfg: ReversalConfig
) -> pd.DataFrame:
    """Per-instrument raw reversal forecasts, aligned to the union daily index.

    Same shape as ``xsmom.book.xs_forecasts`` (columns = symbols, NaN warmup
    preserved), so the shared cross-sectional demean/leverage consume it unchanged.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        f = combine_reversal_forecasts(
            close,
            cfg.formation_windows,
            cfg.reversal_scalar,
            cfg.fdm,
            cfg.vol_span,
            cfg.cap,
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def crowding_forecast_matrix(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    ois: dict[str, pd.Series],
    cfg: ReversalConfig,
    *,
    oi_window: int = 20,
    funding_span: int = 5,
) -> pd.DataFrame:
    """DESCRIPTIVE-ONLY positioning forecast: ``-sign(EWMA funding) * z(OI growth)``.

    Fade the side the crowd is BUILDING into (funding sign = which side is crowded;
    OI-growth z-score = whether inflow is unusually strong right now). Same shape as
    ``reversal_forecast_matrix``. NOT a gated signal — ``open_interest`` history is
    shallow (~144d majors); this exists for an underpowered exploratory read only.
    The z-score uses a trailing rolling mean/std; position-level causality comes from
    the downstream ``.shift(1)`` in ``xs_leverage``, same as the reversal path.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym in closes:
        oi = ois.get(sym)
        fund = fundings.get(sym)
        if oi is None or fund is None:
            continue
        growth = oi.pct_change()
        roll = growth.rolling(oi_window)
        z = (growth - roll.mean()) / roll.std()
        sign_f = np.sign(fund.ewm(span=funding_span, adjust=False).mean())
        raw = (-sign_f * z).clip(lower=-cfg.cap, upper=cfg.cap)
        cols[sym] = raw.reindex(union)
    return pd.DataFrame(cols, index=union)
