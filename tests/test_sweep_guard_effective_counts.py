"""ST134 correction (a): the tp_r arms are not independent trials.

The arms share entries and stops and differ only in target, so counting them as
independent searches overstates the DSR benchmark. These pin the bounds the
pre-registration commits to, and pin that the formula is DELEGATED rather than
re-spelled — a drifting second copy is the defect the spec's own section 1e is about.
"""

import math

import numpy as np
import pandas as pd
import pytest

from analytics.forecast import effective_independent_series
from analytics.research_guards import min_track_record_length
from analytics.sweep_guard import (
    TrialPerf,
    _effective_obs_count,
    _effective_trial_count,
    _trial_sharpe,
    evaluate_commit_gate,
)


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
        """⚠ k=2, where the floor (2.0) and the ceiling (k) COINCIDE — so this
        pins neither bound on its own. It is kept for the rho half: a perfectly
        anti-correlated pair takes ``effective_independent_series``' non-positive
        denominator branch, which returns ``float(k)`` and inverted to exactly
        ``rho = 0.0`` until rho was measured directly. The ceiling proper is
        pinned at k=4 below.
        """
        col = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
        perf = np.column_stack([col, -col])
        rho, n_eff = _effective_trial_count(perf)
        assert rho == pytest.approx(-1.0)
        assert n_eff == 2.0

    def test_the_ceiling_binds_above_two_arms(self) -> None:
        """Mild negative correlation at k=4: ``1 + 3*rho > 0``, so the raw
        ``n_eff`` is genuinely ABOVE k and the ceiling — not the floor, and not a
        degenerate fallback — is what returns it to 4.0.
        """
        rng = np.random.default_rng(3)
        common = rng.normal(size=40)
        perf = np.column_stack(
            [rng.normal(size=40) + ((-1) ** j) * 0.35 * common for j in range(4)]
        )
        rho, n_eff = _effective_trial_count(perf)
        assert rho < 0.0
        assert 1.0 + 3.0 * rho > 0.0  # not the degenerate denominator branch
        assert 4.0 / (1.0 + 3.0 * rho) > 4.0  # unclamped, it would INFLATE
        assert n_eff == 4.0

    def test_an_unmeasurable_rho_is_nan_not_zero(self) -> None:
        """Constant columns make every pairwise correlation NaN. "We could not
        measure it" must not read as "we measured zero correlation" — that value
        IS §4a's decision statistic, and averaging phantom zeros into the median
        pulls the CI toward the bar from the licensed side.
        """
        rho, n_eff = _effective_trial_count(np.zeros((10, 3)))
        assert math.isnan(rho)
        assert n_eff == 3.0  # unmeasurable ⇒ no deflation, the fail-safe direction

    def test_single_arm_is_not_deflated(self) -> None:
        perf = np.array([[1.0], [2.0], [3.0]])
        rho, n_eff = _effective_trial_count(perf)
        assert math.isnan(rho)  # no pair exists, so nothing was measured
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


_MS_PER_DAY = 86_400_000


class TestEffectiveObsCount:
    def test_all_trades_one_day_is_one_effective_observation(self) -> None:
        returns = [1.0, -1.0, 0.5, -0.5, 2.0, -2.0, 1.5, -1.5]
        times = [i * 3_600_000 for i in range(8)]  # all inside one UTC day
        deff, n_eff = _effective_obs_count(TrialPerf("a", returns, times))
        assert n_eff == pytest.approx(1.0)
        assert deff > 1.0

    def test_one_trade_per_day_is_not_deflated(self) -> None:
        returns = [1.0, -1.0, 0.5, -0.5, 2.0, -2.0, 1.5, -1.5]
        times = [i * _MS_PER_DAY for i in range(8)]
        deff, n_eff = _effective_obs_count(TrialPerf("a", returns, times))
        assert deff == pytest.approx(1.0)
        assert n_eff == pytest.approx(8.0)

    def test_clustered_days_sit_between(self) -> None:
        # Four days, four trades each, correlated within a day.
        returns: list[float] = []
        times: list[int] = []
        for day, level in enumerate([2.0, -2.0, 2.0, -2.0]):
            for slot in range(4):
                returns.append(level + 0.01 * slot)
                times.append(day * _MS_PER_DAY + slot * 3_600_000)
        deff, n_eff = _effective_obs_count(TrialPerf("a", returns, times))
        assert 1.0 < n_eff < 16.0
        assert deff > 1.0

    def test_never_exceeds_the_raw_count(self) -> None:
        returns = [1.0, -1.0, 0.5, -0.5]
        times = [i * _MS_PER_DAY for i in range(4)]
        _, n_eff = _effective_obs_count(TrialPerf("a", returns, times))
        assert n_eff <= 4.0

    def test_empty_is_zero(self) -> None:
        deff, n_eff = _effective_obs_count(TrialPerf("a", [], []))
        assert deff == 1.0
        assert n_eff == 0.0


