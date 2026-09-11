"""ST134 section 4b — null calibration for the corrected sweep commit gate.

A gate that admits a known-null family is too permissive whatever it does to real
cells, so this is the check that makes the pre-registration's section 7 disclosure
survivable: it settles the permissiveness question WITHOUT reference to any real cell.

Construction: draw one shared random sign PER UTC DAY BIN and apply it to every arm's
trades in that bin, looking each trade up by ITS OWN timestamp rather than by
position. That is what actually preserves the within-day dependence
``_effective_obs_count`` prices — the correction being calibrated — while setting the
expected return to zero. Binning by day rather than by index also means arms of
different lengths (the production case: different ``tp_r`` arms have different trades,
per ``_build_perf_matrix``'s own docstring) are handled without truncation or
order-dependence.

⚠ **The shared sign leaves the UNCENTERED cross-moment exactly invariant, not the
measured correlation `_effective_trial_count` reads.** ``mean(x_j · x_k)`` is exactly
unchanged (every term carries ``s_i² = 1``), but every consumer reads the *centred*,
calendar-binned Pearson rho, and the flip moves each arm's sample mean — so rho shifts
in any finite sample. Report ``rho_before``/``rho_after`` rather than assert they match;
see :class:`NullCalibrationResult`.

Scope: the DSR leg only, which is the leg being corrected and the one that binds on all
82 scoreable cells in the ST128 run. PBO's CSCV is C(14,7) = 3,432 splits per
evaluation; running it inside 200 replicates would cost more than the whole re-run for
a leg this file does not change.

⛔ Decides nothing and writes no TOML.

⚠ **A LIBRARY, not a CLI.** There is no ``__main__``: the only caller is
``tools/wfo_resweep.py --null-calibration``, which bootstraps ``sys.path`` itself
before importing this. The bootstrap below is kept so the module stays importable
from a bare interpreter (and so a future ``__main__`` inherits it), but no
``test_bare_invocation_works`` guards it, because there is no bare invocation to
guard.
"""

from __future__ import annotations

import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.research_guards import GATE_DSR, deflated_sharpe_ratio  # noqa: E402
from analytics.research_guards.cluster import utc_day_keys  # noqa: E402
from analytics.sweep_guard import (  # noqa: E402
    DEFAULT_N_SPLITS,
    MIN_OBS_FACTOR,
    TrialPerf,
    _build_perf_matrix,
    _effective_obs_count,
    _effective_trial_count,
    _trial_sharpe,
)

DEFAULT_SEED = 20260910
"""The ST134 run seed, in ONE place.

It was restated as a bare ``20260910`` at three sites (this module's
``null_pass_rate``, ``wfo_resweep.median_rho_ci``, ``wfo_resweep``'s own
``_NULL_CALIBRATION_SEED``). A restated constant fails silently and in both
directions, and this one decides which nulls and which bootstrap draws two
kill-switches see — two sites moving and one not is a comparison between
different draws that still looks like one run.
"""

DEFAULT_REPLICATES = 200
"""§4b's pre-committed replicate count — restated at two sites before ST134's fix wave."""


def sign_flip_family(
    trials: Sequence[TrialPerf], rng: np.random.Generator
) -> list[TrialPerf]:
    """One sign per UTC day, shared across every arm — the spec's actual construction.

    Section 4b: "draw a shared random sign vector over the time bins and apply it to
    every arm's trades in that bin." A trade's bin is looked up from ITS OWN timestamp
    via :func:`utc_day_keys`, never by position, so this handles ragged arms (arms of
    different lengths, the normal shape for real ``tp_r`` families) without truncating
    any of them and without depending on how the caller ordered ``trials``. Two trades
    that land in the same UTC day — in any arm — always receive the same sign, which
    is what keeps the within-day design effect alive in the null replicate rather than
    flipping it toward independence.
    """
    if not trials:
        return []
    all_days = sorted({d for t in trials for d in utc_day_keys(t.times)})
    draw = rng.choice(np.array([-1.0, 1.0]), size=len(all_days))
    sign_by_day = dict(zip(all_days, draw, strict=True))
    out: list[TrialPerf] = []
    for t in trials:
        days = utc_day_keys(t.times)
        flipped = [
            float(r) * float(sign_by_day[d])
            for r, d in zip(t.returns, days, strict=True)
        ]
        out.append(TrialPerf(t.label, flipped, list(t.times)))
    return out


