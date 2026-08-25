"""Shared state-tag audit machinery — pure library.

Extracted from ``analytics/venue_premium.py`` (H14) so that H14, H15 and any
later state-tag audit share ONE implementation of the day collapse, cell
construction, per-family DSR/PBO, the stability check, and the pre-committed
verdict map. The directional-DSR defect shipped in H8
(``analytics/indicator_condition.py``) precisely because this class of logic
was duplicated per audit; consolidating to one module is the fix.

**H8 now shares the statistics from here too** (``cell_sharpe``,
``family_pbo``, ``family_dsr``, ``mintrl_n_ok``), so the copy the defect
actually shipped in no longer exists.

**Where the line is drawn, and why it is not further along.** Shared here:
the *statistics* — Sharpe, the family PBO fold, the deflated Sharpe, and the
MinTRL magnitude fold. These answer questions with one correct answer, so a
second implementation is only ever a second place for the same defect. NOT
shared: each audit's **verdict map**, which is its own pre-registration.
H8's ``_map_verdict`` carries a two-sample with-vs-without lift CI and reads
its thresholds off an ``IndicatorConditionConfig``; ``map_verdict`` here
carries the early/late stability leg instead. Collapsing the two into one
parameterised function would let a future edit to one audit's gate silently
move another audit's *pre-committed* gate — the opposite of what a
pre-registration is for. Statistics converge; gates stay per-spec.

PURE: no DB, no network, no file IO. No H14- or H15-specific vocabulary
lives here — series construction, state labels, and family-key grouping stay
in the calling module (``venue_premium.py`` for H14, ``fx_carry.py`` for
H15, ``indicator_condition.py`` for H8).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.audit_guard import (
    DECISION_CONCENTRATE,
    DECISION_DISABLE,
    DECISION_ENABLE,
    DECISION_INSUFFICIENT,
    AuditCell,
    evaluate_audit_cells,
)
from analytics.research_guards import (
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)

Z_WINDOW = 90

DAY_MS = 86_400_000

VERDICT_BUILD = "BUILD"
VERDICT_AVOID = "AVOID"
VERDICT_NO_EDGE = "NO-EDGE"
VERDICT_INSUFFICIENT = "INSUFFICIENT"

BAR = 0.05
ALPHA = 0.05
MIN_N = 30
DSR_FLOOR = 0.95
PBO_CEIL = 0.5
MINTRL_CONFIDENCE = 0.95

_PBO_PERIODS = 20
_PBO_SPLITS = 4


def causal_zscore(series: pd.Series, window: int = Z_WINDOW) -> pd.Series:
    """z of today's value against the ``window`` days STRICTLY before it.

    The ``.shift(1)`` is the causality guarantee and is asserted by a
    perturbation test — do not remove it as a "warm-up" convenience.
    """
    prior = series.shift(1)
    mean = prior.rolling(window, min_periods=window).mean()
    std = prior.rolling(window, min_periods=window).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def collapse_to_daily(
    trades: pd.DataFrame, states: pd.Series, *, lag_days: int = 1
) -> pd.DataFrame:
    """One observation per (UTC day, direction) — the H10 independence lesson
    applied in advance (spec Sec.6).

    25 symbols x 4 timeframes sharing one market-wide daily state are nowhere
    near 100 independent draws, so the trade-level view is not the unit of
    inference; H10 hit exactly this and fixed it by collapsing 6,000
    symbol-weeks to one observation per calendar week. Here it is one
    observation per UTC day.

    ``states`` is indexed by day number (``entry_time // _DAY_MS``). A trade
    entering during day ``d`` is tagged with ``states[d - lag_days]`` — the
    last completed daily close STRICTLY BEFORE its entry (spec Sec.5), never
    ``states[d]``, which would be look-ahead: a trade during day *d* cannot
    know day *d*'s own close, which is not final until 00:00 UTC on *d+1*
    (amendments.md A2). Days with no ``lag_days``-prior state (warm-up, or a
    gap in the premium series) are dropped via the trailing ``dropna``.

    Mapping ``day - lag_days`` rather than ``states.shift(lag_days)`` is
    load-bearing, not stylistic: the day index has real gaps, and a
    positional shift keeps the same index while sliding VALUES down by
    position — so across a gap it silently borrows a neighbour's state
    instead of correctly finding no entry and dropping the row.
    """
    if trades.empty:
        return pd.DataFrame(columns=["day", "direction", "mean_r", "state"])
    df = trades.copy()
    df["day"] = (df["entry_time"] // DAY_MS).astype("int64")
    # groupby(...)[col].mean() with as_index=False types ambiguously under
    # pandas-stubs (SeriesGroupBy.mean() -> Series even though the as_index=
    # False runtime result is a DataFrame); grouping indexed (the default),
    # renaming the resulting Series, then reset_index() types cleanly as a
    # DataFrame while producing the identical column layout.
    daily = (
        df.groupby(["day", "direction"])["pnl_r"].mean().rename("mean_r").reset_index()
    )
    daily["state"] = (daily["day"] - lag_days).map(states)
    return daily.dropna(subset=["state"]).reset_index(drop=True)


def build_state_cells(daily: pd.DataFrame) -> list[AuditCell]:
    """One cell per (state, direction); ``kept_r`` is the same-direction
    complement (all other states), which keeps ``audit_guard``'s
    CONCENTRATE branch meaningful (spec Sec.7).

    ``collapse_to_daily`` returns rows sorted by day ascending (day is the
    primary ``groupby`` sort key); filtering here preserves that order, so
    each cell's ``supp_r``/``kept_r`` stay day-ordered — ``_sign_agrees_
    early_late`` below relies on that ordering as its time axis.
    """
    cells: list[AuditCell] = []
    if daily.empty:
        return cells
    for direction in sorted(daily["direction"].unique()):
        side = daily[daily["direction"] == direction]
        for state in sorted(side["state"].unique()):
            inside_rows = side.loc[side["state"] == state]
            inside = inside_rows["mean_r"]
            outside = side.loc[side["state"] != state, "mean_r"]
            # ``collapse_to_daily`` already emits ONE row per (day, direction),
            # so the day key makes every cluster a singleton and the design
            # effect is exactly 1.0. That is not a no-op worth skipping: it is
            # how AGENTS.md's "book-day rows are already aggregated and must NOT
            # be deflated again" enforces itself instead of relying on a reader
            # to remember it. Passing the real day also keeps the claim
            # checkable — a future change that stops collapsing would start
            # deflating here automatically.
            cells.append(
                AuditCell(
                    label=f"{state}|{direction}",
                    supp_r=list(inside),
                    cluster_key=[int(d) for d in inside_rows["day"]],
                    kept_r=list(outside),
                )
            )
    return cells


def cell_sharpe(arr: npt.NDArray[np.float64]) -> float:
    """Mean/std Sharpe of a day-observation array; ``0.0`` with < 2 points or
    zero dispersion (mirrors ``analytics/indicator_condition.py``'s helper —
    unlike ``audit_guard._slice_sharpe``, a deterministic slice is NOT
    treated as infinitely significant here, since this feeds the MinTRL/DSR
    denominators rather than a significance test).
    """
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(arr)) / sd


def family_pbo(arrays: list[npt.NDArray[np.float64]]) -> float | None:
    """PBO across an (axis, direction) family's states.

    These states are DISJOINT populations with no shared row index —
    day-populations for H14/H15, trade-populations for H8. A swept-parameter
    family is the opposite case and needs none of this: ``analytics/
    sl_horizon.py``'s k-grid re-scores the SAME signals under each arm, so it
    already has a natural paired T x N matrix. Lacking one, each state's own
    ordered return sequence (its row order, a time proxy) is folded into
    ``_PBO_PERIODS`` equal chunks to give ``cscv_pbo`` a shared T axis.
    ``None`` when fewer than 2 states in the family clear the period floor
    (nothing to overfit to with a single trial).
    """
    usable = [a for a in arrays if a.shape[0] >= _PBO_PERIODS]
    if len(usable) < 2:
        return None
    cols: list[npt.NDArray[np.float64]] = []
    for a in usable:
        n_use = (a.shape[0] // _PBO_PERIODS) * _PBO_PERIODS
        cols.append(a[:n_use].reshape(_PBO_PERIODS, -1).mean(axis=1))
    matrix = np.column_stack(cols)
    try:
        return float(cscv_pbo(matrix, n_splits=_PBO_SPLITS).pbo)
    except ValueError:
        return None


def family_dsr(
    target_r: npt.NDArray[np.float64], family_arrays: list[npt.NDArray[np.float64]]
) -> float:
    """Deflated Sharpe of ``target_r`` against its (axis, direction) family's
    trial Sharpes (mirrors ``analytics/indicator_condition.py``'s helper).

    H14 families mix DISABLE-bound cells (positive Sharpe) with ENABLE-bound
    ones (negative Sharpe) — e.g. ``elevated`` and ``depressed`` share the
    ``(level, long)`` family. ``deflated_sharpe_ratio`` measures confidence
    that the TRUE Sharpe exceeds a POSITIVE expected-max-of-N benchmark, so
    feeding it a raw negative Sharpe always deflates to ~0 regardless of how
    reliable the negative effect is — an ENABLE/AVOID cell could never clear
    the family gate. Using the MAGNITUDE of every Sharpe (same treatment as
    the MinTRL call above) asks the direction-agnostic question that
    actually matters here: is this cell's *extremity*, whichever way it
    points, still credible after accounting for having tested N states.
    """
    trial_srs = [abs(cell_sharpe(a)) for a in family_arrays if a.shape[0] >= 2]
    if not trial_srs:
        trial_srs = [abs(cell_sharpe(target_r))]
    return deflated_sharpe_ratio(
        abs(cell_sharpe(target_r)),
        max(int(target_r.shape[0]), 1),
        trial_srs=trial_srs,
    )


def mintrl_n_ok(
    arr: npt.NDArray[np.float64],
    n_obs: int,
    *,
    confidence: float = MINTRL_CONFIDENCE,
) -> tuple[float, bool]:
    """``(MinTRL, n_obs >= MinTRL)`` for one cell's own observation array.

    **The magnitude fold is the entire point of this helper.**
    ``min_track_record_length`` answers "how many observations before this
    *positive* Sharpe is credible", so it returns ``inf`` for a negative one.
    Fed a signed Sharpe it therefore makes the AVOID / negative-direction
    branch of any gate that uses it **structurally unreachable** — the audit
    then reports "no negative effect found" no matter what the data says, and
    fails silently rather than loudly. That is the same directional defect
    that shipped in H8's ``_family_dsr`` (PR #546) wearing a different hat,
    which is why the fold lives in ONE function instead of being re-typed at
    each call site (AGENTS.md's DIRECTIONAL-metrics rule).

    Disclosed consequence, same as ``family_dsr``: folding to magnitude makes
    this leg marginally MORE permissive than the signed form. Bias runs toward
    more passes, never fewer.

    ``n_obs`` is passed explicitly rather than read off ``arr`` because callers
    already hold it (``audit_guard``'s ``CellVerdict.n_supp``). The two are
    equal by construction — ``audit_guard`` sets ``n_supp = supp.shape[0]`` —
    and keeping the parameter makes that equality checkable at each call site
    instead of quietly assumed here.
    """
    mintrl = min_track_record_length(
        abs(cell_sharpe(arr)), target_sr=0.0, confidence=confidence
    )
    return mintrl, float(n_obs) >= mintrl


def sign_agrees_early_late(values: list[float]) -> bool:
    """Split the (day-ordered) sequence at its median index; both halves must
    share a non-zero sign (amendments.md A3 point 2 — a VERDICT INPUT, not a
    printed-only column). This is the stability check that separates a real
    state effect from one regime's artifact: a cell whose sign flips between
    its early and late history fails here regardless of its overall CI /
    DSR / PBO.
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.shape[0] < 2:
        return False
    mid = arr.shape[0] // 2
    early, late = arr[:mid], arr[mid:]
    if early.size == 0 or late.size == 0:
        return False
    early_mean, late_mean = float(early.mean()), float(late.mean())
    return (early_mean > 0 and late_mean > 0) or (early_mean < 0 and late_mean < 0)


def map_verdict(
    decision: str,
    *,
    powered_null: bool,
    n_days_ok: bool,
    dsr: float | None,
    pbo: float | None,
    stable: bool,
) -> str:
    """Map one ``audit_guard`` cell decision to an H14 verdict.

    **Sign inversion (spec Sec.7).** ``audit_guard`` answers a *suppression*
    question, so its decisions run opposite to "is this state good":
    ``DISABLE`` means the state's slice is reliably POSITIVE (-> BUILD, a
    size-up candidate); ``ENABLE`` means reliably NEGATIVE (-> AVOID, a
    suppression candidate). This exact inversion already caused defects in
    ST9 and again in H8 (``analytics/indicator_condition.py``) — do not "fix"
    this to the intuitive DISABLE->AVOID / ENABLE->BUILD map.

    **INSUFFICIENT is two different things (amendments.md A1).**
    ``audit_guard`` returns one ``INSUFFICIENT`` decision for both "n <
    min_n" (genuinely underpowered) and "the CI/Holm gate never cleared"
    (``analytics/audit_guard.py``'s bare ``else`` branch). Collapsing them
    makes the spec's most likely outcome — all cells NO-EDGE (spec Sec.8
    branch 2) — unreachable, so the two must be split.

    **⚠ CORRECTED 2026-08-13 — the split criterion was ``n_supp >= MIN_N``
    and that is a SAMPLE-SIZE FLOOR, not power.** It cannot distinguish "the
    effect is smaller than the bar" from "the CI is five times the bar and we
    cannot tell", so it made NO-EDGE *always* reachable — the mirror of the
    H8 defect it was written to avoid. Measured on H14's own published table:
    **0 of 10 NO-EDGE cells had a CI excluding the bar, the median CI
    half-width was 5.5x the bar, and 8 of 10 point estimates EXCEEDED the
    bar.** The honest criterion is containment — ``audit_guard`` now computes
    it as ``CellVerdict.powered_null`` (CI strictly inside ±``bar``). Do not
    reintroduce an ``n``-based split here; ``n`` says a test ran, never that
    it could have seen anything.

    **The full pre-committed gate (amendments.md A3 / spec Sec.7).** A
    BUILD/AVOID additionally requires ``n_days_ok`` (n >= MinTRL(0.95)),
    family DSR >= 0.95 AND PBO <= 0.5 (both non-``None``), and ``stable``
    (early/late sign agreement). Anything failing any one of these is
    NO-EDGE — never silently promoted to BUILD/AVOID.
    """
    if decision == DECISION_INSUFFICIENT:
        return VERDICT_NO_EDGE if powered_null else VERDICT_INSUFFICIENT
    if decision == DECISION_CONCENTRATE:
        return VERDICT_NO_EDGE
    family_ok = (
        dsr is not None and pbo is not None and dsr >= DSR_FLOOR and pbo <= PBO_CEIL
    )
    gate_ok = n_days_ok and family_ok and stable
    if decision == DECISION_DISABLE and gate_ok:
        return VERDICT_BUILD
    if decision == DECISION_ENABLE and gate_ok:
        return VERDICT_AVOID
    return VERDICT_NO_EDGE


def evaluate_states(
    cells: list[AuditCell],
    family_key: Callable[[str], tuple[str, str]],
    *,
    bar: float = BAR,
    alpha: float = ALPHA,
    min_n: int = MIN_N,
) -> list[tuple[str, str]]:
    """Pre-committed verdict per cell, sharing ONE Holm haircut family.

    ``family_key`` maps a cell label to its ``(axis, direction)`` DSR/PBO
    sub-family. All ``cells`` share one Holm family; DSR/PBO are computed per
    sub-family, mirroring H8's per-indicator-axis grouping.

    ``bar`` is the effect-size floor IN THE UNITS OF THE OBSERVATION. H14
    passes R-multiples and uses the 0.05R default; H15's forward panel
    passes vol-normalised returns and its own BAR_VOL. Passing an R-scaled
    bar to a raw-return panel makes every verdict unreachable — see spec
    Sec.6.
    """
    if not cells:
        return []
    cell_verdicts = evaluate_audit_cells(cells, bar=bar, alpha=alpha, min_n=min_n)

    by_family: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cells):
        by_family.setdefault(family_key(c.label), []).append(i)

    out: list[tuple[str, str]] = []
    for cell, cv in zip(cells, cell_verdicts, strict=True):
        if cv.decision in (DECISION_INSUFFICIENT, DECISION_CONCENTRATE):
            out.append(
                (
                    cell.label,
                    map_verdict(
                        cv.decision,
                        powered_null=cv.powered_null,
                        n_days_ok=False,
                        dsr=None,
                        pbo=None,
                        stable=False,
                    ),
                )
            )
            continue

        supp = np.asarray(cell.supp_r, dtype=np.float64)
        _mintrl, n_days_ok = mintrl_n_ok(supp, cv.n_supp)

        family_idx = by_family[family_key(cell.label)]
        family_arrays = [
            np.asarray(cells[j].supp_r, dtype=np.float64) for j in family_idx
        ]
        out.append(
            (
                cell.label,
                map_verdict(
                    cv.decision,
                    powered_null=cv.powered_null,
                    n_days_ok=n_days_ok,
                    dsr=family_dsr(supp, family_arrays),
                    pbo=family_pbo(family_arrays),
                    stable=sign_agrees_early_late(list(cell.supp_r)),
                ),
            )
        )
    return out
