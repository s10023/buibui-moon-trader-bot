from __future__ import annotations

import math

from analytics.combine.idm import idm_value


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
