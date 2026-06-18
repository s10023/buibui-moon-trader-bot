from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.combine.book import CombinedBookResult
from analytics.combine.config import CombineConfig
from analytics.combine.report import (
    CombineReport,
    combine_gate_verdict,
    evaluate_combined,
)


def _result(returns: np.ndarray, idm: np.ndarray | None = None) -> CombinedBookResult:
    idx = pd.date_range("2021-01-01", periods=len(returns), freq="D")
    return CombinedBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_idm_return=returns,
        idm=idm if idm is not None else np.full(len(returns), 1.2),
        governor=np.ones(len(returns)),
        xs_return_aligned=returns,
        trend_return_aligned=returns,
    )


def test_report_shape_and_fields() -> None:
    rng = np.random.default_rng(0)
    r = 0.001 + 0.01 * rng.standard_normal(800)
    xs = 0.0012 + 0.01 * rng.standard_normal(800)
    tr = 0.0006 + 0.01 * rng.standard_normal(800)
    res = _result(r)
    res = CombinedBookResult(
        daily_index=res.daily_index,
        portfolio_return=r,
        pre_idm_return=r,
        idm=np.full(800, 1.2),
        governor=np.ones(800),
        xs_return_aligned=xs,
        trend_return_aligned=tr,
    )
    trials = {"combined": r, "xs": xs, "trend": tr}
    rep = evaluate_combined(res, CombineConfig(), trials, xs, tr)
    assert isinstance(rep, CombineReport)
    assert rep.n_obs == 800
    assert rep.boot_lo <= rep.sharpe_annual <= rep.boot_hi
    assert 0.0 <= rep.pbo <= 1.0
    assert -1.0 <= rep.corr_xs_trend <= 1.0
    assert rep.realized_idm > 1.0
    assert rep.diversification_mult > 0.0


def test_gate_verdict_true_when_all_pass() -> None:
    rep = CombineReport(
        sharpe_annual=1.5,
        sortino_annual=2.0,
        max_dd=-0.1,
        calmar=3.0,
        annual_return=0.3,
        annual_vol=0.2,
        n_obs=800,
        dsr=0.99,
        pbo=0.2,
        boot_lo=0.4,
        boot_hi=2.5,
        min_trl=300.0,
        corr_xs_trend=0.37,
        realized_idm=1.2,
        vol_xs=0.2,
        vol_trend=0.2,
        vol_combined=0.165,
        diversification_mult=1.21,
        sharpe_xs=1.375,
        sharpe_trend=0.36,
        xs_contribution=0.0006,
        trend_contribution=0.0002,
    )
    assert combine_gate_verdict(rep) is True


def test_gate_verdict_false_when_pbo_high() -> None:
    rep = CombineReport(
        sharpe_annual=1.5,
        sortino_annual=2.0,
        max_dd=-0.1,
        calmar=3.0,
        annual_return=0.3,
        annual_vol=0.2,
        n_obs=800,
        dsr=0.99,
        pbo=0.6,
        boot_lo=0.4,
        boot_hi=2.5,
        min_trl=300.0,
        corr_xs_trend=0.37,
        realized_idm=1.2,
        vol_xs=0.2,
        vol_trend=0.2,
        vol_combined=0.165,
        diversification_mult=1.21,
        sharpe_xs=1.375,
        sharpe_trend=0.36,
        xs_contribution=0.0006,
        trend_contribution=0.0002,
    )
    assert combine_gate_verdict(rep) is False


def test_flat_returns_degenerate_to_zero() -> None:
    res = _result(np.zeros(500), idm=np.ones(500))
    rep = evaluate_combined(
        res,
        CombineConfig(),
        trial_returns={"combined": np.zeros(500)},
        xs_returns=np.zeros(500),
        trend_returns=np.zeros(500),
    )
    assert rep.sharpe_annual == 0.0
    assert rep.dsr == 0.0
    assert math.isinf(rep.min_trl)
    assert combine_gate_verdict(rep) is False
