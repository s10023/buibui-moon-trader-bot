"""Fractional trial and observation counts type-check and behave continuously.

ST134's two corrections produce effective counts, which are floats. These pin that
the guards accept them, that an integral float is identical to the int, and that the
result moves monotonically between neighbouring integers — i.e. the widening exposed
a continuous function rather than a step one.
"""

import pytest

from analytics.research_guards import deflated_sharpe_ratio, expected_max_sharpe
from analytics.research_guards.psr import probabilistic_sharpe_ratio


class TestExpectedMaxSharpeAcceptsFloat:
    def test_integral_float_matches_int(self) -> None:
        assert expected_max_sharpe(9.0, 0.04) == pytest.approx(
            expected_max_sharpe(9, 0.04)
        )

    def test_monotone_between_integers(self) -> None:
        lo = expected_max_sharpe(3, 0.04)
        mid = expected_max_sharpe(3.5, 0.04)
        hi = expected_max_sharpe(4, 0.04)
        assert lo < mid < hi

    def test_below_two_is_no_deflation(self) -> None:
        assert expected_max_sharpe(1.9, 0.04) == 0.0


class TestPsrAcceptsFloat:
    def test_integral_float_matches_int(self) -> None:
        assert probabilistic_sharpe_ratio(0.5, 40.0) == pytest.approx(
            probabilistic_sharpe_ratio(0.5, 40)
        )

    def test_fewer_effective_obs_lowers_confidence(self) -> None:
        assert probabilistic_sharpe_ratio(0.5, 20.0) < probabilistic_sharpe_ratio(
            0.5, 40.0
        )

    def test_below_two_still_raises(self) -> None:
        with pytest.raises(ValueError):
            probabilistic_sharpe_ratio(0.5, 1.5)


class TestDsrAcceptsFloat:
    def test_fractional_trials_and_obs(self) -> None:
        strict = deflated_sharpe_ratio(0.5, 40.0, n_trials=9.0, sr_variance=0.04)
        loose = deflated_sharpe_ratio(0.5, 40.0, n_trials=2.6, sr_variance=0.04)
        assert loose > strict

    def test_fewer_effective_obs_lowers_dsr(self) -> None:
        many = deflated_sharpe_ratio(0.5, 40.0, n_trials=9.0, sr_variance=0.04)
        few = deflated_sharpe_ratio(0.5, 24.0, n_trials=9.0, sr_variance=0.04)
        assert few < many
