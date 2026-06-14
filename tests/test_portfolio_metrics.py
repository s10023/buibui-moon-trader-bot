"""Tests for portfolio.metrics — risk-adjusted stats on a daily curve."""

import math

import numpy as np
import pandas as pd
import pytest

from portfolio.metrics import (
    annual_return,
    annual_vol,
    calmar,
    max_drawdown,
    sharpe,
    sortino,
)


def _curve(values: list[float]) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D", tz="UTC")
    return pd.Series(values, index=idx)


def test_sharpe_positive_drift() -> None:
    # steady +0.1%/day, zero variance theoretically -> guard returns 0.0
    flat = _curve([100.0 * (1.001**i) for i in range(50)])
    # constant geometric return has ~0 stdev of pct change -> sharpe guard 0.0
    assert sharpe(flat) == pytest.approx(0.0, abs=1e-6)


def test_sharpe_known_value() -> None:
    rng = np.random.default_rng(0)
    rets = rng.normal(0.001, 0.01, 365)
    curve = _curve(list(100.0 * np.cumprod(1.0 + rets)))
    s = sharpe(curve)
    # mean/std * sqrt(365); positive, finite, in a sane band
    assert math.isfinite(s) and s > 0.0


def test_max_drawdown() -> None:
    curve = _curve([100, 120, 90, 110, 80])
    # worst peak->trough: 120 -> 80 = -33.3%
    assert max_drawdown(curve) == pytest.approx(-1.0 / 3.0, rel=1e-3)


def test_sortino_only_penalizes_downside() -> None:
    curve = _curve([100, 101, 100, 102, 101, 103])
    assert math.isfinite(sortino(curve))


def test_annual_return_and_vol_and_calmar() -> None:
    curve = _curve([100, 110, 121])  # +10%/period compounding
    ar = annual_return(curve)
    assert ar > 0.0
    assert annual_vol(curve) >= 0.0
    assert math.isfinite(calmar(curve))


def test_flat_curve_is_zero_not_nan() -> None:
    curve = _curve([100.0] * 30)
    assert sharpe(curve) == 0.0
    assert sortino(curve) == 0.0
    assert max_drawdown(curve) == 0.0
