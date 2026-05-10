"""Unit tests for `analytics.regime.classify_series`.

Covers the `slope_threshold` override used by `tools/regime_threshold_sweep.py`.
The default-threshold behaviour is exercised end-to-end by the replay tests
in `tests/test_regime_gate_replay.py`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.regime import classify_series


def _series_4h(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    arr = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "open_time": np.arange(n, dtype=int) * 4 * 60 * 60 * 1000,
            "high": arr * 1.001,
            "low": arr * 0.999,
            "close": arr,
        }
    )


def test_slope_threshold_higher_yields_fewer_trend_labels() -> None:
    """Tighter threshold → fewer bars labelled `trend`. Monotonic in threshold.

    Robust to high_vol overlay: only counts bars labelled exactly `trend`.
    """
    n = 200
    closes = [100.0 + 0.1 * i for i in range(n)]
    df = _series_4h(closes)

    loose = classify_series(df, "4h", slope_threshold=0.001)
    default = classify_series(df, "4h")
    strict = classify_series(df, "4h", slope_threshold=0.02)

    n_trend_loose = int((loose == "trend").sum())
    n_trend_default = int((default == "trend").sum())
    n_trend_strict = int((strict == "trend").sum())

    assert n_trend_loose >= n_trend_default >= n_trend_strict
    assert n_trend_loose > n_trend_strict


def test_slope_threshold_zero_labels_every_nonflat_as_trend() -> None:
    n = 200
    closes = (100.0 + np.arange(n) * 0.001).tolist()
    df = _series_4h(closes)
    aggressive = classify_series(df, "4h", slope_threshold=0.0)
    tail = aggressive.iloc[-50:]
    assert (tail.isin({"trend", "high_vol"})).all()


def test_slope_threshold_default_matches_explicit_default() -> None:
    n = 200
    rng = np.random.default_rng(42)
    closes = (100.0 + rng.normal(scale=0.5, size=n).cumsum()).tolist()
    df = _series_4h(closes)
    a = classify_series(df, "4h")
    b = classify_series(df, "4h", slope_threshold=0.005)
    pd.testing.assert_series_equal(a, b)
