"""Tests for the funding-carry forecast construction."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.carry.forecast import (
    annualized_funding,
    combine_carry_forecasts,
    scaled_carry_forecast,
)

_ANN = 365.0


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")


def test_annualized_funding_span1_is_raw_times_ann() -> None:
    idx = _idx(5)
    fund = pd.Series([0.001, 0.002, -0.001, 0.0, 0.003], index=idx)
    out = annualized_funding(fund, span=1, ann_days=_ANN)
    # span=1 EWMA (adjust=False) returns the latest value unchanged
    np.testing.assert_allclose(out.to_numpy(), fund.to_numpy() * _ANN)


def test_annualized_funding_is_causal() -> None:
    idx = _idx(6)
    base = pd.Series([0.001] * 6, index=idx)
    bumped = base.copy()
    bumped.iloc[4] = 0.05  # bump a later point
    a = annualized_funding(base, span=3, ann_days=_ANN)
    b = annualized_funding(bumped, span=3, ann_days=_ANN)
    # values strictly before the bump are unchanged (EWMA is causal)
    np.testing.assert_allclose(a.iloc[:4].to_numpy(), b.iloc[:4].to_numpy())


def test_scaled_carry_sign_long_when_funding_negative() -> None:
    idx = _idx(40)
    close = pd.Series(np.linspace(100.0, 110.0, 40), index=idx)
    neg_fund = pd.Series([-0.001] * 40, index=idx)
    pos_fund = pd.Series([0.001] * 40, index=idx)
    f_neg = scaled_carry_forecast(close, neg_fund, 1, 30.0, 32, 20.0, _ANN)
    f_pos = scaled_carry_forecast(close, pos_fund, 1, 30.0, 32, 20.0, _ANN)
    # long (positive) when funding negative; short (negative) when funding positive
    assert f_neg.dropna().iloc[-1] > 0
    assert f_pos.dropna().iloc[-1] < 0


def test_scaled_carry_capped() -> None:
    idx = _idx(40)
    close = pd.Series(np.linspace(100.0, 101.0, 40), index=idx)  # tiny vol
    huge_fund = pd.Series([-0.5] * 40, index=idx)  # enormous negative funding
    f = scaled_carry_forecast(close, huge_fund, 1, 30.0, 32, 20.0, _ANN)
    assert f.dropna().abs().max() <= 20.0 + 1e-9


def test_combine_equalweight_times_fdm_recapped() -> None:
    idx = _idx(50)
    close = pd.Series(np.linspace(100.0, 120.0, 50), index=idx)
    fund = pd.Series([-0.0005] * 50, index=idx)
    spans = (1, 5)
    parts = [scaled_carry_forecast(close, fund, s, 30.0, 32, 20.0, _ANN) for s in spans]
    expected = (pd.concat(parts, axis=1).mean(axis=1) * 1.25).clip(-20.0, 20.0)
    got = combine_carry_forecasts(close, fund, spans, 30.0, 1.25, 32, 20.0, _ANN)
    np.testing.assert_allclose(
        got.dropna().to_numpy(), expected.reindex(got.index).dropna().to_numpy()
    )
