"""Book-return-space combine of the two validated sleeve return streams.

All sizing is causal: the combined return on day `d` is `g_d · idm_d · (w_xs·r_xs,d
+ w_trend·r_trend,d)` where `r_*,d` are the sleeves' own causal post-governor
returns, `idm_d` is the IDM from correlation through `d-1`, and `g_d` is the final
vol governor from trailing vol through `d-1`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.combine.config import CombineConfig
from analytics.combine.idm import causal_idm_series, static_idm
from analytics.forecast.book import ForecastBookResult
from analytics.xsmom.book import XSBookResult


@dataclass(frozen=True)
class CombinedBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-IDM, post-governor (NaN-free)
    pre_idm_return: np.ndarray  # weighted sum before IDM
    idm: np.ndarray  # per-day IDM (1.0 during warm-up)
    governor: np.ndarray  # 0.0 / NaN→0.0 during warm-up
    xs_return_aligned: np.ndarray
    trend_return_aligned: np.ndarray


def combine_books(
    xs_result: XSBookResult,
    trend_result: ForecastBookResult,
    cfg: CombineConfig,
) -> CombinedBookResult:
    """Weight → IDM → final causal governor over the two sleeve return streams."""
    s_xs = pd.Series(xs_result.portfolio_return, index=xs_result.daily_index)
    s_tr = pd.Series(trend_result.portfolio_return, index=trend_result.daily_index)
    union = pd.DatetimeIndex(s_xs.index.union(s_tr.index).sort_values())
    r_xs = s_xs.reindex(union).fillna(0.0)
    r_tr = s_tr.reindex(union).fillna(0.0)

    pre = cfg.w_xs * r_xs + cfg.w_trend * r_tr

    if cfg.idm_mode == "static":
        idm_const = static_idm(
            r_xs.to_numpy(), r_tr.to_numpy(), cfg.w_xs, cfg.w_trend, cfg.idm_cap
        )
        idm = pd.Series(idm_const, index=union)
    else:
        idm = causal_idm_series(
            r_xs.to_numpy(),
            r_tr.to_numpy(),
            cfg.w_xs,
            cfg.w_trend,
            cfg.idm_window,
            cfg.idm_min_periods,
            cfg.idm_cap,
            union,
        )

    post_idm = idm * pre

    sc = cfg.sleeve_cfg
    if cfg.apply_governor:
        ann = np.sqrt(sc.annualization_days)
        trailing_vol = (
            post_idm.rolling(sc.gov_window, min_periods=sc.gov_window).std().shift(1)
            * ann
        )
        g = (sc.vol_target_annual / trailing_vol).clip(sc.g_min, sc.g_max)
        port = g.fillna(0.0) * post_idm
    else:
        g = pd.Series(1.0, index=union)
        port = post_idm

    return CombinedBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_idm_return=pre.to_numpy(dtype=np.float64),
        idm=idm.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        xs_return_aligned=r_xs.to_numpy(dtype=np.float64),
        trend_return_aligned=r_tr.to_numpy(dtype=np.float64),
    )


def equity_curve(result: CombinedBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0+r₀) for portfolio.metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
