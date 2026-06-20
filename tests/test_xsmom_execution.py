from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
from pytest import approx as pytest_approx

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import run_xs_backtest, xs_leverage
from analytics.xsmom.execution import (
    ExecutionCostConfig,
    dollar_adv,
    turnover_cost_rate,
)


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_execution_cost_config_defaults() -> None:
    cfg = ExecutionCostConfig()
    assert cfg.capital == 1_000_000.0
    assert cfg.impact == "sqrt"
    assert cfg.adv_window == 30
    # tiers: major tightest, alt widest
    assert cfg.major_bps < cfg.mid_bps < cfg.alt_bps


def test_dollar_adv_trailing_median_and_causal_shift() -> None:
    # dollar volume = 10 for first 5 days, then 100 thereafter.
    idx = _idx(10)
    dv = pd.Series([10.0] * 5 + [100.0] * 5, index=idx)
    adv = dollar_adv({"X": dv}, window=3)["X"]
    # window=3 median, then shift(1): row d uses days d-3..d-1.
    # First 3 rows are NaN (no full prior window after shift).
    assert adv.iloc[:3].isna().all()
    # Day index 3 uses days 0,1,2 -> median(10,10,10) = 10.
    assert adv.iloc[3] == 10.0
    # Day index 4 uses days 1,2,3 -> 10.
    assert adv.iloc[4] == 10.0


def test_dollar_adv_is_causal_to_same_day_volume() -> None:
    idx = _idx(10)
    dv = pd.Series(np.linspace(1.0, 10.0, 10), index=idx)
    base = dollar_adv({"X": dv}, window=3)["X"]
    bumped = dv.copy()
    bumped.iloc[5] *= 100.0  # perturb day 5
    out = dollar_adv({"X": bumped}, window=3)["X"]
    # Day 5's own ADV must be unchanged (uses days 2,3,4 via shift) — the
    # load-bearing causal invariant: ADV[d] never depends on volume[d].
    assert out.iloc[5] == base.iloc[5]
    # The bump is not a no-op: it surfaces downstream (median is robust to a
    # single outlier, so the first changed value lands at day 7, not day 6).
    assert not np.array_equal(out.iloc[6:].to_numpy(), base.iloc[6:].to_numpy())


def test_turnover_cost_rate_tiers_and_sqrt_impact() -> None:
    idx = _idx(2)
    # Two instruments: one major-liquid, one thin alt.
    leverage = pd.DataFrame({"BIG": [0.0, 0.5], "THIN": [0.0, 0.5]}, index=idx)
    adv = {
        "BIG": pd.Series([np.nan, 2_000_000_000.0], index=idx),  # major tier
        "THIN": pd.Series([np.nan, 1_000_000.0], index=idx),  # alt tier
    }
    cfg = ExecutionCostConfig(capital=10_000_000.0, k=0.1, impact="sqrt")
    rate = turnover_cost_rate(leverage, adv, cfg)
    # Row 0: leverage diff 0 but ADV NaN -> impact NaN -> rate NaN.
    assert rate.loc[idx[0]].isna().all()
    # Row 1, BIG: fee + major half-spread + k*sqrt(0.5*10e6 / 2e9)
    exp_big = 0.0005 + 1.0 / 1e4 + 0.1 * np.sqrt(0.5 * 10_000_000.0 / 2_000_000_000.0)
    assert rate.loc[idx[1], "BIG"] == pytest_approx(exp_big)
    # THIN pays the alt spread AND far more impact (tiny ADV).
    exp_thin = 0.0005 + 8.0 / 1e4 + 0.1 * np.sqrt(0.5 * 10_000_000.0 / 1_000_000.0)
    assert rate.loc[idx[1], "THIN"] == pytest_approx(exp_thin)
    assert rate["THIN"].to_numpy()[1] > rate["BIG"].to_numpy()[1]


def test_turnover_cost_rate_collapses_to_flat_at_zero_impact() -> None:
    idx = _idx(2)
    leverage = pd.DataFrame({"X": [0.0, 0.3]}, index=idx)
    adv = {"X": pd.Series([1e9, 1e9], index=idx)}
    # k=0 and all tiers equal -> a flat per-leg constant.
    cfg = ExecutionCostConfig(
        k=0.0, major_bps=2.0, mid_bps=2.0, alt_bps=2.0, fee_pct=0.0005
    )
    rate = turnover_cost_rate(leverage, adv, cfg)
    assert np.allclose(rate["X"].to_numpy(), 0.0005 + 2.0 / 1e4)


def test_turnover_cost_rate_monotonic_in_capital() -> None:
    idx = _idx(2)
    leverage = pd.DataFrame({"X": [0.0, 0.4]}, index=idx)
    adv = {"X": pd.Series([5e7, 5e7], index=idx)}  # alt tier, finite ADV
    lo = turnover_cost_rate(leverage, adv, ExecutionCostConfig(capital=1e6, k=0.1))
    hi = turnover_cost_rate(leverage, adv, ExecutionCostConfig(capital=1e8, k=0.1))
    assert hi["X"].to_numpy()[1] > lo["X"].to_numpy()[1]


def _synth_inputs(
    n: int = 400, seed: int = 0
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for i, sym in enumerate(["A", "B", "C"]):
        steps = rng.normal(0.0, 0.02, n) + 0.0005 * (i - 1)
        closes[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
        fundings[sym] = pd.Series(0.0, index=idx)
    return closes, fundings


def test_run_xs_backtest_default_off_is_byte_identical() -> None:
    closes, fundings = _synth_inputs()
    cfg = dataclasses.replace(ForecastConfig(), speeds=((8, 32, 5.3),))
    base = run_xs_backtest(closes, fundings, cfg)
    again = run_xs_backtest(closes, fundings, cfg, turnover_cost_rate=None)
    assert np.array_equal(base.portfolio_return, again.portfolio_return, equal_nan=True)


def test_run_xs_backtest_constant_rate_matches_scalar_path() -> None:
    closes, fundings = _synth_inputs()
    cfg = dataclasses.replace(ForecastConfig(), speeds=((8, 32, 5.3),))
    base = run_xs_backtest(closes, fundings, cfg)  # scalar cost = fee + slip
    lev = xs_leverage(closes, cfg)
    flat = cfg.fee_pct + cfg.slippage_pct
    const_rate = pd.DataFrame(flat, index=lev.index, columns=lev.columns)
    out = run_xs_backtest(closes, fundings, cfg, turnover_cost_rate=const_rate)
    assert np.allclose(base.portfolio_return, out.portfolio_return, equal_nan=True)
