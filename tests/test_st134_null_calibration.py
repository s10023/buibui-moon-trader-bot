"""ST134 section 4b — the corrected gate must still refuse a known-null family."""

import math

import numpy as np
import pytest

from analytics.sweep_guard import TrialPerf, _build_perf_matrix, _effective_trial_count
from tools.st134_null_calibration import null_pass_rate, sign_flip_family

_MS_PER_DAY = 86_400_000


def _family(k: int = 6, n: int = 80, drift: float = 0.30) -> list[TrialPerf]:
    rng = np.random.default_rng(4242)
    base = rng.normal(loc=drift, scale=1.0, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(k)]


def _moderately_correlated_family(
    k: int = 6, n: int = 80, seed: int = 123
) -> list[TrialPerf]:
    """Fix 5: on `_family()` rho sits at ~0.999 and `n_trials_eff` clamps to the floor
    on nearly every replicate, so the rho -> n_eff MAPPING itself — as opposed to the
    clamp — has no coverage from that fixture alone. Here each arm is a modest shared
    component plus independent noise of comparable scale, which lands rho well inside
    (0, 1) and `n_trials_eff` strictly inside `(2, k)`.
    """
    rng = np.random.default_rng(seed)
    shared = rng.normal(loc=0.30, scale=0.5, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [
        TrialPerf(f"tp{j}", list(shared + rng.normal(scale=1.0, size=n)), times)
        for j in range(k)
    ]


def _day_clustered_family(
    k: int = 6, days: int = 20, per_day: int = 5, seed: int = 4242
) -> list[TrialPerf]:
    """Fix 1 / Fix 5: 5 trades/day for 20 days sharing a per-day component — genuine
    within-day dependence, the shape `_effective_obs_count` exists to price. A family
    built one-trade-per-day (like `_family()` above) cannot exercise the `correct_obs`
    branch meaningfully because its design effect is trivially 1.0 by construction.
    """
    rng = np.random.default_rng(seed)
    times: list[int] = []
    day_component = rng.normal(loc=0.30, scale=0.8, size=days)
    base = np.empty(days * per_day)
    for d in range(days):
        for p in range(per_day):
            idx = d * per_day + p
            base[idx] = day_component[d] + rng.normal(scale=0.2)
            times.append(d * _MS_PER_DAY + p * 1000)
    return [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(k)]


def _underdeflated_family(
    k: int = 30,
    n: int = 200,
    drift: float = 0.1,
    idio_scale: float = 0.5,
    seed: int = 7,
) -> list[TrialPerf]:
    """Fix 4: deliberately constructed so the corrected gate is OVER-permissive — a
    positive control proving the harness can report a FAILING number, not just a
    passing one. Large `k` with per-arm idiosyncratic noise comparable to the shared
    component drives rho down enough that `n_trials_eff` clamps hard while the raw
    `k` stays large, so the correction and the raw count diverge sharply. This is a
    synthetic harness observation, not a finding about any real sweep cell.
    """
    rng = np.random.default_rng(seed)
    base = rng.normal(loc=drift, scale=1.0, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [
        TrialPerf(f"tp{j}", list(base + rng.normal(scale=idio_scale, size=n)), times)
        for j in range(k)
    ]


class TestSignFlipFamily:
    def test_preserves_shape_and_times(self) -> None:
        fam = _family()
        rng = np.random.default_rng(1)
        flipped = sign_flip_family(fam, rng)
        assert len(flipped) == len(fam)
        for a, b in zip(fam, flipped, strict=True):
            assert len(a.returns) == len(b.returns)
            assert a.times == b.times
            assert a.label == b.label

    def test_preserves_cross_arm_correlation(self) -> None:
        """All arms flip together, so the dependence structure survives."""
        fam = _family()
        rng = np.random.default_rng(2)
        flipped = sign_flip_family(fam, rng)
        rho_before, _ = _effective_trial_count(_build_perf_matrix(fam, 28))
        rho_after, _ = _effective_trial_count(_build_perf_matrix(flipped, 28))
        assert rho_after == pytest.approx(rho_before, abs=0.15)

    def test_destroys_the_drift(self) -> None:
        fam = _family(drift=1.0)
        means = []
        for s in range(40):
            flipped = sign_flip_family(fam, np.random.default_rng(s))
            means.append(float(np.mean(flipped[0].returns)))
        assert abs(float(np.mean(means))) < 0.25

    def test_magnitudes_are_untouched(self) -> None:
        fam = _family()
        flipped = sign_flip_family(fam, np.random.default_rng(3))
        assert sorted(abs(r) for r in fam[0].returns) == pytest.approx(
            sorted(abs(r) for r in flipped[0].returns)
        )

    def test_preserves_ragged_arm_lengths_regardless_of_order(self) -> None:
        """Fix 1: `_build_perf_matrix`'s own docstring says arms are NOT
        trade-aligned in production — different params mean different trades — so
        ragged input is the normal case. The old index-based construction truncated
        every arm to the shortest one's length, order-dependently; binning by day
        and looking each trade up by its own timestamp must not."""
        rng_data = np.random.default_rng(9)
        times_a = [0, _MS_PER_DAY, 2 * _MS_PER_DAY]
        times_b = [0, _MS_PER_DAY, 2 * _MS_PER_DAY, 3 * _MS_PER_DAY, 4 * _MS_PER_DAY]
        a = TrialPerf("a", list(rng_data.normal(size=3)), times_a)
        b = TrialPerf("b", list(rng_data.normal(size=5)), times_b)
        flipped_ab = sign_flip_family([a, b], np.random.default_rng(1))
        flipped_ba = sign_flip_family([b, a], np.random.default_rng(1))
        assert [len(t.returns) for t in flipped_ab] == [3, 5]
        assert [len(t.returns) for t in flipped_ba] == [5, 3]

    def test_same_day_trades_share_one_sign(self) -> None:
        """Fix 1: two trades on the same UTC day must get the SAME sign — the whole
        reason for binning by day rather than by position."""
        times = [0, 1000, 2 * _MS_PER_DAY]  # first two share a day; third is new
        fam = [TrialPerf("a", [1.0, 1.0, 1.0], times)]
        flipped = sign_flip_family(fam, np.random.default_rng(5))
        assert flipped[0].returns[0] == flipped[0].returns[1]


class TestNullPassRate:
    def test_uncorrected_gate_refuses_most_nulls(self) -> None:
        res = null_pass_rate(
            _family(), correct_trials=False, n_replicates=40, n_splits=4
        )
        assert 0.0 <= res.rate <= 0.10
        assert res.evaluated == 40
        assert res.skipped == 0

    def test_corrected_gate_also_refuses_most_nulls(self) -> None:
        res = null_pass_rate(
            _family(), correct_trials=True, n_replicates=40, n_splits=4
        )
        assert 0.0 <= res.rate <= 0.10
        assert res.evaluated == 40
        assert res.skipped == 0

    def test_is_deterministic_under_a_seed(self) -> None:
        a = null_pass_rate(
            _family(), correct_trials=True, n_replicates=20, n_splits=4, seed=7
        )
        b = null_pass_rate(
            _family(), correct_trials=True, n_replicates=20, n_splits=4, seed=7
        )
        assert a == b

    def test_corrected_gate_can_exceed_the_abandon_threshold(self) -> None:
        """Fix 4: a kill-switch must be able to report FAILING, not only passing —
        the specificity tests above cannot show that on their own (a stub returning
        0.0 would satisfy them too). Deliberately under-deflated synthetic family;
        not a claim about any real sweep cell."""
        fam = _underdeflated_family()
        corrected = null_pass_rate(
            fam, correct_trials=True, n_replicates=200, n_splits=14
        )
        uncorrected = null_pass_rate(
            fam, correct_trials=False, n_replicates=200, n_splits=14
        )
        assert corrected.rate > 0.10
        assert uncorrected.rate <= 0.10

    def test_correct_trials_runs_the_unclamped_path(self) -> None:
        """Fix 5: on `_family()` rho is ~0.999 and `n_trials_eff` clamps to the floor
        on every replicate, so the correction is exercised only where the clamp
        overrides the measurement. This family's rho lands strictly inside `(2, k)`."""
        fam = _moderately_correlated_family()
        rho, n_eff = _effective_trial_count(_build_perf_matrix(fam, 28))
        assert 2.0 < n_eff < float(len(fam))
        res = null_pass_rate(fam, correct_trials=True, n_replicates=200, n_splits=14)
        assert res.evaluated == 200
        assert 0.0 <= res.rate <= 1.0

    def test_correct_obs_runs_on_a_genuinely_clustered_family(self) -> None:
        """Fix 5: `correct_obs=True` was executed by no test at all. A family with a
        real per-day component gives `_effective_obs_count` something to price."""
        fam = _day_clustered_family()
        res = null_pass_rate(
            fam, correct_trials=False, correct_obs=True, n_replicates=200, n_splits=4
        )
        assert res.evaluated == 200
        assert res.skipped == 0
        assert not math.isnan(res.rate)

    def test_correct_obs_skip_is_visible_not_scored_as_a_refusal(self) -> None:
        """Fix 3: a skipped replicate must show up as `skipped`, never silently as a
        non-pass folded into `rate`. Day-clustering this family down to ~20.7
        effective observations against `min_obs=28` (`n_splits=14`) skips every
        replicate — `rate` must read NaN, not 0.0, so "refused everything" and
        "evaluated nothing" cannot be confused."""
        fam = _day_clustered_family()
        res = null_pass_rate(
            fam, correct_trials=False, correct_obs=True, n_replicates=200, n_splits=14
        )
        assert res.evaluated == 0
        assert res.skipped == 200
        assert math.isnan(res.rate)

    def test_empty_family_does_not_raise(self) -> None:
        """Minor: `max()` on an empty family raised ValueError before this guard."""
        res = null_pass_rate([], correct_trials=True, n_replicates=10, n_splits=4)
        assert res.evaluated == 0
        assert res.skipped == 10
        assert math.isnan(res.rate)

    def test_single_arm_family_does_not_raise(self) -> None:
        """Minor: `statistics.variance` on a length-1 family raised StatisticsError
        before this guard — mirrors `evaluate_commit_gate`'s own `n_trials < 2`."""
        times = [i * _MS_PER_DAY for i in range(80)]
        fam = [TrialPerf("solo", [1.0] * 80, times)]
        res = null_pass_rate(fam, correct_trials=True, n_replicates=10, n_splits=4)
        assert res.evaluated == 0
        assert math.isnan(res.rate)
