"""H8 M1 indicator-state conditioning audit — pure, read-only.

Tags each backtest trade with the M1 indicator state as-of its entry and
emits a pre-committed BUILD/AVOID/NO-EDGE/INSUFFICIENT verdict per
(axis-state x direction). Mirrors tools/warning_value_audit.py.

audit_guard is SIGN-INVERTED: DISABLE == reliably positive (-> BUILD),
ENABLE == reliably negative (-> AVOID). Do not "fix" this to the intuitive map.
"""

from __future__ import annotations

from dataclasses import dataclass

from analytics.brief.types import IndicatorState

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
    """
    if decision == "INSUFFICIENT":
        return "INSUFFICIENT"
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
