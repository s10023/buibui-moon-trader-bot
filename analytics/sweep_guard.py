"""Sweep commit-gate — overfitting refusal for the WFO ``tp_r`` decision.

Wraps the pure :mod:`analytics.research_guards` statistics into a single
*commit / do-not-commit / insufficient* verdict for one swept cell
(``strategy × symbol × tf``). The gate is **additive** to the existing OOS
filter (``/param-sweep-apply`` already drops ``OVERFIT`` rows and requires
positive OOS ``avg_r``); it adds the multiple-testing correction the project
currently lacks.

Commit rule (all three must hold)::

    DSR >= dsr_threshold   AND   PBO <= pbo_threshold   AND   n_obs_eff >= MinTRL

``n_obs_eff`` is the ST134 pre-registration's own spelling of the bar (§6.4), and it
**is** ``n_obs`` on the default path — ``correct_obs`` defaults to off, so the third
leg sees ``float(n_obs)`` and the verdict is byte-identical to the pre-ST134 one.
Feeding the raw count into the leg under ``correct_obs=True`` would make the shipped
leg LOOSER than the pre-registered one (``n_obs_eff <= n_obs`` always), which is the
single direction §7's disclosure exists to defend against.

Pure: no DB / IO. Inputs are per-trial return series; the caller (param_sweep)
adapts ``SweepRow`` objects into :class:`TrialPerf`.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.forecast import effective_independent_series
from analytics.research_guards import (
    GATE_DSR,
    GATE_PBO,
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
from analytics.research_guards.cluster import cluster_stats, utc_day_keys

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

MINTRL_CONFIDENCE = 0.95
"""⚠ A CONFIDENCE LEVEL, not the DSR bar. It coincides with :data:`DSR_THRESHOLD`'s
value and means something else entirely, so it must never be folded into that family."""

DEFAULT_N_SPLITS = 14

MIN_OBS_FACTOR = 2
"""``min_obs = MIN_OBS_FACTOR * n_splits`` — the trade-count floor below which the
statistics are unstable. Named because the ST134 null calibration has to apply the
SAME floor as the gate it calibrates: a second copy there would let the two drift
while each stayed internally consistent."""

MIN_EFFECTIVE_TRIALS = 2.0
"""ST134 §2's pre-registered floor on ``n_trials_eff``.

