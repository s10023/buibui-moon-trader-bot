from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.combine.book import CombinedBookResult, combine_books, equity_curve
from analytics.combine.config import CombineConfig
from analytics.forecast.book import ForecastBookResult
from analytics.xsmom.book import XSBookResult


def _xs_result(returns: np.ndarray, idx: pd.DatetimeIndex) -> XSBookResult:
    return XSBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={},
    )


def _trend_result(returns: np.ndarray, idx: pd.DatetimeIndex) -> ForecastBookResult:
    return ForecastBookResult(
        daily_index=idx,
        portfolio_return=returns,
        pre_governor_return=returns,
        governor=np.ones(len(returns)),
        active_count=np.full(len(returns), 2, dtype=np.int64),
        per_instrument_net={},
    )


def _pair(n: int = 600, seed: int = 0) -> tuple[XSBookResult, ForecastBookResult]:
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    rng = np.random.default_rng(seed)
    r_xs = 0.001 + 0.01 * rng.standard_normal(n)
    r_tr = 0.0005 + 0.01 * rng.standard_normal(n)
    return _xs_result(r_xs, idx), _trend_result(r_tr, idx)


def test_combine_books_returns_result_shape() -> None:
    xs, tr = _pair()
    res = combine_books(xs, tr, CombineConfig())
    assert isinstance(res, CombinedBookResult)
    assert res.portfolio_return.shape[0] == 600
    assert not np.isnan(res.portfolio_return).any()
    assert res.idm.shape[0] == 600
    assert res.governor.shape[0] == 600


def test_no_governor_no_idm_is_weighted_sum() -> None:
    # idm_mode static on perfectly-correlated streams -> IDM == 1.0, governor off
    idx = pd.date_range("2021-01-01", periods=400, freq="D")
    r = 0.001 + 0.01 * np.random.default_rng(7).standard_normal(400)
    xs = _xs_result(r, idx)
    tr = _trend_result(r, idx)
    cfg = CombineConfig(idm_mode="static", apply_governor=False)
    res = combine_books(xs, tr, cfg)
    # IDM(corr=1) = 1.0, so combined == 0.5r + 0.5r == r
    np.testing.assert_allclose(res.portfolio_return, r, atol=1e-12)


def test_idm_lifts_uncorrelated_pre_return() -> None:
    xs, tr = _pair(seed=1)
    cfg = CombineConfig(idm_mode="static", apply_governor=False)
    res = combine_books(xs, tr, cfg)
    pre = res.pre_idm_return
    # uncorrelated sleeves -> IDM > 1 -> post-IDM magnitude scaled up vs pre
    assert res.idm.mean() > 1.0
    assert np.nanstd(res.portfolio_return) > np.nanstd(pre)


def test_governor_targets_vol() -> None:
    xs, tr = _pair(seed=2)
    res = combine_books(xs, tr, CombineConfig())  # governor on
    ann = np.sqrt(365.0)
    live = res.portfolio_return[res.governor > 0.0]
    realized = float(np.std(live, ddof=1)) * ann
    # causal governor pulls realized annual vol toward the 20% target band
    assert 0.10 < realized < 0.35


def test_combine_is_causal_no_lookahead() -> None:
    xs, tr = _pair(seed=3)
    cfg = CombineConfig()  # causal IDM + governor
    base = combine_books(xs, tr, cfg)
    bumped_xs = XSBookResult(
        daily_index=xs.daily_index,
        portfolio_return=xs.portfolio_return.copy(),
        pre_governor_return=xs.pre_governor_return,
        governor=xs.governor,
        active_count=xs.active_count,
        per_instrument_net={},
    )
    k = 400
    bumped_xs.portfolio_return[k] += 5.0  # perturb a future sleeve return
    after = combine_books(bumped_xs, tr, cfg)
    # portfolio_return[k] legitimately moves (pre_k earns the day-k return), so
    # only strictly-earlier returns must be byte-identical.
    np.testing.assert_array_equal(base.portfolio_return[:k], after.portfolio_return[:k])
    # The SIZING quantities (idm, governor) are causal THROUGH k-1, so they must
    # be unchanged up to AND INCLUDING bar k. This is what actually guards both
    # .shift(1)s: without the IDM shift, idm[k] would move; without the governor
    # shift, governor[k] would move.
    np.testing.assert_array_equal(base.idm[: k + 1], after.idm[: k + 1])
    np.testing.assert_array_equal(base.governor[: k + 1], after.governor[: k + 1])


def test_combine_aligns_mismatched_indices() -> None:
    idx_a = pd.date_range("2021-01-01", periods=500, freq="D")
    idx_b = pd.date_range("2021-03-01", periods=500, freq="D")
    rng = np.random.default_rng(5)
    xs = _xs_result(0.01 * rng.standard_normal(500), idx_a)
    tr = _trend_result(0.01 * rng.standard_normal(500), idx_b)
    res = combine_books(xs, tr, CombineConfig())
    union = idx_a.union(idx_b)
    assert len(res.daily_index) == len(union)
    assert not np.isnan(res.portfolio_return).any()


def test_equity_curve_starts_at_one() -> None:
    xs, tr = _pair(seed=6)
    res = combine_books(xs, tr, CombineConfig())
    curve = equity_curve(res)
    assert curve.iloc[0] == 1.0 + res.portfolio_return[0]


def test_degenerate_flat_sleeves() -> None:
    idx = pd.date_range("2021-01-01", periods=300, freq="D")
    xs = _xs_result(np.zeros(300), idx)
    tr = _trend_result(np.zeros(300), idx)
    res = combine_books(xs, tr, CombineConfig())
    assert np.all(res.portfolio_return == 0.0)
    assert np.all(res.idm == 1.0)  # no live data -> neutral IDM throughout
