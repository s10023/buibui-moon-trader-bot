# ST134 Sweep-Gate Trial Independence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the corrected sweep commit gate and the two kill-switch harnesses its
pre-registration requires, without changing any current verdict and without writing any TOML.

**Architecture:** Two counting corrections land in `analytics/sweep_guard.py` behind
keyword-only flags that **default to off**, so every existing caller's verdict stays
byte-identical. Correction (a) reduces the trial family by effective independence; correction (b)
reduces the observation count by day-clustering. Both delegate to functions that already exist —
`analytics.forecast.effective_independent_series` and
`analytics.research_guards.cluster.cluster_stats` — because re-spelling either formula inside this
change would be the exact defect §1e of the spec is about. Two tools then run the pre-registered
kill-switches and the 2×2 report.

**Tech Stack:** Python 3.11+, numpy, pandas, DuckDB, pytest, mypy strict, ruff.

**Spec:** `docs/superpowers/specs/2026-09-10-st134-sweep-gate-trial-independence-preregistration.md`

## Global Constraints

Copied from the spec. Every task's requirements implicitly include this section.

- ⛔ **No TOML is written under any outcome.** Nothing in this plan may touch
  `config/signal_watch*.toml`. Writing them IS the deployment — the signal-watch timer runs the
  working tree on a 15-minute cycle.
- ⛔ **Default behaviour must stay byte-identical.** Both corrections are keyword-only flags
  defaulting to `False`. A test pins that the default path reproduces today's verdict exactly.
- ⛔ **The third leg is not touched.** `sweep_guard`'s third leg stays `n_obs ≥ MinTRL`; do not
  substitute `boot_lo > 0`.
- ⛔ **`V[SR]` is not touched.** `sr_variance` stays `statistics.variance(trial_srs)`.
- **`n_trials_eff` is clamped to `[2.0, float(k)]`** — floored at 2 because deflation is undefined
  below it, ceilinged at the raw count because a correction may only ever *reduce* a family.
- **The `2 × n_splits` floor applies to the effective observation count** when correction (b) is
  on, not the raw one.
- **Run artifacts go under `docs/plans/scratch/`**, never `/tmp` — the ST128 run's JSON went to
  `/tmp` and is gone, which is why its "uniform 9 trials" line could not be verified.
- **Reports carry effect size, never a bare pass count.**
- ⚠ **Never edit the Python tree while a suite is live.** While iterating run the targeted test
  files only (seconds); `make preflight` is the whole-suite answer, at `/post-branch` Step 7.

---

### Task 1: Let the numeric guards accept fractional counts

Both corrections produce **floats** where the research guards declare `int`. The functions already
work with floats at runtime (`expected_max_sharpe` does `n = float(n_trials)` on the next line;
`probabilistic_sharpe_ratio` does `math.sqrt(n_obs - 1)`), so this is an annotation widening with
no behaviour change. It is its own task because it touches a module every sleeve imports.

Widening `int` to `float` cannot break an existing caller — mypy's numeric tower accepts an `int`
argument wherever a `float` is declared.

**Files:**

- Modify: `analytics/research_guards/dsr.py:18` (`expected_max_sharpe`), `:36-45` (`deflated_sharpe_ratio`)
- Modify: `analytics/research_guards/psr.py:12-18` (`probabilistic_sharpe_ratio`)
- Test: `tests/test_research_guards_fractional_counts.py` (create)

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `expected_max_sharpe(n_trials: float, sr_variance: float) -> float`;
  `probabilistic_sharpe_ratio(sr: float, n_obs: float, skew: float = 0.0, kurtosis: float = 3.0, sr_benchmark: float = 0.0) -> float`;
  `deflated_sharpe_ratio(sr: float, n_obs: float, *, trial_srs: Sequence[float] | None = None, n_trials: float | None = None, sr_variance: float | None = None, skew: float = 0.0, kurtosis: float = 3.0) -> float`

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_guards_fractional_counts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_research_guards_fractional_counts.py -q`
Expected: PASS at runtime (Python does not enforce annotations) but `make typecheck` FAILS with
`Argument 1 to "expected_max_sharpe" has incompatible type "float"; expected "int"`. Run both:

```bash
poetry run pytest tests/test_research_guards_fractional_counts.py -q
make typecheck > /tmp/tc.log 2>&1; echo "exit=$?"; grep -c "incompatible type" /tmp/tc.log
```

Expected: pytest passes, typecheck exits non-zero with at least one `incompatible type` line.

- [ ] **Step 3: Widen the three annotations**

In `analytics/research_guards/dsr.py`, change the `expected_max_sharpe` signature line:

```python
def expected_max_sharpe(n_trials: float, sr_variance: float) -> float:
```

and inside `deflated_sharpe_ratio`, change these two parameter lines:

```python
    n_obs: float,
```

```python
n_trials: float | None = (None,)
```

In `analytics/research_guards/psr.py`, change the `probabilistic_sharpe_ratio` parameter line:

```python
    n_obs: float,
```

Leave every body unchanged. `expected_max_sharpe` already does `n = float(n_trials)`;
`probabilistic_sharpe_ratio` already does `math.sqrt(n_obs - 1)`.

- [ ] **Step 4: Run the tests and the type checker**

```bash
poetry run pytest tests/test_research_guards_fractional_counts.py tests/test_research_guards_gate.py -q
make typecheck > /tmp/tc.log 2>&1; echo "exit=$?"; tail -3 /tmp/tc.log
make lint-py
```

Expected: pytest PASS, typecheck exit 0, lint clean.

- [ ] **Step 5: Commit**

```bash
git add analytics/research_guards/dsr.py analytics/research_guards/psr.py \
        tests/test_research_guards_fractional_counts.py
git commit -m "refactor: let the DSR/PSR guards accept fractional trial and observation counts"
```

---

### Task 2: Effective trial count — correction (a), computed but not wired

⚠ **This must DELEGATE to `effective_independent_series`, never re-spell `k / (1 + (k−1)·ρ)`.**
`AGENTS.md` records restated constants as this repo's recurring silent defect, and the spec's own
§1e is about three such links. ρ is then recovered **algebraically** from the returned `n_eff`
rather than computed a second way, so the reported ρ and the applied `n_eff` cannot drift apart.

**Files:**

- Modify: `analytics/sweep_guard.py` (add after `_build_perf_matrix`, currently ending line 108)
- Test: `tests/test_sweep_guard_effective_counts.py` (create)

**Interfaces:**

- Consumes: `_build_perf_matrix(trials, n_bins) -> npt.NDArray[np.float64]` of shape `(n_bins, k)`, already in `sweep_guard`.
- Produces: `_effective_trial_count(perf: npt.NDArray[np.float64]) -> tuple[float, float]` returning `(rho, n_trials_eff)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sweep_guard_effective_counts.py`:

```python
"""ST134 correction (a): the tp_r arms are not independent trials.

The arms share entries and stops and differ only in target, so counting them as
independent searches overstates the DSR benchmark. These pin the bounds the
pre-registration commits to, and pin that the formula is DELEGATED rather than
re-spelled — a drifting second copy is the defect the spec's own section 1e is about.
"""

