"""Tests for the carry report — gate verdict + plumbing + corr."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from analytics.carry.book import run_carry_backtest
from analytics.carry.config import CarryConfig
from analytics.carry.report import CarryReport, carry_gate_verdict, evaluate_carry


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def _make_inputs(n: int = 300) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    idx = _idx(n)
    rng = np.random.default_rng(3)
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for k, sym in enumerate(["AAA", "BBB", "CCC"]):
        closes[sym] = pd.Series(
            100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n))), index=idx
        )
        fundings[sym] = pd.Series(((-1) ** k) * 0.0002, index=idx)
    return closes, fundings


def _base_report() -> CarryReport:
    return CarryReport(
        sharpe_annual=1.0,
        sortino_annual=1.0,
        max_dd=-0.1,
        calmar=1.0,
        annual_return=0.2,
        annual_vol=0.2,
        n_obs=300,
        dsr=0.96,
        pbo=0.4,
        boot_lo=0.1,
        boot_hi=2.0,
        min_trl=100.0,
        corr_to_xs=0.0,
        xs_sharpe=1.3,
        corr_to_trend=0.2,
        trend_sharpe=0.3,
    )


def test_gate_verdict_boundaries() -> None:
    base = _base_report()
    assert carry_gate_verdict(base) is True
    assert carry_gate_verdict(dataclasses.replace(base, dsr=0.94)) is False
    assert carry_gate_verdict(dataclasses.replace(base, pbo=0.6)) is False
    assert carry_gate_verdict(dataclasses.replace(base, boot_lo=0.0)) is False


def test_evaluate_carry_fields_present() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    trials = {
        "span1": res.portfolio_return,
        "span5": res.portfolio_return * 0.9,
        "combined": res.portfolio_return,
    }
    xs = res.portfolio_return * 0.5
    trend = res.portfolio_return * 0.3
    rep = evaluate_carry(
        res, cfg, trial_returns=trials, xs_returns=xs, trend_returns=trend
    )
    assert isinstance(rep, CarryReport)
    assert rep.n_obs == 300
    assert -1.0 <= rep.corr_to_xs <= 1.0
    assert -1.0 <= rep.corr_to_trend <= 1.0


def test_corr_to_xs_excludes_joint_dead_warmup() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    # XS returns identical to the book -> corr ~1.0 over the live tail
    rep = evaluate_carry(
        res,
        cfg,
        trial_returns={
            "span1": res.portfolio_return,
            "combined": res.portfolio_return,
        },
        xs_returns=res.portfolio_return.copy(),
        trend_returns=res.portfolio_return.copy(),
    )
    assert rep.corr_to_xs > 0.99
