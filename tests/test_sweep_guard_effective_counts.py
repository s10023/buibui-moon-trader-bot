"""ST134 correction (a): the tp_r arms are not independent trials.

The arms share entries and stops and differ only in target, so counting them as
independent searches overstates the DSR benchmark. These pin the bounds the
pre-registration commits to, and pin that the formula is DELEGATED rather than
re-spelled — a drifting second copy is the defect the spec's own section 1e is about.
"""

import numpy as np
import pandas as pd
import pytest

from analytics.forecast import effective_independent_series
from analytics.sweep_guard import _effective_trial_count


class TestEffectiveTrialCount:
    def test_identical_arms_floor_at_two(self) -> None:
        col = np.array([1.0, -0.5, 2.0, 0.25, -1.0, 0.75, 1.5, 0.0])
        perf = np.column_stack([col, col, col, col])
        rho, n_eff = _effective_trial_count(perf)
        assert rho == pytest.approx(1.0)
        assert n_eff == 2.0  # k/(1+(k-1)*1) == 1.0, clamped to the floor

    def test_independent_arms_keep_the_raw_count(self) -> None:
        rng = np.random.default_rng(20260910)
        perf = rng.normal(size=(400, 5))
        rho, n_eff = _effective_trial_count(perf)
        assert abs(rho) < 0.15
        assert n_eff == pytest.approx(5.0, abs=0.9)
        assert n_eff <= 5.0  # never above the raw family

    def test_negative_rho_clamps_to_the_raw_count(self) -> None:
        col = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
        perf = np.column_stack([col, -col])
        _, n_eff = _effective_trial_count(perf)
        assert n_eff == 2.0

    def test_single_arm_is_not_deflated(self) -> None:
        perf = np.array([[1.0], [2.0], [3.0]])
        rho, n_eff = _effective_trial_count(perf)
        assert rho == 0.0
        assert n_eff == 1.0

    def test_delegates_to_effective_independent_series(self) -> None:
        """The n_eff we apply is the one that shared function returns, clamped.

        Mutation guard: if _effective_trial_count grows its own copy of
        k/(1+(k-1)*rho), this drifts the moment either spelling changes.
        """
        rng = np.random.default_rng(7)
        base = rng.normal(size=400)
        perf = np.column_stack([base + 0.4 * rng.normal(size=400) for _ in range(6)])
        _, ours = _effective_trial_count(perf)
        theirs, _ = effective_independent_series(
            {f"arm{j}": pd.Series(perf[:, j]) for j in range(perf.shape[1])}
        )
        assert ours == pytest.approx(min(max(theirs, 2.0), 6.0))

    def test_rho_round_trips_from_n_eff(self) -> None:
        """rho is recovered from n_eff, so the two can never disagree."""
        rng = np.random.default_rng(11)
        base = rng.normal(size=500)
        k = 4
        perf = np.column_stack([base + 2.0 * rng.normal(size=500) for _ in range(k)])
        rho, n_eff = _effective_trial_count(perf)
        # n_eff was clamped only if it left [2, k]; here it does not.
        assert 2.0 < n_eff < float(k)
        assert rho == pytest.approx((k / n_eff - 1.0) / (k - 1), rel=1e-9)
