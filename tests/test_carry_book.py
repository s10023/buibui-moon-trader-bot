"""Tests for the carry book — absolute + cross-sectional, costs, causality."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.carry.book import (
    CarryBookResult,
    carry_forecast_matrix,
    carry_leverage,
    equity_curve,
    run_carry_backtest,
)
from analytics.carry.config import CarryConfig


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def _make_inputs(
    n: int = 200,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    idx = _idx(n)
    rng = np.random.default_rng(0)
    closes: dict[str, pd.Series] = {}
    fundings: dict[str, pd.Series] = {}
    for k, sym in enumerate(["AAA", "BBB", "CCC"]):
        steps = rng.normal(0.0, 0.02, n)
        closes[sym] = pd.Series(100.0 * np.exp(np.cumsum(steps)), index=idx)
        fundings[sym] = pd.Series((-1) ** k * 0.0002, index=idx)
    return closes, fundings


def test_forecast_matrix_aligned_to_union() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    f = carry_forecast_matrix(closes, fundings, cfg)
    assert list(f.columns) == ["AAA", "BBB", "CCC"]
    assert len(f.index) == 200


def test_cross_sectional_demean_rows_sum_near_zero() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=True)
    f = carry_forecast_matrix(closes, fundings, cfg)
    demeaned = f.sub(f.mean(axis=1), axis=0)
    warm = demeaned.dropna()
    assert np.allclose(warm.sum(axis=1).to_numpy(), 0.0, atol=1e-9)


def test_run_carry_backtest_result_shape() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    assert isinstance(res, CarryBookResult)
    assert len(res.daily_index) == 200
    assert res.portfolio_return.shape == (200,)
    assert np.isfinite(res.portfolio_return).all()
    assert set(res.per_instrument_net) == {"AAA", "BBB", "CCC"}


def test_absolute_uses_mean_xs_uses_sum() -> None:
    closes, fundings = _make_inputs()
    abs_cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=False)
    xs_cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=True)
    abs_res = run_carry_backtest(closes, fundings, abs_cfg)
    xs_res = run_carry_backtest(closes, fundings, xs_cfg)
    assert not np.allclose(abs_res.portfolio_return, xs_res.portfolio_return)


def test_funding_sign_short_receives_funding() -> None:
    # A single instrument with strong positive funding -> carry forecast short.
    idx = _idx(120)
    rng = np.random.default_rng(1)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.001, 120))), index=idx)
    closes = {"AAA": close}
    fundings = {"AAA": pd.Series(0.002, index=idx)}  # strongly positive -> go short
    cfg = CarryConfig(carry_spans=(1,), cross_sectional=False)
    lev = carry_leverage(closes, fundings, cfg)["AAA"]
    warm = lev.dropna()
    assert (warm < 0).any()  # short leg present


def test_governor_clipped_in_range() -> None:
    closes, fundings = _make_inputs(300)
    cfg = CarryConfig(carry_spans=(1, 5))
    res = run_carry_backtest(closes, fundings, cfg)
    g = pd.Series(res.governor).dropna()
    assert (g >= cfg.g_min - 1e-9).all()
    assert (g <= cfg.g_max + 1e-9).all()


def test_equity_curve_starts_near_one() -> None:
    closes, fundings = _make_inputs()
    cfg = CarryConfig(carry_spans=(1, 5))
    curve = equity_curve(run_carry_backtest(closes, fundings, cfg))
    assert abs(curve.iloc[0] - 1.0) < 0.5
    assert (curve > 0).all()


def test_causality_today_leverage_blind_to_today_funding() -> None:
    """Position on day d uses funding through d-1: bumping funding[k] must NOT move
    leverage at day k (it depends on the demeaned forecast at k-1), but MUST move
    leverage at day k+1. Cross-sectional: the bump on one instrument must leave ALL
    instruments' day-k leverage unchanged. Goes RED if the .shift(1) is removed.
    """
    closes, fundings = _make_inputs(200)
    cfg = CarryConfig(carry_spans=(1, 5), cross_sectional=True)
    k = 150
    base_lev = carry_leverage(closes, fundings, cfg)
    bumped = {s: v.copy() for s, v in fundings.items()}
    bumped["AAA"].iloc[k] = 0.05  # large bump at day k on one instrument
    bump_lev = carry_leverage(closes, bumped, cfg)
    np.testing.assert_allclose(
        base_lev.iloc[k].to_numpy(), bump_lev.iloc[k].to_numpy(), atol=1e-12
    )
    assert not np.allclose(
        base_lev.iloc[k + 1].to_numpy(), bump_lev.iloc[k + 1].to_numpy()
    )


def test_causality_future_does_not_leak_into_past() -> None:
    closes, fundings = _make_inputs(200)
    cfg = CarryConfig(carry_spans=(1, 5))
    k = 150
    base = run_carry_backtest(closes, fundings, cfg)
    bumped_closes = {s: v.copy() for s, v in closes.items()}
    bumped_closes["AAA"].iloc[k + 10] *= 1.5  # future price shock
    bump = run_carry_backtest(bumped_closes, fundings, cfg)
    np.testing.assert_allclose(
        base.portfolio_return[: k + 1], bump.portfolio_return[: k + 1], atol=1e-12
    )