def _correlated_trials(k: int = 6, n: int = 60) -> list[TrialPerf]:
    """k arms sharing a common trade population — the real grid's shape."""
    rng = np.random.default_rng(1234)
    base = rng.normal(loc=0.18, scale=1.0, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [
        TrialPerf(f"tp{j}", list(base + 0.02 * j + 0.05 * rng.normal(size=n)), times)
        for j in range(k)
    ]


class TestCorrectionsAreOptIn:
    def test_default_path_is_unchanged(self) -> None:
        """Byte-identical to today: the flags default off."""
        trials = _correlated_trials()
        v = evaluate_commit_gate(trials[-1], trials, n_grid=6, n_splits=4)
        assert v.n_trials_eff is None
        assert v.n_obs_eff is None
        assert v.rho is None
        assert v.design_effect is None

    def test_trial_correction_raises_dsr(self) -> None:
        trials = _correlated_trials()
        raw = evaluate_commit_gate(trials[-1], trials, n_grid=6, n_splits=4)
        fixed = evaluate_commit_gate(
            trials[-1], trials, n_grid=6, n_splits=4, correct_trials=True
        )
        assert fixed.n_trials_eff is not None and fixed.n_trials_eff < 6.0
        # Value pin, not just the bound: a swapped `rho` (~0.998, the mean pairwise
        # correlation this near-identical-arms fixture induces) would also satisfy
        # `n_trials_eff < 6.0` above, so the bound alone cannot catch a transposed
        # keyword — this pins the OTHER field too.
        assert fixed.rho is not None and fixed.rho == pytest.approx(0.998, rel=0.1)
        assert raw.dsr is not None and fixed.dsr is not None
        assert fixed.dsr >= raw.dsr

    def test_obs_correction_lowers_dsr(self) -> None:
        # Two trades per UTC day sharing a common per-day level plus small jitter,
        # so the day key clusters them. Pairing bare i.i.d. draws by timestamp alone
        # (the brief's original fixture) does NOT cluster them — cluster_stats'
        # one-way ANOVA measures correlation in the VALUES, and two independent
        # draws labelled with the same day carry none; measured across 30 seeds,
        # that shape landed design_effect == 1.0 (ICC clamped to 0) 12/30 times,
        # including seed 99, so `fixed.n_obs_eff < n` failed here 40% of the time.
        rng = np.random.default_rng(99)
        n_days = 30
        day_level = rng.normal(loc=0.3, scale=1.0, size=n_days)
        base = np.repeat(day_level, 2) + 0.05 * rng.normal(size=n_days * 2)
        n = len(base)
        times = [(i // 2) * _MS_PER_DAY for i in range(n)]
        trials = [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(6)]
        raw = evaluate_commit_gate(trials[-1], trials, n_grid=6, n_splits=4)
        fixed = evaluate_commit_gate(
            trials[-1], trials, n_grid=6, n_splits=4, correct_obs=True
        )
        assert fixed.n_obs_eff is not None and fixed.n_obs_eff < float(n)
        # Value pin, not just the bound: a swapped `design_effect` (~2.0, the ratio
        # this 2-per-day fixture induces) would also satisfy `n_obs_eff < 60.0` above
        # — 60 raw trades / design_effect 2.0 == 30 effective, so the bound alone
        # cannot catch a transposed keyword — this pins the OTHER field too.
        assert fixed.design_effect is not None
        assert fixed.design_effect == pytest.approx(2.0, rel=0.1)
        assert fixed.n_obs_eff == pytest.approx(30.0, rel=0.1)
        assert raw.dsr is not None and fixed.dsr is not None
        assert fixed.dsr <= raw.dsr

    def test_obs_correction_can_push_a_cell_to_insufficient(self) -> None:
        """The pre-registered consequence: the floor now bites on effective trades."""
        rng = np.random.default_rng(5)
        n = 12
        base = rng.normal(loc=0.3, scale=1.0, size=n)
        times = [0 for _ in range(n)]  # every trade the same UTC day
        trials = [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(4)]
        fixed = evaluate_commit_gate(
            trials[-1], trials, n_grid=4, n_splits=4, correct_obs=True
        )
        assert fixed.decision == "INSUFFICIENT"
        assert any("effective" in r for r in fixed.reasons)

    def test_the_corrected_family_must_be_the_whole_family(self) -> None:
        """A truncated family under ``correct_trials`` loses the ``n_grid`` floor
        silently — the ceiling is ``k``, so the deflation would be against the
        truncation. It must raise rather than quietly under-deflate.
        """
        trials = _correlated_trials()
        with pytest.raises(ValueError, match="FULL family"):
            evaluate_commit_gate(
                trials[-1], trials, n_grid=99, n_splits=4, correct_trials=True
            )

    def test_the_uncorrected_path_still_accepts_a_truncated_family(self) -> None:
        """The floor is exactly what ``n_grid`` is FOR on the default path."""
        trials = _correlated_trials()
        v = evaluate_commit_gate(trials[-1], trials, n_grid=99, n_splits=4)
        assert v.n_trials == 6

    def test_raw_trial_guard_still_runs_first(self) -> None:
        """The floor of 2.0 must not route around the n_trials < 2 refusal."""
        t = _correlated_trials(k=1)[0]
        v = evaluate_commit_gate(t, [t], n_grid=1, n_splits=4, correct_trials=True)
        assert v.decision == "INSUFFICIENT"
        assert v.n_trials_eff is None


def _mintrl_binding_family() -> tuple[list[TrialPerf], int]:
    """60 trades over 30 UTC days, two per day sharing a per-day level.

    Tuned so the three counts bracket: ``n_obs_eff`` (~30) < ``MinTRL`` < ``n_obs``
    (60). That is the only configuration in which the two candidate counts for the
    third leg disagree, so it is what makes the leg's input observable at all.
    """
    rng = np.random.default_rng(2026)
    days, per_day = 30, 2
    level = rng.normal(loc=0.0, scale=1.0, size=days)
    base = np.repeat(level, per_day) + 0.05 * rng.normal(size=days * per_day)
    base = (base - base.mean()) / base.std(ddof=1) + 0.248
    times = [
        (i // per_day) * _MS_PER_DAY + (i % per_day) * 3_600_000
        for i in range(days * per_day)
    ]
    trials = [TrialPerf(f"tp{j}", list(base + 0.01 * j), times) for j in range(6)]
    return trials, len(base)


class TestTheMinTrlLegReadsTheEffectiveCount:
    """C1 / spec §6.4: the bar is ``DSR >= 0.95 ∧ PBO <= 0.5 ∧ n_obs_eff >= MinTRL``.

    ⚠ Measured while writing these: with ``MINTRL_CONFIDENCE == DSR_THRESHOLD`` and
    both legs assuming the same moments, ``DSR >= 0.95`` IMPLIES the MinTRL leg under
    EITHER count — DSR's z carries ``sr - sr0`` with ``sr0 >= 0`` where MinTRL's
    carries ``sr``, so the MinTRL z is never the smaller. The leg is therefore
    structurally redundant on a COMMIT, which is why "MinTRL bound on none of the 82"
    was never luck. It still differs in the REASON list of a refusal, which is what
    these pin — and feeding it the raw count would leave it looser than the
    pre-registered bar, the one direction §7's disclosure defends against.
    """

    def test_the_leg_binds_when_only_the_effective_count_falls_short(self) -> None:
        trials, n_obs = _mintrl_binding_family()
        mintrl = min_track_record_length(_trial_sharpe(trials[-1].returns))
        _, n_obs_eff = _effective_obs_count(trials[-1])
        assert n_obs_eff < mintrl <= n_obs, "fixture no longer brackets the two counts"

        fixed = evaluate_commit_gate(
            trials[-1], trials, n_grid=6, n_splits=4, correct_obs=True
        )
        assert any("MinTRL" in r for r in fixed.reasons)

    def test_the_default_path_feeds_the_raw_count_unchanged(self) -> None:
        """Byte-identical default: ``effective_obs`` IS ``float(n_obs)`` with
        ``correct_obs`` off, so the same family that trips the leg above does not
        trip it here — confirmed by behaviour, not asserted in prose.
        """
        trials, _ = _mintrl_binding_family()
        raw = evaluate_commit_gate(trials[-1], trials, n_grid=6, n_splits=4)
        assert not any("MinTRL" in r for r in raw.reasons)

    def test_a_raw_refusal_still_names_the_raw_integer(self) -> None:
        """The reason string is the other observable of which count reached the
        leg: on the default path it must keep reading ``n 6`` rather than ``n 6.0``.
        """
        times = [i * _MS_PER_DAY for i in range(8)]
        thin = TrialPerf("x", [1.0, -0.98] * 4, times)
        other = TrialPerf("y", [1.0, -0.97] * 4, times)
        v = evaluate_commit_gate(thin, [thin, other], n_grid=2, n_splits=4)
        assert v.decision == "DO_NOT_COMMIT"
        assert any(r.startswith("n 8 < MinTRL") for r in v.reasons), v.reasons


class TestThresholdsAreNotRestated:
    def test_sweep_guard_derives_from_the_published_gate(self) -> None:
        from analytics.research_guards import GATE_DSR, GATE_PBO
        from analytics.sweep_guard import DSR_THRESHOLD, PBO_THRESHOLD

        assert DSR_THRESHOLD is GATE_DSR
        assert PBO_THRESHOLD is GATE_PBO

    def test_recalibrate_suspect_threshold_derives_from_it_too(self) -> None:
        from analytics.recalibrate_lib import DSR_SUSPECT_THRESHOLD
        from analytics.research_guards import GATE_DSR

        assert DSR_SUSPECT_THRESHOLD is GATE_DSR

    def test_no_literal_thresholds_remain(self) -> None:
        """Mutation guard: a re-added literal fails here, not silently agrees.

        Scoped to the two assignment lines rather than the whole file, so an
        unrelated 0.95 in a docstring or a test fixture does not trip it.
        """
        from pathlib import Path

        src = Path("analytics/sweep_guard.py").read_text(encoding="utf-8")
        assert "DSR_THRESHOLD = 0.95" not in src
        assert "PBO_THRESHOLD = 0.5" not in src
        rec = Path("analytics/recalibrate_lib.py").read_text(encoding="utf-8")
        assert "DSR_SUSPECT_THRESHOLD = 0.95" not in rec
