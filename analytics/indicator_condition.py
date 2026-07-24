"""H8 M1 indicator-state conditioning audit — pure, read-only.

Tags each backtest trade with the M1 indicator state as-of its entry and
emits a pre-committed BUILD/AVOID/NO-EDGE/INSUFFICIENT verdict per
(axis-state x direction). Mirrors tools/warning_value_audit.py.

audit_guard is SIGN-INVERTED: DISABLE == reliably positive (-> BUILD),
ENABLE == reliably negative (-> AVOID). Do not "fix" this to the intuitive map.
"""

from __future__ import annotations

from dataclasses import dataclass


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
