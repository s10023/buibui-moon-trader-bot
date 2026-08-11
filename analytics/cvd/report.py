"""Assemble the D1 CVD verdict: headline metrics + the three-leg gate.

Mirrors ``analytics/xsmom/report.py`` and swaps its ``corr_to_trend`` stamp for
``corr_to_xsmom`` — for this sleeve the decisive comparison is against the
deploy core, not the shelved trend sleeve. A CVD book correlating ~0.9 with
XS-momentum is a restatement of the first edge, not a second one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.research_guards import (
    block_bootstrap_ci,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
    passes_gate,
)
from portfolio import metrics


@dataclass(frozen=True)
class CVDReport:
    """Headline metrics + guards + the decorrelation read for the D1 verdict."""

    sharpe_annual: float
    max_dd: float
    annual_return: float
    annual_vol: float
    n_obs: int
    dsr: float
    pbo: float
    boot_lo: float
    boot_hi: float
    min_trl: float
    corr_to_xsmom: float
    xsmom_sharpe: float
    folded_to_magnitude: bool


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
        return float("nan")
    x, y = a[-n:], b[-n:]
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def evaluate_cvd(
    portfolio_return: npt.NDArray[np.float64],
    cfg: ForecastConfig,
    trial_returns: dict[str, npt.NDArray[np.float64]],
    xsmom_returns: npt.NDArray[np.float64],
    *,
    fold_to_magnitude: bool = False,
) -> CVDReport:
    """All D1 metrics + research-guard stamps + the decorrelation read.

    ``trial_returns`` is the declared multiple-testing family (per-span sleeves
    + combined). ``fold_to_magnitude`` implements the spec's negative-direction
    rule: DSR and MinTRL are directional, so a negative-direction verdict gated
    on either is structurally unreachable unless the target AND every trial
    Sharpe fold to ``abs()``. Folding shrinks trial dispersion in a mixed-sign
    family, so the gate becomes marginally MORE permissive — bias runs toward
    more passes, never fewer.
    """
    r = np.asarray(portfolio_return, dtype=np.float64)
    curve = (1.0 + pd.Series(r)).cumprod()
    ann = math.sqrt(cfg.annualization_days)

    sr_d = _per_period_sharpe(r)
    trial_srs = [_per_period_sharpe(np.asarray(v)) for v in trial_returns.values()]
    if fold_to_magnitude:
        sr_d = abs(sr_d)
        trial_srs = [abs(s) for s in trial_srs]

    min_len = min((len(v) for v in trial_returns.values()), default=0)
    if min_len >= 28 and len(trial_returns) >= 2:
        mat = np.column_stack(
            [np.asarray(v)[-min_len:] for v in trial_returns.values()]
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

    xsm = np.asarray(xsmom_returns, dtype=np.float64)
    xsm_curve = (1.0 + pd.Series(xsm)).cumprod()

    return CVDReport(
        sharpe_annual=metrics.sharpe(curve),
        max_dd=metrics.max_drawdown(curve),
        annual_return=metrics.annual_return(curve),
        annual_vol=metrics.annual_vol(curve),
        n_obs=len(r),
        dsr=dsr,
        pbo=pbo,
        boot_lo=boot_lo,
        boot_hi=boot_hi,
        min_trl=min_trl,
        corr_to_xsmom=_aligned_corr(r, xsm),
        xsmom_sharpe=metrics.sharpe(xsm_curve) if len(xsm) >= 2 else 0.0,
        folded_to_magnitude=fold_to_magnitude,
    )


def cvd_gate_verdict(report: CVDReport) -> bool:
    """The headline gate: DSR >= 0.95 AND PBO <= 0.5 AND boot_lo > 0.

    Delegates to :func:`analytics.research_guards.passes_gate`; the thresholds
    live only there. ``min_trl`` and ``corr_to_xsmom`` are reported stamps, NOT
    legs — ``corr_to_xsmom`` enters the verdict as a judgement under the spec's
    section 8, with the number printed.
    """
    return passes_gate(report.dsr, report.pbo, report.boot_lo)