Below two trials deflation is undefined. Named because two places need it — the clamp
in :func:`_effective_trial_count` and any reader asking whether a cell's corrected
count is FLOOR-BOUND (``tools/wfo_resweep.py``'s ``--books`` summary counts those, and
at k=9 every family clearing §4a's ``rho* = 0.5`` bar is one).
"""

DECISION_COMMIT = "COMMIT"
DECISION_BLOCK = "DO_NOT_COMMIT"
DECISION_INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class TrialPerf:
    """One swept param combo's realised trades (full-window, IS+OOS).

    ``returns`` are per-trade R multiples (after fees) and ``times`` are the
    aligned entry timestamps (ms) used to bin the CSCV performance matrix.
    """

    label: str
    returns: list[float]
    times: list[int]


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


def _trial_sharpe(returns: Sequence[float]) -> float:
    """Per-trade Sharpe of one trial: ``mean / std(ddof=1)``.

    ``0.0`` when fewer than two returns or the series has no dispersion.
    """
    arr = np.asarray(returns, dtype=np.float64)
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(arr)) / sd


def _build_perf_matrix(
    trials: Sequence[TrialPerf], n_bins: int
) -> npt.NDArray[np.float64]:
    """Calendar-binned ``(n_bins, n_trials)`` performance matrix for CSCV.

    Trials are not trade-aligned (different params -> different trades), so each
    column is binned onto a shared time grid over the union trade span; a cell
    holds the sum of that trial's per-trade R inside the bin.
    """
    mat = np.zeros((n_bins, len(trials)), dtype=np.float64)
    all_times = [t for tr in trials for t in tr.times]
    if not all_times:
        return mat
    tmin, tmax = min(all_times), max(all_times)
    span = tmax - tmin
    for j, tr in enumerate(trials):
        for t, r in zip(tr.times, tr.returns, strict=False):
            if span == 0:
                b = 0
            else:
                b = int((t - tmin) / span * n_bins)
                if b >= n_bins:
                    b = n_bins - 1
            mat[b, j] += r
    return mat


def _mean_arm_correlation(columns: dict[str, pd.Series]) -> float:
    """Mean off-diagonal Pearson correlation across the arms — ST134 §4a's statistic.

    Measured DIRECTLY rather than recovered by inverting ``n_eff``, because
    :func:`analytics.forecast.effective_independent_series` returns ``float(k)`` from
    four degenerate branches (fewer than two series, no estimable pair, a non-positive
    denominator, a non-positive ``n_eff``) and **every one of those inverts to exactly
    ``rho = 0.0``** — indistinguishable from a genuine measured zero, in the one value
    §4a's kill-switch decides on. A perfectly anti-correlated pair (``rho = -1``) takes
    the non-positive-denominator branch and reported ``0.0``.

    ``math.nan`` when no pair is estimable (a single arm, or every pairwise correlation
    NaN because a column is constant). NaN says *unmeasurable*; ``0.0`` says
    *measured, and uncorrelated*. The two point at different actions, and
    :func:`tools.wfo_resweep.median_rho_ci` drops the NaNs and reports how many it
    dropped rather than averaging them in as zeros.

    ⚠ This mirrors the correlation step inside ``effective_independent_series`` rather
    than delegating to it (that function returns only ``n_eff``). The anti-drift guard
    is ``test_rho_round_trips_from_n_eff``, which pins ``n_eff == k / (1 + (k-1)*rho)``
    on a non-degenerate family — if either spelling's estimator changes, that identity
    breaks.
    """
    corr = pd.DataFrame(columns).corr().to_numpy()
    k = corr.shape[0]
    off = corr[~np.eye(k, dtype=bool)]
    off = off[~np.isnan(off)]
    if off.size == 0:
        return math.nan
    return float(np.mean(off))


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

    ``rho`` is :func:`_mean_arm_correlation`'s direct measurement, NOT an inversion of
    ``n_eff`` — see that function for why the inversion silently reported ``0.0`` from
    four different degenerate branches. ``n_trials_eff`` is the delegated ``n_eff``
    after the pre-registered clamp bounds. The two satisfy ``n_eff = k / (1 + (k-1) *
    rho)`` jointly only when the raw value already lay inside ``[2, k]`` AND no
    degenerate branch fired — outside that the clamp overrides the count while ``rho``
    keeps reporting the measurement. This is deliberate: ``rho`` is the §4a
    kill-switch's decision statistic and must not become an artifact of the clamp.

    Two bounds, both pre-registered:

    * **floor** :data:`MIN_EFFECTIVE_TRIALS` — below two trials deflation is undefined.
      The raw ``n_trials < 2`` INSUFFICIENT guard in :func:`evaluate_commit_gate` still
      runs first, so this cannot route around it.
    * **ceiling k** — a correction may only ever REDUCE a family. Negative measured
      correlation would otherwise inflate it.

    Returns ``(rho, n_trials_eff)``. A single arm returns ``(nan, 1.0)``: no pair, so
    nothing is measurable and nothing to deflate, and the caller's own guard rejects it.
    """
    k = int(perf.shape[1])
    if k < 2:
        return math.nan, float(max(k, 1))
    columns = {f"arm{j}": pd.Series(perf[:, j]) for j in range(k)}
    n_eff_raw, _ = effective_independent_series(columns)
    rho = _mean_arm_correlation(columns)
    n_eff = min(max(float(n_eff_raw), MIN_EFFECTIVE_TRIALS), float(k))
    return rho, n_eff


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


def _decide(
    *,
    dsr: float,
    pbo: float,
    min_trl: float,
    n_obs_eff: float,
    dsr_threshold: float,
    pbo_threshold: float,
) -> tuple[str, list[str]]:
    """Apply the three hard checks. Returns ``(decision, failing_reasons)``.

    ``n_obs_eff`` is the **effective** observation count — ``float(n_obs)`` on the
    default path and the day-clustered ``n_obs / DEFF`` under ``correct_obs=True``.
    The pre-registration states the committable bar as ``DSR >= 0.95 ∧ PBO <= 0.5 ∧
    n_obs_eff >= MinTRL`` (§6.4), and feeding the RAW count here instead would leave
    the third leg looser than the one that was pre-registered, since ``n_obs_eff <=
    n_obs`` always. It also makes §1e's "the third leg is not touched" decision
    observable: its Decision Log names *MinTRL becoming the deciding leg on any cell*
    as the reversal condition, and under the raw count that observable could not occur
    by construction. §1e is about WHICH LEG (MinTRL rather than ``boot_lo``), never
    about which count feeds it.
    """
    reasons: list[str] = []
    if dsr < dsr_threshold:
        reasons.append(f"DSR {dsr:.2f} < {dsr_threshold:.2f}")
    if pbo > pbo_threshold:
        reasons.append(f"PBO {pbo:.2f} > {pbo_threshold:.2f}")
    if n_obs_eff < min_trl:
        trl = "∞" if math.isinf(min_trl) else f"{math.ceil(min_trl)}"
        # `:g` so an integral effective count (every default-path verdict, where
        # this is `float(n_obs)`) still renders as `n 40` rather than `n 40.0`.
        reasons.append(f"n {n_obs_eff:g} < MinTRL {trl}")
    return (DECISION_COMMIT if not reasons else DECISION_BLOCK, reasons)


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
    truncated grid cannot make DSR look better than it is. This governs the
    **uncorrected** path only: when ``correct_trials=True``, ``effective_trials``
    is overwritten by ``n_trials_eff``, whose ceiling is ``k = len(all_trials)``
    rather than ``n_grid`` — :class:`~analytics.param_sweep.ParamSweepReport`'s
    ``all_rows`` is what keeps the corrected callers in the regime where
    ``k == n_grid``, so the two floors coincide there. That coincidence is now
    ENFORCED rather than documented: a corrected call with ``n_grid > len(all_trials)``
    raises :class:`ValueError`, because the ceiling would otherwise silently discard
    the ``n_grid`` floor and a truncated family would deflate against its own
    truncation. Every production caller passes ``all_rows`` (one row per grid combo),
    so this cannot fire on the shipped path; it exists so a FUTURE top-N caller fails
    loudly instead of quietly.

    ``correct_trials`` and ``correct_obs`` are ST134's two counting corrections and
    both default to **off**, so an existing caller's verdict is unchanged. They pull
    in opposite directions and are meant to be measured as a 2x2 — see the
    pre-registration at
    ``docs/superpowers/specs/2026-09-10-st134-sweep-gate-trial-independence-preregistration.md``.

    ``correct_obs`` feeds BOTH consumers of the observation count — the DSR leg and
    the MinTRL leg — because §6.4 states the bar as ``n_obs_eff >= MinTRL``. The raw
    ``n_obs`` is still reported on the verdict, so the two counts stay auditable side
    by side. See
    ``docs/superpowers/specs/2026-09-10-st134-sweep-gate-trial-independence-preregistration.md``.
    """
    n_trials = len(all_trials)
    n_obs = len(chosen.returns)
    min_obs = MIN_OBS_FACTOR * n_splits

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
        if n_grid > n_trials:
            raise ValueError(
                f"correct_trials needs the FULL family: n_grid {n_grid} > "
                f"{n_trials} trials. The correction's ceiling is k = len(all_trials), "
                "so a truncated family would deflate against its own truncation and "
                "silently lose the n_grid floor — pass all_rows, not the top-N rows."
            )
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
                decision=DECISION_INSUFFICIENT,
                dsr=None,
                pbo=None,
                min_trl=None,
                n_obs=n_obs,
                n_trials=n_trials,
                reasons=[
                    f"{effective_obs:.1f} effective trades < {min_obs} "
                    f"(2x n_splits) after day-clustering {n_obs} raw — stats unstable"
                ],
                rho=rho,
                n_trials_eff=n_trials_eff,
                design_effect=design_effect,
                n_obs_eff=n_obs_eff,
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
        # The EFFECTIVE count, per §6.4's `n_obs_eff >= MinTRL`. Identical to
        # `float(n_obs)` whenever `correct_obs` is off, which is the default.
        n_obs_eff=effective_obs,
        dsr_threshold=dsr_threshold,
        pbo_threshold=pbo_threshold,
    )
    return CommitGateVerdict(
        decision=decision,
        dsr=dsr,
        pbo=pbo,
        min_trl=min_trl,
        n_obs=n_obs,
        n_trials=n_trials,
        reasons=reasons,
        rho=rho,
        n_trials_eff=n_trials_eff,
        design_effect=design_effect,
        n_obs_eff=n_obs_eff,
    )
