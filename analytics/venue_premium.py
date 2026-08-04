"""H14 Coinbase-premium market-state tag — pure library.

Spec: docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md

PURE: no DB, no network, no file IO. Every threshold here is a-priori and was
written down in the spec before any avg_r was computed.

Task 3 (series/labels): the three premium series, the causal z-score, and
the two a-priori state-label axes (level, change).

Task 4 (this addition): the day-level collapse with the one-day entry lag
(spec Sec.5 / amendments.md A2), cell construction, and the pre-committed
BUILD/AVOID/NO-EDGE/INSUFFICIENT gate (spec Sec.7 / amendments.md A1/A3).
Mirrors ``analytics/indicator_condition.py`` (H8) in shape — see
``_map_verdict`` for the sign-inversion and INSUFFICIENT-split reasoning.
"""

from __future__ import annotations

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
Z_THRESHOLD = 1.0
CHANGE_SPAN = 5

LEVEL_ELEVATED = "elevated"
LEVEL_NEUTRAL = "neutral"
LEVEL_DEPRESSED = "depressed"
CHANGE_RISING = "rising"
CHANGE_FALLING = "falling"


def build_premium_series(
    cb_btc: pd.Series, bn_btc: pd.Series, cb_usdt: pd.Series
) -> pd.DataFrame:
    """The three pre-registered series, aligned on their shared index.

    ``prem_adj`` divides out the USDT peg, which the 2026-08-04 probe measured
    as accounting for essentially the whole raw premium at that instant.
    """
    idx = cb_btc.index.intersection(bn_btc.index)
    cb, bn = cb_btc.reindex(idx), bn_btc.reindex(idx)
    usdt = cb_usdt.reindex(idx)
    return pd.DataFrame(
        {
            "prem_raw": cb / bn - 1.0,
            "prem_adj": cb / (bn * usdt) - 1.0,
            "peg_dev": usdt - 1.0,
        },
        index=idx,
    )


def causal_zscore(series: pd.Series, window: int = Z_WINDOW) -> pd.Series:
    """z of today's value against the ``window`` days STRICTLY before it.

    The ``.shift(1)`` is the causality guarantee and is asserted by a
    perturbation test — do not remove it as a "warm-up" convenience.
    """
    prior = series.shift(1)
    mean = prior.rolling(window, min_periods=window).mean()
    std = prior.rolling(window, min_periods=window).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def label_levels(z: pd.Series) -> pd.Series:
    """Pre-registered +/-1.0 thresholds. NaN (warm-up) stays NaN, never 'neutral'."""
    out: pd.Series = pd.Series(
        np.where(z >= Z_THRESHOLD, LEVEL_ELEVATED, LEVEL_NEUTRAL), index=z.index
    )
    out = out.where(z > -Z_THRESHOLD, LEVEL_DEPRESSED)
    return out.where(z.notna(), other=np.nan)


def label_changes(series: pd.Series, span: int = CHANGE_SPAN) -> pd.Series:
    """Sign of the ``span``-day change in the SMOOTHED premium.

    Spec Sec.5 says "sign of the 5-day change in the smoothed premium" but never
    defines the smoothing. Per amendments.md A4 (controller ruling): a
    ``span``-length rolling mean applied before the diff — deterministic,
    a-priori, and adds no free parameter beyond the already-registered
    ``CHANGE_SPAN``. NaN warm-up preserved (both the rolling mean's own
    warm-up and the subsequent diff's).
    """
    smoothed = series.rolling(span, min_periods=span).mean()
    delta = smoothed.diff(span)
    out: pd.Series = pd.Series(
        np.where(delta >= 0.0, CHANGE_RISING, CHANGE_FALLING), index=series.index
    )
    return out.where(delta.notna(), other=np.nan)


# --------------------------------------------------------------------------- #
# Task 4: per-day collapse, cell construction, and the pre-committed verdict.
# Spec Sec.5-7; amendments.md A1-A3 override the plan's original sketch —
# see each helper's docstring for exactly what changed and why.
# --------------------------------------------------------------------------- #

_DAY_MS = 86_400_000

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

# H14's two DSR/PBO sub-families — H14's counterpart to H8's per-indicator
# axes (ema_stack, regime, ... in analytics/indicator_condition.py).
_LEVEL_STATES = frozenset({LEVEL_ELEVATED, LEVEL_NEUTRAL, LEVEL_DEPRESSED})
_CHANGE_STATES = frozenset({CHANGE_RISING, CHANGE_FALLING})

