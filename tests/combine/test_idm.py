from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analytics.combine.idm import causal_idm_series, idm_value, static_idm


def test_idm_zero_corr_equal_weights() -> None:
    # IDM = 1/sqrt(0.5^2 + 0.5^2 + 0) = 1/sqrt(0.5) = 1.41421356
    assert idm_value(0.5, 0.5, 0.0, cap=2.5) == math.sqrt(2.0)


def test_idm_perfect_corr_is_one() -> None:
    # IDM = 1/sqrt(0.25 + 0.25 + 0.5) = 1/sqrt(1.0) = 1.0 (no diversification)
    assert idm_value(0.5, 0.5, 1.0, cap=2.5) == 1.0


def test_idm_caps_when_denominator_nonpositive() -> None:
    # corr = -1, equal weights -> var = 0 -> would be +inf -> capped
    assert idm_value(0.5, 0.5, -1.0, cap=2.5) == 2.5


def test_idm_monotone_decreasing_in_corr() -> None:
    lo = idm_value(0.5, 0.5, 0.0, cap=2.5)
    mid = idm_value(0.5, 0.5, 0.37, cap=2.5)
    hi = idm_value(0.5, 0.5, 0.9, cap=2.5)
    assert lo > mid > hi


def test_static_idm_independent_streams_near_sqrt2() -> None:
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000)
    b = rng.standard_normal(2000)
    # ~zero correlation -> IDM ~ 1.414
    assert 1.30 < static_idm(a, b, 0.5, 0.5, cap=2.5) < 1.50


def test_static_idm_identical_streams_is_one() -> None:
    a = np.linspace(-1.0, 1.0, 500)
    # corr == 1 -> IDM == 1.0
    assert abs(static_idm(a, a, 0.5, 0.5, cap=2.5) - 1.0) < 1e-9


def test_static_idm_ignores_joint_zero_warmup() -> None:
    rng = np.random.default_rng(1)
    a = np.concatenate([np.zeros(100), rng.standard_normal(900)])
    b = np.concatenate([np.zeros(100), rng.standard_normal(900)])
    # leading joint-zero warm-up must not inflate the correlation toward 1
    assert 1.30 < static_idm(a, b, 0.5, 0.5, cap=2.5) < 1.50


def test_causal_idm_series_warmup_is_neutral_one() -> None:
    idx = pd.date_range("2021-01-01", periods=600, freq="D")
    rng = np.random.default_rng(2)
    a = rng.standard_normal(600)
    b = rng.standard_normal(600)
    s = causal_idm_series(
        a, b, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx
    )
    assert len(s) == 600
    # before min_periods of trailing data the IDM is the neutral 1.0
    assert (s.iloc[:120] == 1.0).all()
    # once warmed, an ~uncorrelated pair lifts IDM above 1.0
    assert s.iloc[-1] > 1.0


def test_causal_idm_series_identical_streams_trends_to_one() -> None:
    idx = pd.date_range("2021-01-01", periods=600, freq="D")
    rng = np.random.default_rng(3)
    a = rng.standard_normal(600)
    s = causal_idm_series(
        a, a, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx
    )
    # perfectly correlated sleeves -> no diversification -> IDM ~ 1.0 once warmed
    assert abs(s.iloc[-1] - 1.0) < 1e-6


def test_causal_idm_series_is_causal_no_lookahead() -> None:
    idx = pd.date_range("2021-01-01", periods=600, freq="D")
    rng = np.random.default_rng(4)
    a = rng.standard_normal(600)
    b = rng.standard_normal(600)
    base = causal_idm_series(
        a, b, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx
    )
    a2 = a.copy()
    a2[400] += 5.0  # perturb a future return
    after = causal_idm_series(
        a2, b, 0.5, 0.5, window=365, min_periods=120, cap=2.5, index=idx
    )
    # IDM at day t uses corr through t-1; a change at 400 cannot move IDM[:401]
    pd.testing.assert_series_equal(base.iloc[:401], after.iloc[:401], check_names=False)
