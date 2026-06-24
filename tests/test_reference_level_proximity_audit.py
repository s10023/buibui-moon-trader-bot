"""Tests for tools.reference_level_proximity_audit (read-only proximity audit)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analytics.backtest.engine import _compute_atr14
from tools.reference_level_proximity_audit import (
    CohortVerdict,
    _atr14_series,
    _band_of,
    _two_sample_lift_ci,
    evaluate_primary,
    normalize_backtest,
    normalize_live,
    tag_entries,
)


def _day_ms(date_str: str) -> int:
    return int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1000)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def test_band_of_thresholds() -> None:
    s = pd.Series([0.1, 0.3, 0.7, 2.0, np.nan])
    bands = _band_of(s, (0.25, 0.5, 1.0))
    assert list(bands) == ["<=0.25", "<=0.5", "<=1.0", ">1.0", "n/a"]


def test_atr14_series_matches_engine() -> None:
    rng = np.random.default_rng(0)
    n = 60
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    t = pd.DataFrame({"high": high, "low": low, "close": close})
    series = _atr14_series(t)
    for idx in range(14, n):
        expected = _compute_atr14(high, low, close, idx)
        assert series.iloc[idx] == pytest.approx(expected)


def test_two_sample_lift_ci_positive() -> None:
    rng = np.random.default_rng(0)
    near = rng.normal(1.0, 0.2, 80)
    far = rng.normal(0.0, 0.2, 80)
    lo, hi = _two_sample_lift_ci(near, far, n_boot=1000, seed=1)
    assert lo > 0


def test_two_sample_lift_ci_overlap() -> None:
    rng = np.random.default_rng(1)
    near = rng.normal(0.0, 0.5, 80)
    far = rng.normal(0.0, 0.5, 80)
    lo, hi = _two_sample_lift_ci(near, far, n_boot=1000, seed=1)
    assert lo < 0 < hi


# --------------------------------------------------------------------------- #
# source normalization                                                         #
# --------------------------------------------------------------------------- #


def test_normalize_live_maps_columns() -> None:
    raw = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "tf": ["1h"],
            "direction": ["long"],
            "entry_price": [100.0],
            "candle_ts_ms": [_day_ms("2024-06-12")],
            "outcome_r": [0.5],
        }
    )
    out = normalize_live(raw)
    assert list(out.columns) == [
        "symbol",
        "tf",
        "direction",
        "entry_price",
        "ts_ms",
        "r",
    ]
    assert out.loc[0, "r"] == 0.5
    assert out.loc[0, "ts_ms"] == _day_ms("2024-06-12")


def test_normalize_backtest_drops_nulls() -> None:
    raw = pd.DataFrame(
        {
            "symbol": ["BTCUSDT", "ETHUSDT"],
            "timeframe": ["1h", "4h"],
            "direction": ["long", "short"],
            "entry_price": [100.0, None],
            "signal_time": [_day_ms("2024-06-12"), _day_ms("2024-06-12")],
            "pnl_r": [0.5, 0.1],
        }
    )
    out = normalize_backtest(raw)
    assert len(out) == 1  # the null entry_price row is dropped
    assert out.loc[0, "symbol"] == "BTCUSDT"


# --------------------------------------------------------------------------- #
# tagging                                                                      #
# --------------------------------------------------------------------------- #


def _daily() -> pd.DataFrame:
    # PDL for a 2024-06-12 entry = 06-11 low = 96; PDH = 116.
    rows = [
        ("2024-06-10", 100.0, 110.0, 90.0, 105.0),
        ("2024-06-11", 105.0, 116.0, 96.0, 100.0),
        ("2024-06-12", 100.0, 102.0, 95.0, 101.0),
    ]
    return pd.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "timeframe": "1d",
                "open_time": _day_ms(d),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
            }
            for d, o, h, lo, c in rows
        ]
    )


def _h1() -> pd.DataFrame:
    base = pd.Timestamp("2024-06-12 00:00", tz="UTC")
    rows = []
    for h in range(13):
        low, high, close = 97.5, 98.5, 98.0
        if h == 9:  # recent bar wicks below PDL (96)
            low, high, close = 95.5, 98.0, 97.0
        if h == 10:  # entry bar: reclaims above 96
            low, high, close = 96.8, 98.0, 97.5
        rows.append(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "open_time": int((base + pd.Timedelta(hours=h)).timestamp() * 1000),
                "open": close,
                "high": high,
                "low": low,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_tag_entries_near_pdl_sweep_and_far_control() -> None:
    h1 = _h1()
    base = pd.Timestamp("2024-06-12 00:00", tz="UTC")
    ts_10 = int((base + pd.Timedelta(hours=10)).timestamp() * 1000)
    ts_11 = int((base + pd.Timedelta(hours=11)).timestamp() * 1000)
    entries = pd.DataFrame(
        [
            # near PDL (96), long, swept-and-reclaimed
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "direction": "long",
                "entry_price": 96.5,
                "ts_ms": ts_10,
                "r": 0.5,
            },
            # far from every level
            {
                "symbol": "BTCUSDT",
                "tf": "1h",
                "direction": "long",
                "entry_price": 200.0,
                "ts_ms": ts_11,
                "r": -0.2,
            },
        ]
    )
    tagged = tag_entries(
        entries,
        {"BTCUSDT": _daily()},
        {("BTCUSDT", "1h"): h1},
        sweep_lookback=3,
    )
    near = tagged[tagged["entry_price"] == 96.5].iloc[0]
    far = tagged[tagged["entry_price"] == 200.0].iloc[0]

    assert near["near_name"] == "PDL"
    assert near["prim_sweep"]
    assert near["prim_band"] in ("<=0.25", "<=0.5", "<=1.0")
    assert near["near_band"] in ("<=0.25", "<=0.5", "<=1.0")

    # Far entry is excluded from the primary cohort by the band filter (it is far
    # from the prior-period extreme too), regardless of incidental sweep structure.
    assert far["near_band"] == ">1.0"
    assert far["prim_band"] == ">1.0"


# --------------------------------------------------------------------------- #
# verdict                                                                      #
# --------------------------------------------------------------------------- #


def _cohort(
    direction: str,
    n: int,
    r_mean: float,
    *,
    prim_band: str,
    prim_sweep: bool,
    near_band: str,
    seed: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "direction": [direction] * n,
            "prim_band": [prim_band] * n,
            "prim_sweep": [prim_sweep] * n,
            "near_band": [near_band] * n,
            "r": rng.normal(r_mean, 0.3, n),
        }
    )


def test_evaluate_primary_build_and_insufficient() -> None:
    near_long = _cohort(
        "long", 60, 0.5, prim_band="<=0.5", prim_sweep=True, near_band="<=0.5", seed=1
    )
    far_long = _cohort(
        "long", 60, -0.2, prim_band=">1.0", prim_sweep=False, near_band=">1.0", seed=2
    )
    near_short = _cohort(
        "short", 10, 0.4, prim_band="<=0.5", prim_sweep=True, near_band="<=0.5", seed=3
    )
    tagged = pd.concat([near_long, far_long, near_short], ignore_index=True)

    verdicts = evaluate_primary(
        tagged, min_n=30, bar=0.05, alpha=0.05, n_boot=500, seed=7
    )
    assert isinstance(verdicts[0], CohortVerdict)
    assert verdicts[0].decision == "BUILD"  # long: strong near edge + positive lift
    assert verdicts[1].decision == "INSUFFICIENT"  # short: n=10 < min_n


def test_evaluate_primary_no_edge_when_flat() -> None:
    near_long = _cohort(
        "long", 60, 0.0, prim_band="<=0.5", prim_sweep=True, near_band="<=0.5", seed=4
    )
    far_long = _cohort(
        "long", 60, 0.0, prim_band=">1.0", prim_sweep=False, near_band=">1.0", seed=5
    )
    tagged = pd.concat([near_long, far_long], ignore_index=True)
    verdicts = evaluate_primary(
        tagged, min_n=30, bar=0.05, alpha=0.05, n_boot=500, seed=7
    )
    assert verdicts[0].decision == "NO-EDGE"  # near avg ~0, does not clear +bar
