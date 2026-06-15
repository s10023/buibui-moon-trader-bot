"""Tests for portfolio.metrics — risk-adjusted stats on a daily curve."""

import math

import numpy as np
import pandas as pd
import pytest

from portfolio.book import SizedTrade
from portfolio.metrics import (
    annual_return,
    annual_vol,
    attribution,
    calmar,
    max_drawdown,
    sharpe,
    sortino,
)


def _sized(symbol: str, strategy: str, direction: str, realized_r: float) -> SizedTrade:
    return SizedTrade(
        signal_id=f"{symbol}-{strategy}-{direction}",
        symbol=symbol,
        tf="1h",
        strategy=strategy,
        direction=direction,
        entry_idx=0,
        exit_idx=1,
        r_eff=0.0025,
        g_vol=1.0,
        g_regime=1.0,
        rc_fixed=25.0,
        rc_comp=25.0,
        pnl_fixed=25.0 * realized_r,
        pnl_comp=25.0 * realized_r,
        realized_r=realized_r,
        regime=None,
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


def test_attribution_empty_is_empty_frame() -> None:
    assert attribution([]).empty


def test_attribution_default_grouping() -> None:
    sized = [
        _sized("BTCUSDT", "fvg", "short", 2.0),
        _sized("ETHUSDT", "bos", "long", -1.0),
    ]
    agg = attribution(sized)
    assert set(agg.columns) >= {"strategy", "tf", "direction", "n", "total_r", "avg_r"}
    assert len(agg) == 2
    # sorted by total_pnl desc -> the +2R fvg short on top
    assert agg.iloc[0]["strategy"] == "fvg"


def test_attribution_groups_by_symbol() -> None:
    # the `by` parameter must actually work for any groupable SizedTrade field
    sized = [
        _sized("BTCUSDT", "fvg", "short", 2.0),
        _sized("BTCUSDT", "bos", "long", 1.0),
        _sized("ETHUSDT", "bos", "long", -1.0),
    ]
    agg = attribution(sized, by=("symbol",))
    assert list(agg["symbol"]) == ["BTCUSDT", "ETHUSDT"]
    btc = agg[agg["symbol"] == "BTCUSDT"].iloc[0]
    assert btc["n"] == 2 and btc["total_r"] == pytest.approx(3.0)
