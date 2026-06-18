"""Assemble the trend×XS combine verdict: headline metrics + guards + diversification.

Pure over a CombinedBookResult plus the gate family's daily returns ({trend, XS,
combined}) and the aligned per-sleeve return arrays. Mirrors
`analytics.xsmom.report`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.combine.book import CombinedBookResult, equity_curve
from analytics.combine.config import CombineConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from portfolio import metrics

_GATE_DSR = 0.95
_GATE_PBO = 0.5


def _per_period_sharpe(r: npt.NDArray[np.float64]) -> float:
    if len(r) < 2:
        return 0.0
    sd = float(np.std(r, ddof=1))
    if sd < 1e-12:
        return 0.0
    return float(np.mean(r) / sd)


def _ann_sharpe(r: npt.NDArray[np.float64], ann: float) -> float:
    return _per_period_sharpe(r) * ann


def _aligned_corr(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
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


def _live_vol(r: npt.NDArray[np.float64], ann: float) -> float:
    """Annualized vol over the non-zero (live) tail; 0.0 if degenerate."""
    live = r[r != 0.0]
    if len(live) < 2:
        return 0.0
    return float(np.std(live, ddof=1)) * ann


@dataclass(frozen=True)
class CombineReport:
    sharpe_annual: float
    sortino_annual: float
    max_dd: float
    calmar: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_xs_trend: float
    realized_idm: float
    vol_xs: float
    vol_trend: float
    vol_combined: float
    diversification_mult: float
    sharpe_xs: float
    sharpe_trend: float
    xs_contribution: float
    trend_contribution: float


def evaluate_combined(
    result: CombinedBookResult,
    cfg: CombineConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xs_returns: npt.NDArray[np.float64],
    trend_returns: npt.NDArray[np.float64],
) -> CombineReport:
    """Headline metrics + DSR/PBO/boot/MinTRL over {trend, XS, combined} + diversification."""
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.sleeve_cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [
        _per_period_sharpe(np.asarray(v, dtype=np.float64))
        for v in trial_returns.values()
    ]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack(
            [np.asarray(v, dtype=np.float64)[-min_len:] for v in trial_returns.values()]
        )
        pbo = cscv_pbo(mat).pbo
    else:
        pbo = float("nan")

    if sr_d != 0.0:

        def _stat_fn(x: npt.NDArray[np.float64]) -> float:
            return _ann_sharpe(x, ann)

        boot = block_bootstrap_ci(r, stat_fn=_stat_fn, seed=7)
        boot_lo, boot_hi = boot.lo, boot.hi
        dsr = deflated_sharpe_ratio(sr_d, len(r), trial_srs=trial_srs)
        min_trl = min_track_record_length(sr_d, target_sr=1.0 / ann, confidence=0.95)
    else:
        boot_lo = boot_hi = dsr = 0.0
        min_trl = float("inf")

    xs_arr = np.asarray(xs_returns, dtype=np.float64)
    tr_arr = np.asarray(trend_returns, dtype=np.float64)
    vol_xs = _live_vol(xs_arr, ann)
    vol_trend = _live_vol(tr_arr, ann)
    vol_combined = _live_vol(result.pre_idm_return, ann)
    weighted_avg_vol = cfg.w_xs * vol_xs + cfg.w_trend * vol_trend
    diversification_mult = (
        weighted_avg_vol / vol_combined if vol_combined > 1e-12 else 0.0
    )

    live_idm = result.idm[result.idm != 1.0]
    realized_idm = float(np.mean(live_idm)) if len(live_idm) else 1.0

    xs_curve = (1.0 + pd.Series(xs_arr)).cumprod()
    tr_curve = (1.0 + pd.Series(tr_arr)).cumprod()

    return CombineReport(
        sharpe_annual=metrics.sharpe(curve),
        sortino_annual=metrics.sortino(curve),
        max_dd=metrics.max_drawdown(curve),
        calmar=metrics.calmar(curve),
        annual_return=metrics.annual_return(curve),
        annual_vol=metrics.annual_vol(curve),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
        corr_xs_trend=_aligned_corr(xs_arr, tr_arr),
        realized_idm=realized_idm,
        vol_xs=vol_xs,
        vol_trend=vol_trend,
        vol_combined=vol_combined,
        diversification_mult=diversification_mult,
        sharpe_xs=metrics.sharpe(xs_curve),
        sharpe_trend=metrics.sharpe(tr_curve),
        xs_contribution=cfg.w_xs * float(np.mean(xs_arr)) if len(xs_arr) else 0.0,
        trend_contribution=cfg.w_trend * float(np.mean(tr_arr)) if len(tr_arr) else 0.0,
    )


def combine_gate_verdict(report: CombineReport) -> bool:
    """The headline gate: DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0."""
    if math.isnan(report.pbo):
        return False
    return report.dsr >= _GATE_DSR and report.pbo <= _GATE_PBO and report.boot_lo > 0.0
