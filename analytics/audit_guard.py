"""Audit-tool verdicts via bootstrap CI + multiple-testing haircut.

Replaces the crude ±0.05R bar in ``tools/gate_audit.py`` and
``tools/adr_threshold_audit.py`` with two statistical gates that BOTH must hold
before a cell earns an ``ENABLE`` / ``DISABLE`` verdict:

1. **Effect size (cluster bootstrap CI).** A CI on the suppressed slice's mean R
   must clear the ±``bar`` on the correct side (``ci.hi <= -bar`` → losers we
   should drop; ``ci.lo >= +bar`` → winners we must not suppress). It resamples
   whole **clusters**, not trades.
2. **Multiple-testing significance (Holm haircut).** Each tested cell's
   two-sided p-value (from its slice Sharpe) is Holm-adjusted across the family
   of cells tested in one audit run; the adjusted p-value must be ``< alpha``.
   The t-statistic uses ``n_eff = n / DEFF``, never the trade count.

⚠ **BOTH legs are priced on the cluster unit, and it is REQUIRED.** A block
bootstrap absorbs *serial* dependence — it resamples runs adjacent in the array
it is handed — and it reached neither channel here: same-day cross-symbol trades
are scattered through that array, and no trade query feeding a cell carries an
``ORDER BY``, so its adjacency was DuckDB storage order. Undeflated, this repo's
own panel read 70 of 125 cells as significant where 52 survive, on a
trade-weighted design effect of **4.991**. See
:mod:`analytics.research_guards.cluster` and
``docs/audits/2026-08-25-st80-audit-guard-cluster-key.md``.

Cells with ``n_supp < min_n``, **or with an unusable ``cluster_key``**, are
``INSUFFICIENT`` and excluded from the family (they never inflate the haircut
denominator). The verdict / reason shape mirrors :mod:`analytics.sweep_guard` so
the project's guard consumers stay consistent.

Pure: no DB / IO. Consumes :mod:`analytics.research_guards`.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Literal

import numpy as np
import numpy.typing as npt

from analytics.research_guards import (
    cluster_bootstrap_ci,
    cluster_stats,
    haircut_sharpe,
)

DEFAULT_BAR = 0.05
DEFAULT_ALPHA = 0.05
DEFAULT_MIN_N = 30
DEFAULT_N_BOOT = 2000
DEFAULT_SEED = 12345

DECISION_ENABLE = "ENABLE"
DECISION_DISABLE = "DISABLE"
DECISION_CONCENTRATE = "CONCENTRATE"
DECISION_INSUFFICIENT = "INSUFFICIENT"

_Method = Literal["bonferroni", "holm", "bhy"]

_NORM = NormalDist()


@dataclass(frozen=True)
class AuditCell:
    """One cell's inputs.

    ``supp_r`` are the per-trade R multiples of the would-be-suppressed slice
    (the verdict statistic operates on their mean). ``kept_r`` are the surviving
    trades' R — used only for the ``CONCENTRATE`` kept-vs-suppressed comparison;
    pass ``[]`` when there is no kept slice (e.g. the ADR aggregate view).

    ``cluster_key`` is the dependence unit, one entry per ``supp_r`` row, and it
    is **REQUIRED and positioned before the defaulted** ``kept_r`` so mypy
    forces every call site to state it. For this repo's panels that is the UTC
    day via :func:`analytics.research_guards.utc_day_keys` — a **lower bound**
    on a 24/7 tape rather than an exact session, which that function documents
    and measures.

    ⚠ **A mismatched length FAILS CLOSED to ``INSUFFICIENT``** rather than
    falling back to per-trade resampling. An unmeasurable panel and an
    uncorrelated one must not both read as a deflator of 1.0 — that is the
    fail-open shape this repo already closed in ``tools/distil_power.py``, and
    the whole reason the key is required instead of optional.

    A caller with genuinely ungrouped observations declares that by passing
    distinct keys (``range(len(supp_r))``), which costs nothing — singleton
    clusters give ICC 0 and DEFF 1 — and leaves a greppable decision where a
    silent default would leave none.
    """

    label: str
    supp_r: Sequence[float]
    cluster_key: Sequence[Hashable]
    kept_r: Sequence[float] = field(default_factory=list)


@dataclass(frozen=True)
class CellVerdict:
    decision: str  # ENABLE | DISABLE | CONCENTRATE | INSUFFICIENT
    n_supp: int
    n_kept: int
    supp_avg: float | None
    kept_avg: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    n_tests: int
    reasons: list[str]
    n_clusters: int | None = None
    """Distinct ``cluster_key`` values in the suppressed slice — the real sample
    size behind ``n_supp``. ``None`` when the cell was never tested."""
    design_effect: float | None = None
    """``1 + (m̄-1)·ICC``: how many trades it takes to buy one independent
    observation. ``None`` when untested. Reported because a deflator applied
    silently is indistinguishable from one that was forgotten."""
    powered_null: bool = False
    """True iff the CI lies strictly INSIDE ±``bar`` — i.e. an effect worth
    acting on has been ruled out, not merely left uncalled.

    Computed by :func:`powered_null`, which is the single definition — call it
    rather than restating ``ci_lo > -bar and ci_hi < bar`` anywhere. A
    sample-size floor cannot distinguish "the effect is smaller than the bar"
    from "the CI is five times the bar and we cannot tell", and six audits
    inferred the former from something that was not power; that function's
    docstring lists the shapes. Defaults ``False``: a cell that was never
    tested (``n < min_n``, no CI) has established nothing.
    """


def powered_null(ci_lo: float | None, ci_hi: float | None, *, bar: float) -> bool:
    """True iff a two-sided CI lies strictly INSIDE ``±bar``.

    **The one honest test for a powered null, extracted 2026-08-14 so it stops
    being re-derived.** Six audits have now inferred power from something that
    is not power: four from a sample-size floor (``n >= min_n``), one from
    failure to clear the bar (ST27), and one — ``multi_regime_study`` — from
    ``|Δ| < MDE``, which since ``MDE = 2.802 × SE`` is a significance test
    wearing a power label. All six share a shape: a criterion computed from the
    data's own noise can report that an effect was *not seen*, never that one
    was *ruled out*. Only the bar carries the notion of "worth acting on", so
    only a CI sized against the bar can license a negative claim.

    Returns ``False`` for a missing or non-finite bound: a cell that was never
    tested has established nothing. That default is load-bearing — the failure
    the callers care about is a null claimed too easily, so the untested case
    must fall to ``INSUFFICIENT``, never to ``powered``.

    For a best-of-k arm sweep the predicate is one-sided instead; see
    :func:`analytics.sl_horizon.negative_claim_licensed`, which documents why
    the two must stay distinct.
    """
    if ci_lo is None or ci_hi is None:
        return False
    if not (math.isfinite(ci_lo) and math.isfinite(ci_hi)):
        return False
    return ci_lo > -bar and ci_hi < bar


def _mean(arr: npt.NDArray[np.float64]) -> float:
    return float(np.mean(arr))


def _slice_sharpe(arr: npt.NDArray[np.float64]) -> float:
    """Per-trade Sharpe ``mean / std(ddof=1)``; ``0.0`` with no dispersion.

    ``±inf`` for a zero-variance, non-zero-mean slice: a deterministic edge has
    no sampling uncertainty, so it is treated as maximally significant.
    """
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    mean = float(np.mean(arr))
    if sd == 0.0:
        return 0.0 if mean == 0.0 else math.copysign(math.inf, mean)
    return mean / sd


def _two_sided_p(abs_sr: float, n_eff: int) -> float:
    """Two-sided p-value for ``Sharpe != 0`` — matches ``haircut_sharpe``'s
    internal ``t = sr·√n`` so the family p-values align exactly with the value
    the haircut recomputes per cell.

    ⚠ **``n_eff``, never the trade count.** Callers must pass the
    cluster-deflated count and pass the *same* integer to ``haircut_sharpe``,
    or the two disagree about the test they are running. Undeflated, this leg
    called 70 of 125 cells significant where 52 survive — a larger error
    channel than the CI it sits beside.
    """
    t = abs_sr * math.sqrt(n_eff)
    return 2.0 * (1.0 - _NORM.cdf(abs(t)))


@dataclass
class _Eligible:
    idx: int
    arr: npt.NDArray[np.float64]
    keys: Sequence[Hashable]
    sr: float
    abs_p: float
    n_eff: int
    n_clusters: int
    design_effect: float


def evaluate_audit_cells(
    cells: Sequence[AuditCell],
    *,
    bar: float = DEFAULT_BAR,
    alpha: float = DEFAULT_ALPHA,
    min_n: int = DEFAULT_MIN_N,
    haircut_method: _Method = "holm",
    n_boot: int = DEFAULT_N_BOOT,
    seed: int | None = DEFAULT_SEED,
    enable_concentrate: bool = True,
) -> list[CellVerdict]:
    """Verdict per cell, sharing one Holm haircut family across all tested cells.

    Returns a list aligned 1:1 with ``cells``. A cell earns ``ENABLE`` /
    ``DISABLE`` only if its bootstrap CI clears the ±``bar`` AND its Holm-adjusted
    p-value is ``< alpha``; ``CONCENTRATE`` refines the ``DISABLE`` branch when the
    kept slice out-performs the (reliably positive) suppressed slice by ≥ ``bar``.
    Everything else is ``INSUFFICIENT``.
    """
    eligible: list[_Eligible] = []
    skip_reason: dict[int, str] = {}
    for i, cell in enumerate(cells):
        arr = np.asarray(cell.supp_r, dtype=np.float64)
        n = int(arr.shape[0])
        if len(cell.cluster_key) != n:
            # FAIL CLOSED. Resampling trades here would report a confident,
            # undeflated verdict — the exact failure the required key exists to
            # prevent — so an unusable key must cost the verdict, not the guard.
            skip_reason[i] = f"cluster_key length {len(cell.cluster_key)} != n_supp {n}"
            continue
        if n < min_n or n < 2:
            skip_reason[i] = f"n {n} < min_n {min_n}"
            continue
        cs = cluster_stats(arr, cell.cluster_key)
        n_eff = max(2, int(round(cs.n_eff)))
        sr = _slice_sharpe(arr)
        eligible.append(
            _Eligible(
                i,
                arr,
                cell.cluster_key,
                sr,
                _two_sided_p(abs(sr), n_eff),
                n_eff,
                cs.n_clusters,
                cs.design_effect,
            )
        )

    family_p = [e.abs_p for e in eligible]
    n_tests = len(family_p)
    adj_by_idx: dict[int, float] = {}
    ci_by_idx: dict[int, tuple[float, float]] = {}
    stats_by_idx: dict[int, tuple[int, float]] = {
        e.idx: (e.n_clusters, e.design_effect) for e in eligible
    }
    for e in eligible:
        # SAME integer in both, or the family p-value and the value the haircut
        # recomputes describe different tests.
        hr = haircut_sharpe(
            abs(e.sr),
            e.n_eff,
            n_tests,
            method=haircut_method,
            pvalues_all=family_p,
        )
        adj_by_idx[e.idx] = hr.adjusted_pvalue
        ci = cluster_bootstrap_ci(
            e.arr, e.keys, _mean, n_boot=n_boot, alpha=alpha, seed=seed
        )
        ci_by_idx[e.idx] = (ci.lo, ci.hi)

    out: list[CellVerdict] = []
    for i, cell in enumerate(cells):
        supp = np.asarray(cell.supp_r, dtype=np.float64)
        kept = np.asarray(cell.kept_r, dtype=np.float64)
        n_supp = int(supp.shape[0])
        n_kept = int(kept.shape[0])
        supp_avg = float(np.mean(supp)) if n_supp else None
        kept_avg = float(np.mean(kept)) if n_kept else None

        if i not in adj_by_idx:
            out.append(
                CellVerdict(
                    DECISION_INSUFFICIENT,
                    n_supp,
                    n_kept,
                    supp_avg,
                    kept_avg,
                    None,
                    None,
                    None,
                    n_tests,
                    [skip_reason.get(i, f"n {n_supp} < min_n {min_n}")],
                )
            )
            continue

        adj_p = adj_by_idx[i]
        ci_lo, ci_hi = ci_by_idx[i]
        significant = adj_p < alpha
        reasons: list[str] = []

        if ci_hi <= -bar and significant:
            decision = DECISION_ENABLE
        elif ci_lo >= bar and significant:
            if (
                enable_concentrate
                and kept_avg is not None
                and supp_avg is not None
                and kept_avg >= supp_avg + bar
            ):
                decision = DECISION_CONCENTRATE
            else:
                decision = DECISION_DISABLE
        else:
            decision = DECISION_INSUFFICIENT
            if not significant:
                reasons.append(f"Holm-adj p {adj_p:.3f} >= alpha {alpha:.2f}")
            if ci_hi > -bar and ci_lo < bar:
                reasons.append(
                    f"CI [{ci_lo:.3f}, {ci_hi:.3f}] does not clear ±{bar:.2f}R"
                )

        out.append(
            CellVerdict(
                decision,
                n_supp,
                n_kept,
                supp_avg,
                kept_avg,
                ci_lo,
                ci_hi,
                adj_p,
                n_tests,
                reasons,
                n_clusters=stats_by_idx[i][0],
                design_effect=stats_by_idx[i][1],
                powered_null=powered_null(ci_lo, ci_hi, bar=bar),
            )
        )
    return out
