"""Tests for analytics/venue_premium.py (H14 pure library core).

Spec: docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md
"""

import numpy as np
import pandas as pd

from analytics.venue_premium import (
    build_premium_series,
    causal_zscore,
    label_changes,
    label_levels,
)


def _series(vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=range(len(vals)), dtype=float)


def test_premium_removes_the_peg_deviation() -> None:
    cb = _series([100.0])
    bn = _series([100.0])
    usdt = _series([0.99])
    out = build_premium_series(cb, bn, usdt)
    assert out["prem_raw"].iloc[0] == 0.0
    # peg-adjusted: 100 / (100 * 0.99) - 1 == +1.0101%
    assert abs(out["prem_adj"].iloc[0] - 0.010101) < 1e-5
    assert abs(out["peg_dev"].iloc[0] + 0.01) < 1e-12


def test_causal_zscore_never_uses_the_future() -> None:
    rng = np.random.default_rng(0)
    base = _series(list(rng.normal(size=200)))
    z = causal_zscore(base, window=90)

    bumped = base.copy()
    k = 150
    bumped.iloc[k] += 10.0
    z2 = causal_zscore(bumped, window=90)

    # Everything strictly BEFORE the perturbed day is untouched.
    pd.testing.assert_series_equal(z.iloc[:k], z2.iloc[:k])
    # And the perturbation IS visible at k, proving the test can fail.
    assert z.iloc[k] != z2.iloc[k]


def test_label_levels_uses_the_pre_registered_thresholds() -> None:
    z = _series([-2.0, -1.0, 0.0, 1.0, 2.0, float("nan")])
    out = label_levels(z)
    assert list(out[:5]) == [
        "depressed",
        "depressed",
        "neutral",
        "elevated",
        "elevated",
    ]
    assert pd.isna(out.iloc[5])


def test_build_premium_series_aligns_on_shared_index() -> None:
    # bn_btc has an extra index entry (3) that cb_btc/cb_usdt don't share;
    # the output must be limited to the intersection of cb_btc and bn_btc.
    cb = pd.Series([100.0, 101.0, 102.0], index=[0, 1, 2], dtype=float)
    bn = pd.Series([100.0, 101.0, 102.0, 103.0], index=[0, 1, 2, 3], dtype=float)
    usdt = pd.Series([1.0, 1.0, 1.0], index=[0, 1, 2], dtype=float)
    out = build_premium_series(cb, bn, usdt)
    assert list(out.index) == [0, 1, 2]
    assert list(out.columns) == ["prem_raw", "prem_adj", "peg_dev"]


def test_causal_zscore_is_nan_during_warmup() -> None:
    rng = np.random.default_rng(1)
    base = _series(list(rng.normal(size=50)))
    z = causal_zscore(base, window=90)
    # window=90 but only 50 points exist -> every value should be NaN warm-up.
    assert z.isna().all()


def test_causal_zscore_uses_prior_window_not_current_value() -> None:
    # A window of constant 1.0s followed by one huge outlier: the z-score at
    # the outlier's own index must be computed from the PRIOR window's mean/std
    # (both would be 0/NaN-guarded), not incorporate the outlier itself into
    # the window used to score it.
    vals = [1.0] * 90 + [1000.0]
    s = _series(vals)
    z = causal_zscore(s, window=90)
    # prior window (indices 0..89, all 1.0) has std=0 -> NaN guarded, so the
    # z at index 90 must be NaN (0/0 guarded via replace(0.0, nan)).
    assert pd.isna(z.iloc[90])


def test_label_changes_rising_and_falling_on_smoothed_series() -> None:
    # A4: label_changes applies a `span`-length rolling mean before diffing,
    # so short-horizon noise inside the smoothing window must NOT flip the
    # label — only the smoothed level's span-day change sign matters.
    vals = [10.0, 10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0, 20.0]
    s = _series(vals)
    out = label_changes(s, span=5)
    # First `2*span - 1` entries are NaN warm-up: the rolling mean itself
    # needs `span` points to produce its first value (valid from index
    # span-1), and diff(span) additionally needs a value `span` positions
    # earlier, so the first valid diff lands at index 2*span - 1.
    warmup = 2 * 5 - 1
    assert out.iloc[:warmup].isna().all()
    # By the end, the smoothed series has risen from ~10 to ~20.
    assert out.iloc[-1] == "rising"


def test_label_changes_preserves_nan_warmup() -> None:
    s = _series([1.0, 2.0, 3.0])
    out = label_changes(s, span=5)
    assert out.isna().all()