_PBO_PERIODS = 20
_PBO_SPLITS = 4


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
    df["day"] = (df["entry_time"] // _DAY_MS).astype("int64")
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
            inside = side.loc[side["state"] == state, "mean_r"]
            outside = side.loc[side["state"] != state, "mean_r"]
            cells.append(
                AuditCell(
                    label=f"{state}|{direction}",
                    supp_r=list(inside),
                    kept_r=list(outside),
                )
            )
    return cells


def _cell_family_key(label: str) -> tuple[str, str]:
    """``"state|direction"`` -> ``(axis, direction)`` for the DSR/PBO
    sub-family — H14's counterpart to H8's per-indicator axis grouping.
    """
    state, direction = label.rsplit("|", 1)
    if state in _LEVEL_STATES:
        axis = "level"
    elif state in _CHANGE_STATES:
        axis = "change"
    else:
        raise ValueError(f"unrecognized state token {state!r} in label {label!r}")
    return axis, direction


def _cell_sharpe(arr: npt.NDArray[np.float64]) -> float:
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


def _family_pbo(arrays: list[npt.NDArray[np.float64]]) -> float | None:
    """PBO across an (axis, direction) family's states.

    H14's states are DISJOINT day-populations with no shared row index
    (unlike a swept-parameter family with a natural paired T x N matrix), so
    — mirroring H8 — each state's own day-ordered return sequence is folded
    into ``_PBO_PERIODS`` equal chunks to give ``cscv_pbo`` a shared T axis.
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


def _family_dsr(
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
    trial_srs = [abs(_cell_sharpe(a)) for a in family_arrays if a.shape[0] >= 2]
    if not trial_srs:
        trial_srs = [abs(_cell_sharpe(target_r))]
    return deflated_sharpe_ratio(
        abs(_cell_sharpe(target_r)),
        max(int(target_r.shape[0]), 1),
        trial_srs=trial_srs,
    )


def _sign_agrees_early_late(values: list[float]) -> bool:
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


def _map_verdict(
    decision: str,
    *,
    n_supp: int,
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
    min_n" (genuinely underpowered) and "powered but the CI/Holm gate never
    cleared" (``analytics/audit_guard.py``'s bare ``else`` branch).
    Collapsing them makes the spec's most likely outcome — all cells
    NO-EDGE (spec Sec.8 branch 2) — unreachable. Split on ``n_supp``: only a
    cell with fewer than ``MIN_N`` day-observations is truly INSUFFICIENT; a
    powered null cell is NO-EDGE.

    **The full pre-committed gate (amendments.md A3 / spec Sec.7).** A
    BUILD/AVOID additionally requires ``n_days_ok`` (n >= MinTRL(0.95)),
    family DSR >= 0.95 AND PBO <= 0.5 (both non-``None``), and ``stable``
    (early/late sign agreement). Anything failing any one of these is
    NO-EDGE — never silently promoted to BUILD/AVOID.
    """
    if decision == DECISION_INSUFFICIENT:
        return VERDICT_INSUFFICIENT if n_supp < MIN_N else VERDICT_NO_EDGE
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


def evaluate_premium_states(cells: list[AuditCell]) -> list[tuple[str, str]]:
    """Pre-committed verdict per cell (spec Sec.7).

    All ``cells`` passed in share ONE ``audit_guard`` Holm-haircut family —
    spec Sec.5's full primary family is level(3) x direction(2) + change(2)
    x direction(2) = 10 cells on ``prem_adj``, built by calling
    ``build_state_cells`` once per axis and concatenating before this call.
    DSR/PBO are instead computed per (axis, direction) SUB-family
    (``_cell_family_key``), mirroring H8's per-indicator-axis family.
    """
    if not cells:
        return []
    cell_verdicts = evaluate_audit_cells(cells, bar=BAR, alpha=ALPHA, min_n=MIN_N)

    by_family: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cells):
        by_family.setdefault(_cell_family_key(c.label), []).append(i)

    out: list[tuple[str, str]] = []
    for cell, cv in zip(cells, cell_verdicts, strict=True):
        if cv.decision in (DECISION_INSUFFICIENT, DECISION_CONCENTRATE):
            out.append(
                (
                    cell.label,
                    _map_verdict(
                        cv.decision,
                        n_supp=cv.n_supp,
                        n_days_ok=False,
                        dsr=None,
                        pbo=None,
                        stable=False,
                    ),
                )
            )
            continue

        supp = np.asarray(cell.supp_r, dtype=np.float64)
        sharpe = _cell_sharpe(supp)
        # ENABLE cells have a negative mean/Sharpe by construction. MinTRL
        # (target_sr=0.0) returns inf whenever sr <= target_sr, which would
        # make a reliably-BAD cell always fail this check regardless of n. What
        # matters is confidence the effect is reliably nonzero, not
        # specifically positive, so gate on the MAGNITUDE — with skew=0.0
        # (the default) the variance term is symmetric in sr, so this is
        # exactly the mirror-image MinTRL for a negative benchmark of 0.
        mintrl = min_track_record_length(
            abs(sharpe), target_sr=0.0, confidence=MINTRL_CONFIDENCE
        )
        n_days_ok = float(cv.n_supp) >= mintrl

        family_idx = by_family[_cell_family_key(cell.label)]
        family_arrays = [
            np.asarray(cells[j].supp_r, dtype=np.float64) for j in family_idx
        ]
        dsr = _family_dsr(supp, family_arrays)
        pbo = _family_pbo(family_arrays)
        stable = _sign_agrees_early_late(list(cell.supp_r))

        verdict = _map_verdict(
            cv.decision,
            n_supp=cv.n_supp,
            n_days_ok=n_days_ok,
            dsr=dsr,
            pbo=pbo,
            stable=stable,
        )
        out.append((cell.label, verdict))
    return out
