"""Assemble the carry verdict: headline metrics + guards + diversification read.

Pure over a ``CarryBookResult`` plus the candidate trials' daily returns (the honest
multiple-testing family for DSR/PBO) and the XS + trend sleeves' daily returns (the
diversification read against the deploy core, XS). Mirrors ``analytics.xsmom.report``
and adds ``corr_to_xs`` / ``xs_sharpe``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.carry.book import CarryBookResult, equity_curve
from analytics.carry.config import CarryConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from portfolio import metrics


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
    """Pearson corr over the common tail, excluding joint dead warm-up (0, 0)."""
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


def _sharpe_of(returns: npt.NDArray[np.float64]) -> float:
    if len(returns) >= 2:
        return metrics.sharpe((1.0 + pd.Series(returns)).cumprod())
    return 0.0


@dataclass(frozen=True)
class CarryReport:
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
    corr_to_xs: float
    xs_sharpe: float
    corr_to_trend: float
    trend_sharpe: float


def evaluate_carry(
    result: CarryBookResult,
    cfg: CarryConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xs_returns: npt.NDArray[np.float64],
    trend_returns: npt.NDArray[np.float64],
) -> CarryReport:
    """Headline metrics + research-guard stamps + XS/trend diversification reads.

    ``trial_returns`` is the honest multiple-testing family (per-span carry books +
    combined). ``xs_returns`` / ``trend_returns`` are the deploy-core + trend sleeve's
    daily returns on the same universe/window.
    """
    r = result.portfolio_return
    curve = equity_curve(result)
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(v) for v in trial_returns.values()]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack([v[-min_len:] for v in trial_returns.values()])
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

    return CarryReport(
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
        corr_to_xs=_aligned_corr(r, xs_returns),
        xs_sharpe=_sharpe_of(xs_returns),
        corr_to_trend=_aligned_corr(r, trend_returns),
        trend_sharpe=_sharpe_of(trend_returns),
    )


def carry_gate_verdict(report: CarryReport) -> bool:
    """The de-biased gate: DSR >= 0.95 AND PBO <= 0.5 AND bootstrap CI lower bound > 0."""
    return report.dsr >= 0.95 and report.pbo <= 0.5 and report.boot_lo > 0.0
