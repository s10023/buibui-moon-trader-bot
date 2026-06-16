"""Per-instrument cross-sectional forecasts, leverage, and portfolio aggregation.

All sizing is causal: the position held during day `d` is sized from information
through day `d-1` only. The cross-sectional demean is a same-day reduction over
causal forecasts; the `.shift(1)` is applied AFTER demeaning, BEFORE sizing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.ewmac import combine_forecasts


def _union_index(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    return union.sort_values()


def xs_forecasts(closes: dict[str, pd.Series], cfg: ForecastConfig) -> pd.DataFrame:
    """Raw combined EWMAC forecasts per instrument, aligned to the union daily index.

    Columns = symbols, index = sorted union of all instrument dates. NaN where an
    instrument has not-yet-warmed-up; zero-vol instruments (e.g. stablecoins, constant
    prices) produce NaN from 0/0 division and are filled to 0.0 (no trend signal).
    Causal: each column is `combine_forecasts(...)`, which uses only closes through
    each day.
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        f = combine_forecasts(
            close, cfg.speeds, cfg.fdm, cfg.vol_span, cfg.cap, weights=cfg.weights
        )
        # Zero-vol instruments yield 0/0=NaN; fill to 0 (no trend signal).
        f = f.fillna(0.0)
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def xs_demeaned_forecasts(
    closes: dict[str, pd.Series], cfg: ForecastConfig
) -> pd.DataFrame:
    """Cross-sectionally demeaned forecasts (relative strength).

    `g_i(d) = f_i(d) - mean_{j in active(d)} f_j(d)`; the row mean skips NaN so it
    is taken over the active instruments only. Each active row sums to ~0
    (dollar-neutral). Not yet shifted — see `xs_leverage`.
    """
    f = xs_forecasts(closes, cfg)
    return f.sub(f.mean(axis=1), axis=0)


@dataclass(frozen=True)
class XSBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]
