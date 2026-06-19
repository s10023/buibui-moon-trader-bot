"""Per-instrument carry leverage and portfolio aggregation.

All sizing is causal: the position held during day ``d`` is sized from information
through ``d-1`` only. The cross-sectional demean (when enabled) is a same-day reduction
over causal forecasts; the ``.shift(1)`` is applied AFTER demeaning, BEFORE sizing.
Mirrors the trend (``analytics.forecast.book``) and XS (``analytics.xsmom.book``)
templates, swapping the EWMAC forecast for the funding-carry forecast.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.carry.config import CarryConfig
from analytics.carry.forecast import combine_carry_forecasts
from analytics.forecast.vol import ew_return_vol


def _union_index(closes: dict[str, pd.Series]) -> pd.DatetimeIndex:
    union = pd.DatetimeIndex([])
    for s in closes.values():
        union = union.union(pd.DatetimeIndex(s.index))
    return union.sort_values()


def carry_forecast_matrix(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: CarryConfig,
) -> pd.DataFrame:
    """Combined carry forecast per instrument, aligned to the union daily index.

    Columns = symbols, index = sorted union of all instrument dates. NaN where an
    instrument has not warmed up or where return-vol is undefined; the NaN warm-up
    bars are intentional (the cross-sectional demean skips NaN via ``mean(axis=1)``).
    """
    union = _union_index(closes)
    cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        fund = fundings.get(sym, pd.Series(0.0, index=close.index))
        f = combine_carry_forecasts(
            close,
            fund,
            cfg.carry_spans,
            cfg.carry_scalar,
            cfg.fdm,
            cfg.vol_span,
            cfg.cap,
            cfg.annualization_days,
        )
        cols[sym] = f.reindex(union)
    return pd.DataFrame(cols, index=union)


def carry_leverage(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: CarryConfig,
) -> pd.DataFrame:
    """Causal vol-parity leverage matrix from the carry forecast.

    Absolute: per-instrument forecast. Cross-sectional: forecast demeaned across the
    active set (dollar-neutral). Demean (if enabled) -> ``.shift(1)`` (position on day
    ``d`` uses info through ``d-1``) -> vol-target each leg:
    ``leverage = (f_shifted / 10) * (vol_target / vol_ann)``.
    """
    f = carry_forecast_matrix(closes, fundings, cfg)
    if cfg.cross_sectional:
        f = f.sub(f.mean(axis=1), axis=0)
    f_shifted = f.shift(1)
    union = pd.DatetimeIndex(f.index)
    ann = np.sqrt(cfg.annualization_days)

    lev_cols: dict[str, pd.Series] = {}
    for sym, close in closes.items():
        vol_ann = ew_return_vol(close, cfg.vol_span).mul(ann).reindex(union)
        lev = (f_shifted[sym] / 10.0) * (cfg.vol_target_annual / vol_ann)
        lev_cols[sym] = lev.replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame(lev_cols, index=union)


@dataclass(frozen=True)
class CarryBookResult:
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray  # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray  # NaN for the first gov_window warm-up bars
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]


def run_carry_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: CarryConfig,
) -> CarryBookResult:
    """Causal carry book — absolute (equal-risk mean) or cross-sectional (sum)."""
    leverage = carry_leverage(closes, fundings, cfg)
    union = pd.DatetimeIndex(leverage.index)
    cost = cfg.fee_pct + cfg.slippage_pct

    per_net: dict[str, pd.Series] = {}
    net_cols: list[pd.Series] = []
    for sym, close in closes.items():
        lev = leverage[sym]
        r = close.pct_change().reindex(union)
        gross = lev * r
        turnover = (lev - lev.shift(1).fillna(0.0)).abs() * cost
        fund = (
            fundings.get(sym, pd.Series(0.0, index=close.index))
            .reindex(union)
            .fillna(0.0)
        )
        funding_cost = lev * fund  # shorts (lev<0) receive funding when fund>0
        net = gross - turnover - funding_cost
        per_net[sym] = net
        net_cols.append(net)

    net_mat = pd.concat(net_cols, axis=1)
    active = net_mat.notna().sum(axis=1)
    # cross-sectional = long-short P&L (sum of legs; all-NaN warm-up -> 0.0);
    # absolute = equal-risk mean across active instruments
    pre = net_mat.sum(axis=1) if cfg.cross_sectional else net_mat.mean(axis=1)
    pre = pre.fillna(0.0)

    ann = np.sqrt(cfg.annualization_days)
    trailing_vol = (
        pre.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().shift(1) * ann
    )
    g = (cfg.vol_target_annual / trailing_vol).clip(cfg.g_min, cfg.g_max)
    port = g.fillna(0.0) * pre

    return CarryBookResult(
        daily_index=union,
        portfolio_return=port.to_numpy(dtype=np.float64),
        pre_governor_return=pre.to_numpy(dtype=np.float64),
        governor=g.to_numpy(dtype=np.float64),
        active_count=active.to_numpy(dtype=np.int64),
        per_instrument_net=per_net,
    )


def equity_curve(result: CarryBookResult) -> pd.Series:
    """Compounding equity curve (starts at 1.0+r0) for portfolio.metrics."""
    r = pd.Series(result.portfolio_return, index=result.daily_index)
    return (1.0 + r).cumprod()
