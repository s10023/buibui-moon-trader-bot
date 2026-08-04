"""H8 M1 indicator-state conditioning audit — pure, read-only.

Tags each backtest trade with the M1 indicator state as-of its entry and
emits a pre-committed BUILD/AVOID/NO-EDGE/INSUFFICIENT verdict per
(axis-state x direction). Mirrors tools/warning_value_audit.py.

audit_guard is SIGN-INVERTED: DISABLE == reliably positive (-> BUILD),
ENABLE == reliably negative (-> AVOID). Do not "fix" this to the intuitive map.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics import audit_guard
from analytics.backtest.engine import _compute_atr14
from analytics.brief.indicators import build_indicator_state
from analytics.brief.types import IndicatorState
from analytics.regime import classify_series
from analytics.research_guards import cscv_pbo, deflated_sharpe_ratio

# Minimum pre-entry bar count on each timeframe before we trust M1 state
# enough to tag a trade. This is a coarse floor (some sub-blocks, e.g.
# EmaState.stack, additionally need >= 200 1d bars internally and simply
# return None below that — the floor here just guards against computing
# indicators over near-empty slices).
_MIN_1D_BARS = 60
_MIN_1H_BARS = 60

_AXES: tuple[str, ...] = (
    "ema_stack",
    "ema_slope",
    "regime",
    "bb_squeeze",
    "bb_pctb",
    "vwap_weekly",
    "vwap_monthly",
    "vp_value_area",
    "pa_char",
    "monday_range",
)


@dataclass(frozen=True)
class IndicatorConditionConfig:
    bar: float = 0.05  # R economic bar (~2.5x round-trip cost)
    alpha: float = 0.05  # Holm family alpha
    min_n: int = 30  # per-cell floor
    n_boot: int = 2000  # bootstrap resamples
    block: int = 5  # block length for serial-correlation-aware boot (reserved)
    seed: int = 12345
    dsr_floor: float = 0.95
    pbo_ceil: float = 0.5


def _map_verdict(
    decision: str,
    *,
    n_supp: int,
    lift: float,
    lift_lo: float,
    lift_hi: float,
    dsr: float | None,
    pbo: float | None,
    cfg: IndicatorConditionConfig,
) -> str:
    """Map an audit_guard cell decision to an H8 verdict (INVERTED).

    audit_guard.evaluate_audit_cells's decisions are sign-inverted relative to
    the with-state slice's mean: DISABLE means the slice is reliably POSITIVE
    (-> BUILD, a lever to enter/boost in this state); ENABLE means the slice is
    reliably NEGATIVE (-> AVOID, a lever to suppress entries in this state).
    Do not "fix" this to the intuitive DISABLE->AVOID / ENABLE->BUILD map — it
    inverts every result (this bit ST9; see docs/superpowers/specs/
    2026-07-24-h8-m1-indicator-conditioning-design.md §2).

    **INSUFFICIENT is two different things.** ``audit_guard`` returns one
    ``INSUFFICIENT`` decision from two branches: ``n < min_n`` (genuinely
    underpowered, ``analytics/audit_guard.py``'s early ``continue``) and
    "powered, but the CI/Holm gate never cleared" (its bare ``else``).
    Collapsing them reports a tested-null cell as if it had never been tested.
    Split on ``n_supp``: only a cell below ``cfg.min_n`` is truly
    INSUFFICIENT; a powered null is NO-EDGE. Mirrors the same fix in
    ``analytics/venue_premium.py`` (H14).
    """
    if decision == "INSUFFICIENT":
        return "INSUFFICIENT" if n_supp < cfg.min_n else "NO-EDGE"
    family_ok = (
        dsr is not None
        and pbo is not None
        and dsr >= cfg.dsr_floor
        and pbo <= cfg.pbo_ceil
    )
    if decision == "DISABLE" and lift > 0 and lift_lo > 0 and family_ok:
        return "BUILD"
    if decision == "ENABLE" and lift < 0 and lift_hi < 0 and family_ok:
        return "AVOID"
    return "NO-EDGE"


def axis_states(
    state: IndicatorState, regime_label: str | None, ref_close: float
) -> dict[str, str | None]:
    """``IndicatorState`` -> ``{axis: state-enum|None}``.

    A missing sub-block -> ``None`` for that axis only (the trade is excluded
    from that axis's split, never dropped globally). ``ref_close`` is unused
    directly here (``ProfileState.vs_value`` already carries the
    above/inside/below classification computed from the SAME ``ref_close`` at
    tagging time) but is kept in the signature for parity with the design doc
    and in case a future axis needs it directly.
    """
    out: dict[str, str | None] = dict.fromkeys(_AXES)
    out["regime"] = regime_label
    if state.ema is not None:
        out["ema_stack"] = state.ema.stack
        out["ema_slope"] = state.ema.slope_200
    if state.bb is not None:
        out["bb_squeeze"] = "squeeze" if state.bb.squeeze else "no_squeeze"
        if state.bb.pct_b is not None:
            out["bb_pctb"] = (
                "low"
                if state.bb.pct_b < 0.2
                else "high"
                if state.bb.pct_b > 0.8
                else "mid"
            )
    if state.vwap is not None:
        if state.vwap.weekly_dist_atr is not None:
            out["vwap_weekly"] = "above" if state.vwap.weekly_dist_atr >= 0 else "below"
        if state.vwap.monthly_dist_atr is not None:
            out["vwap_monthly"] = (
                "above" if state.vwap.monthly_dist_atr >= 0 else "below"
            )
    if state.profile is not None:
        out["vp_value_area"] = state.profile.vs_value
    if state.pa is not None:
        out["pa_char"] = state.pa.label
    if state.monday is not None:
        out["monday_range"] = state.monday.state
    return out


def _axes_as_of(
    sym: str, t: int, market_by_pair: dict[tuple[str, str], pd.DataFrame]
) -> dict[str, str | None]:
    """Compute the M1 axis states for one ``(symbol, entry_time=t)``.

    **Causal core (load-bearing):** only bars with ``open_time <= t`` are
    visible — the entry bar itself is the last usable bar. Any bar with
    ``open_time > t`` MUST be excluded before calling
    ``build_indicator_state``; this is what
    ``test_tag_trades_is_causal_and_mutation_proof`` locks. A symbol with no
    1d/1h OHLCV, or a pre-entry slice too short for M1, yields all-None axes.
    The full causal history (not a trailing cap) is passed through, so an
    indicator's warmup is identical to running it over all bars up to ``t``.
    """
    d1 = market_by_pair.get((sym, "1d"))
    h1 = market_by_pair.get((sym, "1h"))
    axes: dict[str, str | None] = dict.fromkeys(_AXES)
    if d1 is None or h1 is None:
        return axes
    c1d = d1[d1["open_time"] <= t].reset_index(drop=True)
    c1h = h1[h1["open_time"] <= t].reset_index(drop=True)
    if len(c1d) < _MIN_1D_BARS or len(c1h) < _MIN_1H_BARS:
        return axes
    ref_close = float(c1d["close"].iloc[-1])
    atr14 = _compute_atr14(
        c1d["high"].to_numpy(dtype=float),
        c1d["low"].to_numpy(dtype=float),
        c1d["close"].to_numpy(dtype=float),
        len(c1d) - 1,
    )
    atr_last = float(atr14) if atr14 is not None else 0.0
    regime_series = classify_series(c1d, "1d")
    regime_label = str(regime_series.iloc[-1]) if len(regime_series) else None
    state, _notes = build_indicator_state(
        c1d, c1h, regime_series, ref_close, atr_last, as_of_ms=t
    )
    if state is None:
        return axes
    return axis_states(state, regime_label, ref_close)


def tag_trades(
    entries: pd.DataFrame, market_by_pair: dict[tuple[str, str], pd.DataFrame]
) -> pd.DataFrame:
    """Add one column per axis (state as-of entry) to ``entries``.

    ``entries`` must have ``symbol`` and ``entry_time`` columns.
    ``market_by_pair`` must carry ``(symbol, "1d")`` and ``(symbol, "1h")``
    OHLCV — M1 indicator state is always computed from 1d + 1h regardless of
    the trade's own timeframe (mirrors the brief panel).

    The M1 state depends only on ``(symbol, entry_time)`` (the causal slice),
    NOT on the trade's strategy/direction/tf, so the per-``(symbol, t)`` result
    is **memoized** — many strategies firing on the same candle share one
    compute. This is a pure dedup: byte-identical to computing every row
    independently (see ``_axes_as_of`` for the causal guard). Rows whose symbol
    has no 1d/1h OHLCV, or where the pre-entry slice is too short for M1, get
    all-None axes (excluded per-axis downstream, never dropped globally).
    """
    cache: dict[tuple[str, int], dict[str, str | None]] = {}
    rows: list[dict[str, object]] = []
    for _, tr in entries.iterrows():
        sym = str(tr["symbol"])
        t = int(tr["entry_time"])
        key = (sym, t)
        axes = cache.get(key)
        if axes is None:
            axes = _axes_as_of(sym, t, market_by_pair)
            cache[key] = axes
        row: dict[str, object] = {str(k): v for k, v in tr.to_dict().items()}
        row.update(axes)
        rows.append(row)
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class _RawCell:
    """One (axis, state, direction) split, pre-gate."""

    axis: str
    state: str
    direction: str
    with_r: npt.NDArray[np.float64]
    without_r: npt.NDArray[np.float64]


def build_condition_cells(
    tagged: pd.DataFrame, *, axes: Sequence[str]
) -> list[_RawCell]:
    """Split ``tagged`` trades into per-(axis-state x direction) cells.

    For a given axis, the "without" slice is same-direction trades whose
    value on that axis is present and differs from the tested state (design
    doc §7). Rows where the axis is ``None``/NaN are excluded from that
    axis's split entirely (never coerced into a state, never dropped from
    other axes' splits).
    """
    cells: list[_RawCell] = []
    if tagged.empty:
        return cells
    for axis in axes:
        if axis not in tagged.columns:
            continue
        sub_axis = tagged[tagged[axis].notna()]
        if sub_axis.empty:
            continue
        for direction, dgrp in sub_axis.groupby("direction", sort=True):
            states = sorted({str(s) for s in dgrp[axis]})
            for state in states:
                with_mask = dgrp[axis].astype(str) == state
                with_r = dgrp.loc[with_mask, "pnl_r"].to_numpy(dtype=np.float64)
                without_r = dgrp.loc[~with_mask, "pnl_r"].to_numpy(dtype=np.float64)
                cells.append(
                    _RawCell(
                        axis=axis,
                        state=state,
                        direction=str(direction),
                        with_r=with_r,
                        without_r=without_r,
                    )
                )
    return cells


@dataclass(frozen=True)
class ConditionVerdict:
    axis: str
    state: str
    direction: str
    verdict: str  # BUILD | AVOID | NO-EDGE | INSUFFICIENT
    n_with: int
    n_without: int
    avg_r_with: float
    avg_r_without: float
    lift: float
    lift_lo: float
    lift_hi: float
    dsr: float | None
    pbo: float | None


def _lift_ci(
    with_r: npt.NDArray[np.float64],
    without_r: npt.NDArray[np.float64],
    cfg: IndicatorConditionConfig,
) -> tuple[float, float, float]:
    """Seeded two-sample bootstrap CI on mean(with) - mean(without)."""
    if with_r.shape[0] < 2 or without_r.shape[0] < 2:
        lift = (
            float(with_r.mean() - without_r.mean())
            if with_r.size and without_r.size
            else float("nan")
        )
        return lift, float("nan"), float("nan")
    rng = np.random.default_rng(cfg.seed)
    diffs = np.empty(cfg.n_boot)
    for i in range(cfg.n_boot):
        a = rng.choice(with_r, size=len(with_r), replace=True)
        b = rng.choice(without_r, size=len(without_r), replace=True)
        diffs[i] = a.mean() - b.mean()
    lift = float(with_r.mean() - without_r.mean())
    lo, hi = np.quantile(diffs, [cfg.alpha / 2, 1 - cfg.alpha / 2])
    return lift, float(lo), float(hi)


def _cell_sharpe(arr: npt.NDArray[np.float64]) -> float:
    if arr.shape[0] < 2:
        return 0.0
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(arr)) / sd


# Family-DSR/PBO construction for the H8 axis-state family. Unlike a
# swept-parameter family (analytics/sl_horizon.py's k-grid, which re-scores
# the SAME signals under each arm -> a natural paired T x N matrix), H8's
# states are DISJOINT trade populations with no shared row index. This folds
# each state's own return sequence (row order, a time proxy) into
# _PBO_PERIODS equal chunks, giving a shared T axis cscv_pbo can split.
_PBO_PERIODS = 20
_PBO_SPLITS = 4


def _family_pbo(arrays: list[npt.NDArray[np.float64]]) -> float | None:
    """PBO across an (axis, direction) family's states. ``None`` when fewer
    than 2 states clear the period floor (mirrors sl_horizon.py's graceful
    "PBO skipped" branch — a cell can still be BUILD/AVOID-eligible on DSR
    alone only if DSR's own multiplicity check also degrades gracefully, but
    _map_verdict requires BOTH dsr and pbo non-None, so a family of size 1
    can never gate — by design, there is nothing to overfit to.
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
    """Deflated Sharpe of ``target_r`` against its (axis, direction) family.

    H8 families mix DISABLE-bound cells (positive Sharpe) with ENABLE-bound
    ones (negative Sharpe) — ``bullish`` and ``bearish`` share the
    ``(ema_stack, long)`` family. ``deflated_sharpe_ratio`` measures confidence
    that the TRUE Sharpe exceeds a POSITIVE expected-max-of-N benchmark, so a
    raw negative Sharpe deflates to ~0 however reliable the negative effect is.
    Since AVOID requires ``dsr >= cfg.dsr_floor``, the signed form made AVOID
    structurally near-unreachable: a reliably-negative cell measured 0.0000
    against 0.9980 for its mirror-image positive cell. Using the MAGNITUDE of
    every Sharpe asks the direction-agnostic question that actually applies —
    is this cell's *extremity*, whichever way it points, still credible after
    accounting for having tested N states. Mirrors ``analytics/venue_premium``.

    Disclosed consequence: folding to magnitude shrinks trial dispersion in a
    mixed-sign family, so this gate is marginally MORE permissive than the
    signed form. Bias runs toward more passes, never fewer.
    """
    trial_srs = [abs(_cell_sharpe(a)) for a in family_arrays if a.shape[0] >= 2]
    if not trial_srs:
        trial_srs = [abs(_cell_sharpe(target_r))]
    return deflated_sharpe_ratio(
        abs(_cell_sharpe(target_r)),
        max(int(target_r.shape[0]), 1),
        trial_srs=trial_srs,
    )


def evaluate_conditions(
    cells: list[_RawCell], cfg: IndicatorConditionConfig
) -> list[ConditionVerdict]:
    """The design doc §7 gate: one Holm family across every cell passed in,
    plus a per-(axis, direction) DSR/PBO family stamp and the inverted-verdict
    map (``_map_verdict``).
    """
    if not cells:
        return []
    ag_cells = [
        audit_guard.AuditCell(
            label=f"{c.axis}|{c.state}|{c.direction}",
            supp_r=c.with_r.tolist(),
            kept_r=c.without_r.tolist(),
        )
        for c in cells
    ]
    cell_verdicts = audit_guard.evaluate_audit_cells(
        ag_cells,
        bar=cfg.bar,
        alpha=cfg.alpha,
        min_n=cfg.min_n,
        n_boot=cfg.n_boot,
        seed=cfg.seed,
    )

    by_family: dict[tuple[str, str], list[int]] = {}
    for i, c in enumerate(cells):
        by_family.setdefault((c.axis, c.direction), []).append(i)

    out: list[ConditionVerdict] = []
    for c, cv in zip(cells, cell_verdicts, strict=True):
        n_with, n_without = c.with_r.shape[0], c.without_r.shape[0]
        avg_with = float(c.with_r.mean()) if n_with else float("nan")
        avg_without = float(c.without_r.mean()) if n_without else float("nan")
        # Only a genuinely UNDERPOWERED cell short-circuits. A powered cell
        # whose CI/Holm gate merely failed to clear is a tested null: it falls
        # through so its real lift, CI and family stats are computed, and
        # _map_verdict resolves it to NO-EDGE. Short-circuiting both (the
        # pre-fix behaviour) published a fabricated lift of exactly 0.0 with a
        # [0.0, 0.0] CI for cells that do have a measured lift, and made them
        # indistinguishable from cells that were never tested at all. The
        # n_with < 2 arm mirrors audit_guard's own eligibility test, which
        # needs 2 points before it can compute a Sharpe.
        if cv.decision == "INSUFFICIENT" and (n_with < cfg.min_n or n_with < 2):
            out.append(
                ConditionVerdict(
                    axis=c.axis,
                    state=c.state,
                    direction=c.direction,
                    verdict="INSUFFICIENT",
                    n_with=n_with,
                    n_without=n_without,
                    avg_r_with=avg_with,
                    avg_r_without=avg_without,
                    lift=float("nan"),
                    lift_lo=float("nan"),
                    lift_hi=float("nan"),
                    dsr=None,
                    pbo=None,
                )
            )
            continue
        lift, lift_lo, lift_hi = _lift_ci(c.with_r, c.without_r, cfg)
        family_idx = by_family[(c.axis, c.direction)]
        family_arrays = [cells[j].with_r for j in family_idx]
        dsr = _family_dsr(c.with_r, family_arrays)
        pbo = _family_pbo(family_arrays)
        verdict = _map_verdict(
            cv.decision,
            n_supp=n_with,
            lift=lift,
            lift_lo=lift_lo,
            lift_hi=lift_hi,
            dsr=dsr,
            pbo=pbo,
            cfg=cfg,
        )
        out.append(
            ConditionVerdict(
                axis=c.axis,
                state=c.state,
                direction=c.direction,
                verdict=verdict,
                n_with=n_with,
                n_without=n_without,
                avg_r_with=avg_with,
                avg_r_without=avg_without,
                lift=lift,
                lift_lo=lift_lo,
                lift_hi=lift_hi,
                dsr=dsr,
                pbo=pbo,
            )
        )
    return out