import numpy as np
import pytest

from analytics.forecast import effective_independent_series
from analytics.sweep_guard import _effective_trial_count

import pandas as pd


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
        perf = np.column_stack([base + 0.5 * rng.normal(size=500) for _ in range(k)])
        rho, n_eff = _effective_trial_count(perf)
        # n_eff was clamped only if it left [2, k]; here it does not.
        assert 2.0 < n_eff < float(k)
        assert rho == pytest.approx((k / n_eff - 1.0) / (k - 1), rel=1e-9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sweep_guard_effective_counts.py -q`
Expected: FAIL with `ImportError: cannot import name '_effective_trial_count' from 'analytics.sweep_guard'`

- [ ] **Step 3: Implement**

Add this import to the `analytics.forecast` import block at the top of
`analytics/sweep_guard.py` (create the import line if the module is not yet imported there):

```python
import pandas as pd

from analytics.forecast import effective_independent_series
```

Then add the function immediately after `_build_perf_matrix`:

```python
def _effective_trial_count(
    perf: npt.NDArray[np.float64],
) -> tuple[float, float]:
    """Mean pairwise arm correlation and the effective trial count it implies.

    ST134 correction (a). The ``tp_r`` arms share their entries and their stops and
    differ only in where the target sits — they are re-labellings of one trade
    population, not independent searches — so deflating DSR by the raw grid size
    overstates the expected-maximum benchmark.

    ⚠ **Delegates to :func:`analytics.forecast.effective_independent_series`.** That
    is the repo's one spelling of ``n_eff = k / (1 + (k-1) * rho)`` and it is applied
    to the SYMBOL axis elsewhere; this moves it onto the TRIAL axis. A second copy
    here would be the restated-constant defect the ST134 pre-registration section 1e
    documents at three other sites.

    ``rho`` is recovered ALGEBRAICALLY from the returned ``n_eff`` rather than
    computed a second way, so the reported correlation and the applied count cannot
    drift apart.

    Two bounds, both pre-registered:

    * **floor 2.0** — below two trials deflation is undefined. The raw
      ``n_trials < 2`` INSUFFICIENT guard in :func:`evaluate_commit_gate` still runs
      first, so this cannot route around it.
    * **ceiling k** — a correction may only ever REDUCE a family. Negative measured
      correlation would otherwise inflate it.

    Returns ``(rho, n_trials_eff)``. A single arm returns ``(0.0, 1.0)``: nothing to
    deflate, and the caller's own guard rejects it.
    """
    k = int(perf.shape[1])
    if k < 2:
        return 0.0, float(max(k, 1))
    n_eff_raw, _ = effective_independent_series(
        {f"arm{j}": pd.Series(perf[:, j]) for j in range(k)}
    )
    n_eff = min(max(float(n_eff_raw), 2.0), float(k))
    rho = (k / n_eff - 1.0) / (k - 1)
    return rho, n_eff
```

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_sweep_guard_effective_counts.py tests/test_sweep_guard.py -q
make lint-py && make typecheck
```

Expected: all PASS, lint and typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add analytics/sweep_guard.py tests/test_sweep_guard_effective_counts.py
git commit -m "feat: compute the effective tp_r trial count from arm correlation, delegating the formula"
```

---

### Task 3: Effective observation count — correction (b), computed but not wired

The restrictive twin. `sweep_guard` counts raw trades and never day-clusters them — the blind spot
`audit_guard` carried until ST80, on a path that never got the fix.

**Files:**

- Modify: `analytics/sweep_guard.py` (add after `_effective_trial_count`)
- Test: `tests/test_sweep_guard_effective_counts.py` (extend)

**Interfaces:**

- Consumes: `TrialPerf(label: str, returns: list[float], times: list[int])`, already in `sweep_guard`.
- Produces: `_effective_obs_count(chosen: TrialPerf) -> tuple[float, float]` returning `(design_effect, n_obs_eff)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sweep_guard_effective_counts.py`:

```python
from analytics.sweep_guard import TrialPerf, _effective_obs_count

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sweep_guard_effective_counts.py::TestEffectiveObsCount -q`
Expected: FAIL with `ImportError: cannot import name '_effective_obs_count'`

- [ ] **Step 3: Implement**

Add to the imports at the top of `analytics/sweep_guard.py`:

```python
from analytics.research_guards.cluster import cluster_stats, utc_day_keys
```

Then add the function immediately after `_effective_trial_count`:

```python
def _effective_obs_count(chosen: TrialPerf) -> tuple[float, float]:
    """Design effect and effective observation count for the chosen arm's trades.

    ST134 correction (b), and the restrictive twin of :func:`_effective_trial_count`.
    ``n_obs`` enters DSR as ``sqrt(n - 1)``, and this gate has never clustered it —
    the blind spot :mod:`analytics.audit_guard` carried until ST80, on a path that
    never got the fix. Measured there: trade-weighted design effect 4.991, median
    1.670, concentrating in the 15m cells that are 64.4% of the live ledger.

    ⚠ ``utc_day_keys`` is a documented LOWER BOUND on the dependence unit for a 24/7
    tape — the design effect keeps rising past the day with no plateau — so read the
    deflation as a floor and a surviving verdict as conservative.

    ⚠ This is NOT the same estimator as :func:`_effective_trial_count` applied twice.
    ``AGENTS.md`` forbids running the series route and the design-effect route on ONE
    axis; these are two. (a) counts SEARCHES along the trial axis, (b) counts
    OBSERVATIONS within the chosen arm. Applying only one leaves the other
    denominator wrong.

    Returns ``(design_effect, n_obs_eff)``; an empty arm returns ``(1.0, 0.0)`` and
    the caller's trade-count floor rejects it.
    """
    if not chosen.returns:
        return 1.0, 0.0
    values = np.asarray(chosen.returns, dtype=np.float64)
    stats = cluster_stats(values, utc_day_keys(chosen.times))
    return stats.design_effect, stats.n_eff
```

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_sweep_guard_effective_counts.py tests/test_sweep_guard.py -q
make lint-py && make typecheck
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/sweep_guard.py tests/test_sweep_guard_effective_counts.py
git commit -m "feat: day-cluster the sweep gate's observation count, the blind spot audit_guard lost at ST80"
```

---

### Task 4: Wire both corrections into the gate, default OFF

The whole 2×2 the spec's §5 requires must be reachable from one function, and the default must
reproduce today's verdict exactly.

**Files:**

- Modify: `analytics/sweep_guard.py:56-67` (`CommitGateVerdict`), `:134-195` (`evaluate_commit_gate`)
- Test: `tests/test_sweep_guard_effective_counts.py` (extend)

**Interfaces:**

- Consumes: `_effective_trial_count`, `_effective_obs_count` from Tasks 2-3.
- Produces: `evaluate_commit_gate(chosen, all_trials, *, n_grid, dsr_threshold=DSR_THRESHOLD, pbo_threshold=PBO_THRESHOLD, mintrl_confidence=MINTRL_CONFIDENCE, n_splits=DEFAULT_N_SPLITS, correct_trials: bool = False, correct_obs: bool = False) -> CommitGateVerdict`, with `CommitGateVerdict` gaining four fields: `rho: float | None`, `n_trials_eff: float | None`, `design_effect: float | None`, `n_obs_eff: float | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sweep_guard_effective_counts.py`:

```python
from analytics.sweep_guard import evaluate_commit_gate


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
        assert raw.dsr is not None and fixed.dsr is not None
        assert fixed.dsr >= raw.dsr

    def test_obs_correction_lowers_dsr(self) -> None:
        # Two trades per UTC day, so the day key clusters them.
        rng = np.random.default_rng(99)
        n = 60
        base = rng.normal(loc=0.3, scale=1.0, size=n)
        times = [(i // 2) * _MS_PER_DAY for i in range(n)]
        trials = [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(6)]
        raw = evaluate_commit_gate(trials[-1], trials, n_grid=6, n_splits=4)
        fixed = evaluate_commit_gate(
            trials[-1], trials, n_grid=6, n_splits=4, correct_obs=True
        )
        assert fixed.n_obs_eff is not None and fixed.n_obs_eff < float(n)
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

    def test_raw_trial_guard_still_runs_first(self) -> None:
        """The floor of 2.0 must not route around the n_trials < 2 refusal."""
        t = _correlated_trials(k=1)[0]
        v = evaluate_commit_gate(t, [t], n_grid=1, n_splits=4, correct_trials=True)
        assert v.decision == "INSUFFICIENT"
        assert v.n_trials_eff is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sweep_guard_effective_counts.py::TestCorrectionsAreOptIn -q`
Expected: FAIL with `TypeError: evaluate_commit_gate() got an unexpected keyword argument 'correct_trials'`

- [ ] **Step 3: Implement**

Replace the `CommitGateVerdict` dataclass body in `analytics/sweep_guard.py` with:

```python
@dataclass(frozen=True)
class CommitGateVerdict:
    decision: str  # COMMIT | DO_NOT_COMMIT | INSUFFICIENT
    dsr: float | None
    pbo: float | None
    min_trl: float | None
    n_obs: int
    n_trials: int
    reasons: list[str]
    # ST134. All four are None on the uncorrected path, which is the default, so a
    # reader can tell "not corrected" from "corrected and came back equal".
    rho: float | None = None
    n_trials_eff: float | None = None
    design_effect: float | None = None
    n_obs_eff: float | None = None

    @property
    def committable(self) -> bool:
        return self.decision == DECISION_COMMIT
```

Replace `evaluate_commit_gate` with:

```python
def evaluate_commit_gate(
    chosen: TrialPerf,
    all_trials: Sequence[TrialPerf],
    *,
    n_grid: int,
    dsr_threshold: float = DSR_THRESHOLD,
    pbo_threshold: float = PBO_THRESHOLD,
    mintrl_confidence: float = MINTRL_CONFIDENCE,
    n_splits: int = DEFAULT_N_SPLITS,
    correct_trials: bool = False,
    correct_obs: bool = False,
) -> CommitGateVerdict:
    """Verdict for committing ``chosen``'s params, given the full grid.

    ``n_grid`` is the true number of trials searched (>= ``len(all_trials)`` when
    the caller truncated to top-N); it is the N-floor fed to the deflation so a
    truncated grid cannot make DSR look better than it is.

    ``correct_trials`` and ``correct_obs`` are ST134's two counting corrections and
    both default to **off**, so an existing caller's verdict is unchanged. They pull
    in opposite directions and are meant to be measured as a 2x2 — see the
    pre-registration at
    ``docs/superpowers/specs/2026-09-10-st134-sweep-gate-trial-independence-preregistration.md``.
    """
    n_trials = len(all_trials)
    n_obs = len(chosen.returns)
    min_obs = 2 * n_splits

    if n_trials < 2:
        return CommitGateVerdict(
            DECISION_INSUFFICIENT,
            None,
            None,
            None,
            n_obs,
            n_trials,
            [f"only {n_trials} trial(s); need >= 2 to deflate"],
        )
    if n_obs < min_obs:
        return CommitGateVerdict(
            DECISION_INSUFFICIENT,
            None,
            None,
            None,
            n_obs,
            n_trials,
            [f"{n_obs} trades < {min_obs} (2x n_splits) — stats unstable"],
        )

    perf = _build_perf_matrix(all_trials, min_obs)

    rho: float | None = None
    n_trials_eff: float | None = None
    effective_trials: float = float(max(n_grid, n_trials))
    if correct_trials:
        rho, n_trials_eff = _effective_trial_count(perf)
        effective_trials = n_trials_eff

    design_effect: float | None = None
    n_obs_eff: float | None = None
    effective_obs: float = float(n_obs)
    if correct_obs:
        design_effect, n_obs_eff = _effective_obs_count(chosen)
        effective_obs = n_obs_eff
        if effective_obs < float(min_obs):
            return CommitGateVerdict(
                DECISION_INSUFFICIENT,
                None,
                None,
                None,
                n_obs,
                n_trials,
                [
                    f"{effective_obs:.1f} effective trades < {min_obs} "
                    f"(2x n_splits) after day-clustering {n_obs} raw — stats unstable"
                ],
                rho,
                n_trials_eff,
                design_effect,
                n_obs_eff,
            )

    sr = _trial_sharpe(chosen.returns)
    trial_srs = [_trial_sharpe(t.returns) for t in all_trials]
    dsr = deflated_sharpe_ratio(
        sr,
        effective_obs,
        n_trials=effective_trials,
        sr_variance=statistics.variance(trial_srs),
    )
    min_trl = min_track_record_length(sr, confidence=mintrl_confidence)
    pbo = cscv_pbo(perf, n_splits=n_splits).pbo

    decision, reasons = _decide(
        dsr=dsr,
        pbo=pbo,
        min_trl=min_trl,
        n_obs=n_obs,
        dsr_threshold=dsr_threshold,
        pbo_threshold=pbo_threshold,
    )
    return CommitGateVerdict(
        decision,
        dsr,
        pbo,
        min_trl,
        n_obs,
        n_trials,
        reasons,
        rho,
        n_trials_eff,
        design_effect,
        n_obs_eff,
    )
```

⚠ Note two deliberate choices. `_build_perf_matrix` moves **above** the correction block so both
PBO and the trial correlation read one matrix. And `_decide` still receives the **raw** `n_obs` for
the MinTRL leg — that leg is out of scope per the Global Constraints, and feeding it an effective
count would be a third change.

Finally, forward the two flags through the `param_sweep` wrapper so a caller holding a finished
`ParamSweepReport` can re-score its rows under any book **without re-running a single backtest**.
In `analytics/param_sweep.py`, replace `_compute_sweep_gate` (currently `:284-302`) with:

```python
def _compute_sweep_gate(
    trial_rows: list[SweepRow],
    chosen_row: SweepRow | None,
    n_grid: int,
    *,
    correct_trials: bool = False,
    correct_obs: bool = False,
) -> CommitGateVerdict:
    """Gate verdict: deflate ``chosen_row`` against the full ``trial_rows`` grid.

    ``chosen_row`` is the recommended (committable) config the apply-skill would
    write; ``trial_rows`` is the whole grid (for cross-trial variance + PBO).

    The two ST134 correction flags default off and are forwarded unchanged. They are
    keyword-only so a caller re-scoring a finished report under a second book cannot
    pass them positionally into ``n_grid``.
    """
    if chosen_row is None:
        return CommitGateVerdict(
            DECISION_INSUFFICIENT,
            None,
            None,
            None,
            0,
            len(trial_rows),
            ["no non-overfit config to evaluate"],
        )
    chosen = _row_to_trialperf(chosen_row)
    all_trials = [_row_to_trialperf(r) for r in trial_rows]
    return evaluate_commit_gate(
        chosen,
        all_trials,
        n_grid=n_grid,
        correct_trials=correct_trials,
        correct_obs=correct_obs,
    )
```

The existing call at `:520` passes `n_grid=n` as a keyword and is unchanged.

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_sweep_guard_effective_counts.py tests/test_sweep_guard.py \
  tests/test_param_sweep.py tests/test_wfo_resweep.py -q
make lint-py && make typecheck
```

Expected: all PASS. The pre-existing `test_sweep_guard.py` passing unchanged is the proof that the
default path is untouched.

- [ ] **Step 5: Commit**

```bash
git add analytics/sweep_guard.py tests/test_sweep_guard_effective_counts.py
git commit -m "feat: wire both ST134 counting corrections into the commit gate, default off"
```

---

### Task 5: Collapse the three restated thresholds onto the published constants

Three sites hold `0.95`: `sweep_guard`, `recalibrate_lib`, and the published gate. None calls the
others; they agree by coincidence, which `AGENTS.md` names as indistinguishable from agreement by
construction.

⚠ `passes_gate(dsr, pbo, boot_lo)` takes three legs and `sweep_guard`'s third is MinTRL, so the
gate **function** cannot be called here. The two shared **constants** can, and that is the fix.

**Files:**

- Modify: `analytics/sweep_guard.py:34-35`
- Modify: `analytics/recalibrate_lib.py:17-19`
- Test: `tests/test_sweep_guard_effective_counts.py` (extend)

**Interfaces:**

- Consumes: `GATE_DSR`, `GATE_PBO` from `analytics.research_guards`.
- Produces: no signature change. `DSR_THRESHOLD` and `PBO_THRESHOLD` stay importable names.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sweep_guard_effective_counts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_sweep_guard_effective_counts.py::TestThresholdsAreNotRestated -q`
Expected: FAIL — `assert 0.95 is 0.95` may pass by interning, but
`test_no_literal_thresholds_remain` FAILS on `assert "DSR_THRESHOLD = 0.95" not in src`.

- [ ] **Step 3: Implement**

In `analytics/sweep_guard.py`, extend the existing `analytics.research_guards` import to include
the two constants:

```python
from analytics.research_guards import (
    GATE_DSR,
    GATE_PBO,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
```

and replace the two constant lines:

```python
DSR_THRESHOLD = GATE_DSR
"""Re-exported from :mod:`analytics.research_guards` — NOT a second copy.

``AGENTS.md``: the gate is one function and its thresholds are one pair of
constants. This module cannot call :func:`passes_gate` (its third leg is MinTRL,
not ``boot_lo``), but it must not restate the two it shares. Until ST134 it held
its own ``0.95``, and :mod:`analytics.recalibrate_lib` held a third under a comment
claiming it matched this one — three links agreeing by coincidence.
"""

PBO_THRESHOLD = GATE_PBO
"""Re-exported from :mod:`analytics.research_guards`. See :data:`DSR_THRESHOLD`."""
```

In `analytics/recalibrate_lib.py`, extend the import and replace the constant:

```python
from analytics.research_guards import GATE_DSR, deflated_sharpe_ratio
```

```python
# A 5-star cell whose Deflated Sharpe falls below this is overfit-suspect (spec
# section 3). Derived from the published gate rather than restated: this line held its
# own 0.95 under a comment saying it "matches the sweep commit-gate threshold", which
# is agreement by coincidence — ST134.
DSR_SUSPECT_THRESHOLD = GATE_DSR
```

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_sweep_guard_effective_counts.py tests/test_sweep_guard.py \
  tests/test_recalibrate.py tests/test_research_guards_gate.py -q
make lint-py && make typecheck
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/sweep_guard.py analytics/recalibrate_lib.py \
        tests/test_sweep_guard_effective_counts.py
git commit -m "refactor: derive the sweep and recalibrate DSR thresholds from the published gate"
```

---

### Task 6: The §4a kill-switch — measure arm correlation across all 273 cells

⛔ **This measures and reports. It decides nothing and writes no TOML.** Its output is what the
operator rules on.

**Files:**

- Modify: `tools/wfo_resweep.py` (add `--measure-rho`; result dataclass at `:97-111`; argparse at `:265-280`)
- Test: `tests/test_wfo_resweep.py` (extend)

**Interfaces:**

- Consumes: `CommitGateVerdict.rho`, `.n_trials_eff` from Task 4.
- Produces: `median_rho_ci(rhos: list[float], *, n_boot: int = 10_000, seed: int = 20260910) -> tuple[float, float, float]` returning `(median, ci_lo, ci_hi)`; and a `rho_verdict(ci_lo: float, ci_hi: float, *, bar: float = 0.5) -> str` returning one of `"PROCEED"`, `"NOT_LICENSED"`, `"INSUFFICIENT"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wfo_resweep.py`:

```python
from tools.wfo_resweep import median_rho_ci, rho_verdict


class TestMedianRhoCi:
    def test_ci_brackets_the_median(self) -> None:
        rhos = [0.60, 0.62, 0.65, 0.68, 0.70, 0.72, 0.75]
        med, lo, hi = median_rho_ci(rhos)
        assert lo <= med <= hi
        assert med == pytest.approx(0.68)

    def test_tight_sample_gives_a_tight_ci(self) -> None:
        _, lo, hi = median_rho_ci([0.70] * 50)
        assert hi - lo == pytest.approx(0.0, abs=1e-9)

    def test_single_value_is_degenerate_not_a_crash(self) -> None:
        med, lo, hi = median_rho_ci([0.42])
        assert med == lo == hi == pytest.approx(0.42)

    def test_empty_is_nan(self) -> None:
        med, lo, hi = median_rho_ci([])
        assert math.isnan(med) and math.isnan(lo) and math.isnan(hi)


class TestRhoVerdict:
    def test_lower_bound_above_the_bar_proceeds(self) -> None:
        assert rho_verdict(0.55, 0.80) == "PROCEED"

    def test_upper_bound_below_the_bar_is_not_licensed(self) -> None:
        assert rho_verdict(0.10, 0.40) == "NOT_LICENSED"

    def test_straddling_the_bar_is_insufficient(self) -> None:
        assert rho_verdict(0.40, 0.60) == "INSUFFICIENT"

    def test_touching_the_bar_is_insufficient_not_a_pass(self) -> None:
        """A boundary reading is untested, never cleared — the six-site defect."""
        assert rho_verdict(0.50, 0.90) == "INSUFFICIENT"
        assert rho_verdict(0.10, 0.50) == "INSUFFICIENT"

    def test_nan_is_insufficient(self) -> None:
        assert rho_verdict(float("nan"), float("nan")) == "INSUFFICIENT"
```

Add `import math` and `import pytest` to that file's imports if absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_wfo_resweep.py -q -k "Rho"`
Expected: FAIL with `ImportError: cannot import name 'median_rho_ci' from 'tools.wfo_resweep'`

- [ ] **Step 3: Implement**

Add to `tools/wfo_resweep.py`:

```python
def median_rho_ci(
    rhos: list[float],
    *,
    n_boot: int = 10_000,
    seed: int = 20260910,
) -> tuple[float, float, float]:
    """Median arm correlation and its percentile bootstrap CI.

    ST134 section 4a. The decision statistic is the DISTRIBUTION across cells, never
    any one cell's point estimate — rho is measured over ``2 * n_splits`` bins, so a
    per-cell value is noisy by construction.
    """
    clean = [r for r in rhos if not math.isnan(r)]
    if not clean:
        nan = float("nan")
        return nan, nan, nan
    arr = np.asarray(clean, dtype=np.float64)
    med = float(np.median(arr))
    rng = np.random.default_rng(seed)
    draws = rng.choice(arr, size=(n_boot, arr.size), replace=True)
    meds = np.median(draws, axis=1)
    return med, float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def rho_verdict(ci_lo: float, ci_hi: float, *, bar: float = 0.5) -> str:
    """CI containment against the pre-registered bar — THREE readings, not two.

    ⛔ A threshold comparison is not a verdict. A sample-size floor, an MDE, a
    p-value or a failure to clear a bar are none of them power; ``AGENTS.md`` records
    that family at six sites, each spelling the arithmetic differently.

    * ``PROCEED`` — the CI lower bound clears the bar; the correction is licensed.
    * ``NOT_LICENSED`` — the upper bound sits below it; established the other way.
    * ``INSUFFICIENT`` — the CI straddles or touches the bar. **Untested, not
      cleared**, and it licenses no conclusion about whether the gate's refusals are
      correct. Those are different claims.
    """
    if math.isnan(ci_lo) or math.isnan(ci_hi):
        return "INSUFFICIENT"
    if ci_lo > bar:
        return "PROCEED"
    if ci_hi < bar:
        return "NOT_LICENSED"
    return "INSUFFICIENT"
```

Add the `--measure-rho` flag to the argparse block:

```python
    parser.add_argument(
        "--measure-rho",
        action="store_true",
        help=(
            "ST134 section 4a kill-switch: run every cell with the trial correction on, "
            "report the arm-correlation distribution and its CI verdict, and STOP. "
            "Writes no TOML and decides nothing."
        ),
    )
```

In `main`, after the per-cell loop populates `results`, add:

```python
    if args.measure_rho:
        rhos = [r["rho"] for r in results if r.get("rho") is not None]
        med, lo, hi = median_rho_ci(rhos)
        verdict = rho_verdict(lo, hi)
        print(f"\nST134 §4a — arm correlation across {len(rhos)} scoreable cells")
        print(f"  median rho {med:.4f}   95% CI [{lo:.4f}, {hi:.4f}]   bar 0.50")
        print(f"  VERDICT: {verdict}")
        if verdict != "PROCEED":
            print(
                "  ⛔ STOP. This does NOT license 'the gate was right' — failing to\n"
                "     license a correction is a different claim from the refusals\n"
                "     being correct. Route to ST133 on the existing evidence."
            )
```

Extend the `CellVerdict` dataclass at `:97-111` — add these two fields after the existing
`n_trials: int` line:

```python
    rho: float | None
    n_trials_eff: float | None
```

and populate them in the constructor call at `:186`, immediately after the existing
`min_trl=report.gate.min_trl,` line:

```python
rho = (report.gate.rho,)
n_trials_eff = (report.gate.n_trials_eff,)
```

Then re-score the finished report under the trials-corrected book. This re-uses
`report.rows`, so it runs **no** additional backtests. In the per-cell loop, immediately after the
existing `report = run_param_sweep(...)` call:

```python
                    corrected = _compute_sweep_gate(
                        report.rows,
                        _recommended_row(report.rows),
                        report.n_grid,
                        correct_trials=True,
                    )
```

and pass `corrected.rho` / `corrected.n_trials_eff` into the `CellVerdict` instead of
`report.gate.*`. Import `_compute_sweep_gate` and `_recommended_row` from `analytics.param_sweep`
alongside the existing `run_param_sweep` import at `:71-73`.

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_wfo_resweep.py -q
make lint-py && make typecheck
python3 tools/wfo_resweep.py --help | head -20
```

Expected: tests PASS, `--measure-rho` appears in the help output (this also exercises the bare
invocation the repo requires).

- [ ] **Step 5: Commit**

```bash
git add tools/wfo_resweep.py tests/test_wfo_resweep.py
git commit -m "feat: add the ST134 arm-correlation kill-switch with CI containment, three readings"
```

---

### Task 7: The §4b kill-switch — null calibration on the DSR leg

A gate that admits a known-null family is too permissive whatever it does to real cells. This is
what makes the spec's §7 disclosure survivable, so it is not optional.

**Files:**

- Create: `tools/st134_null_calibration.py`
- Test: `tests/test_st134_null_calibration.py` (create)

**Interfaces:**

- Consumes: `TrialPerf`, `_effective_trial_count` from `analytics.sweep_guard`.
- Produces: `sign_flip_family(trials: Sequence[TrialPerf], rng: np.random.Generator) -> list[TrialPerf]`; `null_pass_rate(trials: Sequence[TrialPerf], *, correct_trials: bool, correct_obs: bool = False, n_replicates: int = 200, n_splits: int = 14, seed: int = 20260910) -> float`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_st134_null_calibration.py`:

```python
"""ST134 section 4b — the corrected gate must still refuse a known-null family."""

import numpy as np
import pytest

from analytics.sweep_guard import TrialPerf, _effective_trial_count, _build_perf_matrix
from tools.st134_null_calibration import null_pass_rate, sign_flip_family

_MS_PER_DAY = 86_400_000


def _family(k: int = 6, n: int = 80, drift: float = 0.30) -> list[TrialPerf]:
    rng = np.random.default_rng(4242)
    base = rng.normal(loc=drift, scale=1.0, size=n)
    times = [i * _MS_PER_DAY for i in range(n)]
    return [TrialPerf(f"tp{j}", list(base + 0.02 * j), times) for j in range(k)]


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


class TestNullPassRate:
    def test_uncorrected_gate_refuses_most_nulls(self) -> None:
        rate = null_pass_rate(
            _family(), correct_trials=False, n_replicates=40, n_splits=4
        )
        assert 0.0 <= rate <= 0.10

    def test_corrected_gate_also_refuses_most_nulls(self) -> None:
        rate = null_pass_rate(
            _family(), correct_trials=True, n_replicates=40, n_splits=4
        )
        assert 0.0 <= rate <= 0.10

    def test_is_deterministic_under_a_seed(self) -> None:
        a = null_pass_rate(
            _family(), correct_trials=True, n_replicates=20, n_splits=4, seed=7
        )
        b = null_pass_rate(
            _family(), correct_trials=True, n_replicates=20, n_splits=4, seed=7
        )
        assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_st134_null_calibration.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.st134_null_calibration'`

- [ ] **Step 3: Implement**

Create `tools/st134_null_calibration.py`:

```python
"""ST134 section 4b — null calibration for the corrected sweep commit gate.

A gate that admits a known-null family is too permissive whatever it does to real
cells, so this is the check that makes the pre-registration's section 7 disclosure
survivable: it settles the permissiveness question WITHOUT reference to any real cell.

Construction: draw one shared random sign vector over the trades and apply it to every
arm at once. That preserves the cross-arm correlation structure EXACTLY — all arms flip
together — while setting the expected return to zero. The family is then a null
carrying this family's own dependence, which is what the correction claims to price.

Scope: the DSR leg only, which is the leg being corrected and the one that binds on all
82 scoreable cells in the ST128 run. PBO's CSCV is C(14,7) = 3,432 splits per
evaluation; running it inside 200 replicates would cost more than the whole re-run for
a leg this file does not change.

⛔ Decides nothing and writes no TOML.
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.research_guards import GATE_DSR, deflated_sharpe_ratio  # noqa: E402
from analytics.sweep_guard import (  # noqa: E402
    TrialPerf,
    _build_perf_matrix,
    _effective_obs_count,
    _effective_trial_count,
    _trial_sharpe,
)


def sign_flip_family(
    trials: Sequence[TrialPerf], rng: np.random.Generator
) -> list[TrialPerf]:
    """One shared sign vector applied to every arm — kills the mean, keeps the rho."""
    if not trials:
        return []
    n = len(trials[0].returns)
    signs = rng.choice(np.array([-1.0, 1.0]), size=n)
    out: list[TrialPerf] = []
    for t in trials:
        m = min(n, len(t.returns))
        flipped = [float(t.returns[i] * signs[i]) for i in range(m)]
        out.append(TrialPerf(t.label, flipped, list(t.times[:m])))
    return out


def null_pass_rate(
    trials: Sequence[TrialPerf],
    *,
    correct_trials: bool,
    correct_obs: bool = False,
    n_replicates: int = 200,
    n_splits: int = 14,
    seed: int = 20260910,
) -> float:
    """Fraction of sign-flipped null replicates whose DSR still clears the bar."""
    rng = np.random.default_rng(seed)
    min_obs = 2 * n_splits
    passes = 0
    for _ in range(n_replicates):
        fam = sign_flip_family(trials, rng)
        chosen = max(fam, key=lambda t: _trial_sharpe(t.returns))
        n_obs: float = float(len(chosen.returns))
        n_trials: float = float(len(fam))
        if correct_trials:
            _, n_trials = _effective_trial_count(_build_perf_matrix(fam, min_obs))
        if correct_obs:
            _, n_obs = _effective_obs_count(chosen)
            if n_obs < float(min_obs):
                continue
        srs = [_trial_sharpe(t.returns) for t in fam]
        dsr = deflated_sharpe_ratio(
            _trial_sharpe(chosen.returns),
            n_obs,
            n_trials=n_trials,
            sr_variance=statistics.variance(srs),
        )
        if dsr >= GATE_DSR:
            passes += 1
    return passes / n_replicates if n_replicates else 0.0
```

⚠ The chosen arm is re-selected as the best of the flipped family each replicate. Selecting the
best is the whole thing being deflated for; holding the original winner fixed would test a
different question.

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_st134_null_calibration.py -q
make lint-py && make typecheck
python3 tools/st134_null_calibration.py 2>&1 | head -3
```

Expected: tests PASS; the bare invocation resolves repo imports without a traceback.

- [ ] **Step 5: Commit**

```bash
git add tools/st134_null_calibration.py tests/test_st134_null_calibration.py
git commit -m "feat: add the ST134 null calibration for the corrected gate's DSR leg"
```

---

### Task 8: The 2×2 report and its effect-size fields

⛔ **"How many cells clear" must not be the headline.** The decision this feeds is whether to move
live `tp_r` values, and a pass count cannot support it.

**Files:**

- Modify: `tools/wfo_resweep.py` (the per-cell loop; the JSON writer at `:386`)
- Test: `tests/test_wfo_resweep.py` (extend)

**Interfaces:**

- Consumes: `evaluate_commit_gate(..., correct_trials=, correct_obs=)` from Task 4.
- Produces: `BOOKS: tuple[tuple[str, bool, bool], ...]` and `effect_size(current_oos_avg_r: float | None, winner_oos_avg_r: float | None) -> float | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wfo_resweep.py`:

```python
from tools.wfo_resweep import BOOKS, effect_size


class TestBooks:
    def test_all_four_books_are_declared(self) -> None:
        assert BOOKS == (
            ("raw", False, False),
            ("trials_corrected", True, False),
            ("obs_corrected", False, True),
            ("both", True, True),
        )

    def test_raw_book_is_first_so_it_is_the_baseline(self) -> None:
        assert BOOKS[0][0] == "raw"


class TestEffectSize:
    def test_delta_is_winner_minus_current(self) -> None:
        assert effect_size(0.10, 0.32) == pytest.approx(0.22)

    def test_negative_delta_is_reported_not_clamped(self) -> None:
        assert effect_size(0.40, 0.15) == pytest.approx(-0.25)

    def test_missing_current_is_none(self) -> None:
        assert effect_size(None, 0.32) is None

    def test_missing_winner_is_none(self) -> None:
        assert effect_size(0.10, None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_wfo_resweep.py -q -k "Books or EffectSize"`
Expected: FAIL with `ImportError: cannot import name 'BOOKS' from 'tools.wfo_resweep'`

- [ ] **Step 3: Implement**

Add to `tools/wfo_resweep.py`:

```python
BOOKS: tuple[tuple[str, bool, bool], ...] = (
    ("raw", False, False),
    ("trials_corrected", True, False),
    ("obs_corrected", False, True),
    ("both", True, True),
)
"""ST134 section 5: every cell is scored under all four books.

Reporting only ``both`` makes the two corrections unattributable, which is the
failure ST128 section 5 exists to prevent. ``raw`` is first because it is the
baseline every other column is read against.
"""


def effect_size(
    current_oos_avg_r: float | None,
    winner_oos_avg_r: float | None,
) -> float | None:
    """OOS ``avg_r`` the winner adds over the live value, or None if either is absent.

    ⚠ **An optimistic bound, and it must be labelled one where it is reported.** The
    winner is SELECTED on this number, and that selection is exactly what DSR
    deflates for. The live realisation is expected to be smaller.
    """
    if current_oos_avg_r is None or winner_oos_avg_r is None:
        return None
    return winner_oos_avg_r - current_oos_avg_r
```

In the per-cell loop, replace the single trials-corrected re-score added in Task 6 with a loop
over `BOOKS`. All four read `report.rows`, so this runs **no** additional backtests:

```python
                    chosen_row = _recommended_row(report.rows)
                    book_verdicts = {
                        name: _compute_sweep_gate(
                            report.rows,
                            chosen_row,
                            report.n_grid,
                            correct_trials=ct,
                            correct_obs=co,
                        )
                        for name, ct, co in BOOKS
                    }
```

`CellVerdict.rho` and `.n_trials_eff` now come from `book_verdicts["trials_corrected"]`, which is
the same value Task 6 computed. Add to each cell's JSON record:

```python
        "books": {
            name: {
                "decision": v.decision,
                "dsr": v.dsr,
                "pbo": v.pbo,
                "rho": v.rho,
                "n_trials_eff": v.n_trials_eff,
                "design_effect": v.design_effect,
                "n_obs_eff": v.n_obs_eff,
                "reasons": v.reasons,
            }
            for name, v in book_verdicts.items()
        },
        "effect_size_oos_avg_r": effect_size(current_oos_avg_r, winner_oos_avg_r),
        "moved_to_insufficient_under_obs_correction": (
            book_verdicts["raw"].decision != DECISION_INSUFFICIENT
            and book_verdicts["obs_corrected"].decision == DECISION_INSUFFICIENT
        ),
```

⚠ `DECISION_INSUFFICIENT` is already defined in `analytics/sweep_guard.py`; add it to
`wfo_resweep`'s existing `from analytics.sweep_guard import (...)` block if it is not there yet,
rather than comparing against the string `"INSUFFICIENT"` — that literal is exactly the restated
constant this branch exists to remove.

Change the default `--out` destination so a run's evidence survives:

```python
    parser.add_argument(
        "--out",
        default="docs/plans/scratch/st134-resweep-{label}.json",
        help=(
            "Write the full per-cell result as JSON here. Defaults under "
            "docs/plans/scratch/ so the run's evidence survives a reboot and lands "
            "in the backup glob — ST128's went to /tmp and is gone."
        ),
    )
```

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_wfo_resweep.py tests/test_sweep_guard.py -q
make lint-py && make typecheck
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/wfo_resweep.py tests/test_wfo_resweep.py
git commit -m "feat: score every ST134 cell under all four books and report effect size, not a pass count"
```

---

### Task 9: Live exposure per cell — the other half of the effect size

§5 item 2. A large OOS delta on a cell that fires twice a year is not the same decision as the
same delta on a cell that fires daily, and a report carrying only the delta cannot tell them
apart.

**Files:**

- Create: `analytics/live_exposure.py`
- Test: `tests/test_live_exposure.py` (create)
- Modify: `tools/wfo_resweep.py` (add `live_alerts_per_week` to each cell's JSON record)

**Interfaces:**

- Consumes: a `duckdb.DuckDBPyConnection` opened on the snapshot the sweep already uses.
- Produces: `alerts_per_week(conn: duckdb.DuckDBPyConnection, *, symbol: str, timeframe: str, strategy: str, since_ms: int, now_ms: int) -> float`.

⚠ The ledger column is **`tf`**, not `timeframe` — `backtest_runs` uses `timeframe` and
`signal_alert_outcomes` uses `tf`, so a query copied between them silently returns nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_live_exposure.py`:

```python
"""Live alert frequency per swept cell — ST134 section 5 item 2."""

import duckdb
import pytest

from analytics.live_exposure import alerts_per_week

_MS_PER_DAY = 86_400_000
_WEEK = 7 * _MS_PER_DAY


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE signal_alert_outcomes (
            signal_id TEXT PRIMARY KEY,
            symbol TEXT, tf TEXT, strategy TEXT, direction TEXT,
            fired_at_ms BIGINT
        )
    """)
    return conn


def _fire(conn: duckdb.DuckDBPyConnection, sid: str, ts: int, **kw: str) -> None:
    row = {"symbol": "BTCUSDT", "tf": "15m", "strategy": "bos", "direction": "long"}
    row.update(kw)
    conn.execute(
        "INSERT INTO signal_alert_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        [sid, row["symbol"], row["tf"], row["strategy"], row["direction"], ts],
    )


class TestAlertsPerWeek:
    def test_counts_only_the_named_cell(self) -> None:
        conn = _conn()
        _fire(conn, "a", 0)
        _fire(conn, "b", _MS_PER_DAY)
        _fire(conn, "c", 2 * _MS_PER_DAY, strategy="fvg")
        _fire(conn, "d", 3 * _MS_PER_DAY, tf="1h")
        _fire(conn, "e", 4 * _MS_PER_DAY, symbol="ETHUSDT")
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert rate == pytest.approx(2.0)

    def test_scales_to_a_weekly_rate(self) -> None:
        conn = _conn()
        for i in range(8):
            _fire(conn, f"s{i}", i * _MS_PER_DAY)
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=4 * _WEEK,
        )
        assert rate == pytest.approx(2.0)

    def test_excludes_fires_outside_the_window(self) -> None:
        conn = _conn()
        _fire(conn, "old", -_WEEK)
        _fire(conn, "in", _MS_PER_DAY)
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert rate == pytest.approx(1.0)

    def test_no_fires_is_zero_not_none(self) -> None:
        rate = alerts_per_week(
            _conn(),
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        )
        assert rate == 0.0

    def test_zero_length_window_is_zero_not_a_division_error(self) -> None:
        conn = _conn()
        _fire(conn, "a", 0)
        rate = alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="15m",
            strategy="bos",
            since_ms=100,
            now_ms=100,
        )
        assert rate == 0.0

    def test_reads_tf_not_timeframe(self) -> None:
        """Mutation guard: backtest_runs uses `timeframe`, this ledger uses `tf`.

        A query copied from the backtest side returns nothing and reads as a cell
        that never fires — silent, and in the direction that hides exposure.
        """
        conn = _conn()
        _fire(conn, "a", 0, tf="4h")
        assert alerts_per_week(
            conn,
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="bos",
            since_ms=0,
            now_ms=_WEEK,
        ) == pytest.approx(1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_live_exposure.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.live_exposure'`

- [ ] **Step 3: Implement**

Create `analytics/live_exposure.py`:

```python
"""How often a swept cell actually fires live.

ST134 section 5 item 2. An OOS ``avg_r`` delta is only half an effect size: the same
delta on a cell that fires daily and one that fires twice a year are different
decisions, and a report carrying only the delta cannot tell them apart.

Read-only against ``signal_alert_outcomes``.
"""

from __future__ import annotations

import duckdb

_MS_PER_WEEK = 7 * 86_400_000


def alerts_per_week(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    since_ms: int,
    now_ms: int,
) -> float:
    """Live alerts per week for one ``(symbol, timeframe, strategy)`` cell.

    ⚠ The ledger's timeframe column is ``tf``, while ``backtest_runs`` calls the same
    thing ``timeframe``. A query copied from the backtest side matches no rows and
    reads as a cell that never fires — silent, and in the direction that HIDES
    exposure, which is the direction that matters here.

    Returns ``0.0`` for an empty or zero-length window rather than raising, so a
    cell with no live history reports as unexposed rather than aborting the run.
    """
    span_ms = now_ms - since_ms
    if span_ms <= 0:
        return 0.0
    row = conn.execute(
        """
        SELECT COUNT(*) FROM signal_alert_outcomes
        WHERE symbol = ?
          AND tf = ?
          AND strategy = ?
          AND fired_at_ms >= ?
          AND fired_at_ms < ?
        """,
        [symbol, timeframe, strategy, since_ms, now_ms],
    ).fetchone()
    n = int(row[0]) if row is not None else 0
    return n * _MS_PER_WEEK / span_ms
```

In `tools/wfo_resweep.py`, import it and add to each cell's JSON record beside
`effect_size_oos_avg_r`. `symbol`, `timeframe` and `strategy` are already the per-cell loop
variables; `conn` is the snapshot connection the sweep opened. `since_ms` / `now_ms` are **not** in
scope yet — derive them once before the loop from the same `--since` the sweep uses:

```python
    since_ms = int(pd.Timestamp(args.since, tz="UTC").timestamp() * 1000)
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
```

```python
        "live_alerts_per_week": alerts_per_week(
            conn,
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            since_ms=since_ms,
            now_ms=now_ms,
        ),
```

- [ ] **Step 4: Run the tests**

```bash
poetry run pytest tests/test_live_exposure.py tests/test_wfo_resweep.py -q
make lint-py && make typecheck
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics/live_exposure.py tests/test_live_exposure.py tools/wfo_resweep.py
git commit -m "feat: report live alerts per week per cell, the other half of the ST134 effect size"
```

---

## After the last task

⛔ **Do NOT run the 273 cells as part of this plan, and do not write any TOML.** This plan builds
the machinery; running it is step 3 of the spec's §9 sequencing and its kill-switches gate it. The
order is: measure ρ → read the three-way verdict → null calibration → only then the 2×2 re-run.

Then the branch's landing chain:

- [ ] `make lint-py` · `make typecheck` · `make lint-md` — foreground, seconds each
- [ ] `make test-regression` — **required**: this diff touches `analytics/**/*.py`, which is inside
      CI's regression paths filter. Background it (~93s).
- [ ] Invoke `/post-branch`. Phases 0-4 run BEFORE `gh pr create` so the doc fixes ship in the
      initial push and CI runs once. Phase 0 is `make post-branch-checks`.
- [ ] `make preflight` at Step 7 — this diff contains Python, so it is in scope. It REPLACES
      `make test`; do not run both.
- [ ] Update SoT ST134, `MEMORY.md` Current State, and the handoff.