@dataclass(frozen=True)
class NullCalibrationResult:
    """A bare pass rate cannot distinguish "the gate refused every null" from "every
    replicate was insufficient and skipped" — this repo's own rule that a SKIP is not
    a PASS (``AGENTS.md``, ``sanity_checks.py``, the sensitive-terms gate).

    ``rate`` is computed over ``evaluated`` replicates only — never over
    ``n_replicates`` — so an insufficient-effective-obs skip (``correct_obs=True``)
    cannot silently score as a refusal, the anti-conservative direction for a control
    whose whole job is detecting over-permissiveness. ``rate`` is ``NaN`` when nothing
    could be evaluated: fewer than two trials, or every replicate skipped.

    ``rho_before`` is the input family's own measured correlation
    (``_effective_trial_count`` on ``trials`` before any flip); ``rho_after`` is the
    mean measured correlation across evaluated replicates. The two are reported, not
    asserted equal — see the module docstring on why the centred Pearson rho shifts
    even though the sign flip is uncentered-exact.
    """

    rate: float
    evaluated: int
    skipped: int
    rho_before: float
    rho_after: float


def null_pass_rate(
    trials: Sequence[TrialPerf],
    *,
    correct_trials: bool,
    correct_obs: bool = False,
    n_replicates: int = DEFAULT_REPLICATES,
    n_splits: int = DEFAULT_N_SPLITS,
    seed: int = DEFAULT_SEED,
) -> NullCalibrationResult:
    """Calibration verdict: how often a sign-flipped null still clears the DSR bar.

    Fewer than two trials cannot be deflated at all (mirrors
    ``evaluate_commit_gate``'s own ``n_trials < 2`` guard, which returns
    ``INSUFFICIENT`` rather than raising) — returned as a result with ``evaluated=0``
    rather than letting ``max()`` or ``statistics.variance`` raise on the degenerate
    input.

    ⚠ ``n_splits`` is not a free knob: it sets the CSCV bin count rho is measured over
    AND, through :data:`analytics.sweep_guard.MIN_OBS_FACTOR`, the effective-observation
    floor. A calibration run at a different ``n_splits`` from the gate it calibrates
    measures a DIFFERENT book, with both halves internally consistent and no test able
    to fail — which is why every default here is the gate's own constant rather than a
    copy of its value.
    """
    rng = np.random.default_rng(seed)
    min_obs = MIN_OBS_FACTOR * n_splits
    if len(trials) < 2:
        return NullCalibrationResult(
            rate=math.nan,
            evaluated=0,
            skipped=n_replicates,
            rho_before=math.nan,
            rho_after=math.nan,
        )

    rho_before, _ = _effective_trial_count(_build_perf_matrix(trials, min_obs))
    passes = 0
    evaluated = 0
    skipped = 0
    rho_after_sum = 0.0
    for _ in range(n_replicates):
        fam = sign_flip_family(trials, rng)
        rho_i, n_trials_eff_i = _effective_trial_count(_build_perf_matrix(fam, min_obs))
        chosen = max(fam, key=lambda t: _trial_sharpe(t.returns))
        n_obs: float = float(len(chosen.returns))
        n_trials: float = float(len(fam))
        if correct_trials:
            n_trials = n_trials_eff_i
        if correct_obs:
            _, n_obs_eff = _effective_obs_count(chosen)
            n_obs = n_obs_eff
            if n_obs < float(min_obs):
                skipped += 1
                continue
        srs = [_trial_sharpe(t.returns) for t in fam]
        dsr = deflated_sharpe_ratio(
            _trial_sharpe(chosen.returns),
            n_obs,
            n_trials=n_trials,
            sr_variance=statistics.variance(srs),
        )
        rho_after_sum += rho_i
        evaluated += 1
        if dsr >= GATE_DSR:
            passes += 1
    rate = passes / evaluated if evaluated else math.nan
    rho_after = rho_after_sum / evaluated if evaluated else math.nan
    return NullCalibrationResult(
        rate=rate,
        evaluated=evaluated,
        skipped=skipped,
        rho_before=rho_before,
        rho_after=rho_after,
    )
