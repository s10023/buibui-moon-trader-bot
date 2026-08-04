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
``analytics.state_audit.map_verdict`` for the sign-inversion and
INSUFFICIENT-split reasoning.

H15 extraction: the day collapse, cell construction, per-family DSR/PBO,
stability check, verdict map and the pre-committed gate constants now live
in ``analytics/state_audit.py`` so H14, H15 and later state-tag audits share
ONE implementation. This module keeps H14's own vocabulary (the premium
series, the level/change state labels, and their family-key grouping) and
re-exports the shared names below so existing importers keep working
unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.audit_guard import AuditCell
from analytics.state_audit import (
    ALPHA,
    BAR,
    DAY_MS,
    DSR_FLOOR,
    MIN_N,
    MINTRL_CONFIDENCE,
    PBO_CEIL,
    VERDICT_AVOID,
    VERDICT_BUILD,
    VERDICT_INSUFFICIENT,
    VERDICT_NO_EDGE,
    Z_WINDOW,
    build_state_cells,
    causal_zscore,
    cell_sharpe,
    collapse_to_daily,
    evaluate_states,
    family_dsr,
    family_pbo,
    map_verdict,
    sign_agrees_early_late,
)

__all__ = [
    "ALPHA",
    "BAR",
    "CHANGE_FALLING",
    "CHANGE_RISING",
    "CHANGE_SPAN",
    "DAY_MS",
    "DSR_FLOOR",
    "LEVEL_DEPRESSED",
    "LEVEL_ELEVATED",
    "LEVEL_NEUTRAL",
    "MIN_N",
    "MINTRL_CONFIDENCE",
    "PBO_CEIL",
    "VERDICT_AVOID",
    "VERDICT_BUILD",
    "VERDICT_INSUFFICIENT",
    "VERDICT_NO_EDGE",
    "Z_THRESHOLD",
    "Z_WINDOW",
    "build_premium_series",
    "build_state_cells",
    "causal_zscore",
    "cell_sharpe",
    "collapse_to_daily",
    "evaluate_premium_states",
    "evaluate_states",
    "family_dsr",
    "family_pbo",
    "label_changes",
    "label_levels",
    "map_verdict",
    "sign_agrees_early_late",
]

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

# H14's two DSR/PBO sub-families — H14's counterpart to H8's per-indicator
# axes (ema_stack, regime, ... in analytics/indicator_condition.py).
_LEVEL_STATES = frozenset({LEVEL_ELEVATED, LEVEL_NEUTRAL, LEVEL_DEPRESSED})
_CHANGE_STATES = frozenset({CHANGE_RISING, CHANGE_FALLING})


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


def evaluate_premium_states(cells: list[AuditCell]) -> list[tuple[str, str]]:
    """Pre-committed verdict per cell (spec Sec.7).

    H14's family is level(3) x direction(2) + change(2) x direction(2) = 10 cells
    on ``prem_adj``. Thin wrapper over the shared ``evaluate_states`` — the gate
    itself lives in ``analytics/state_audit.py`` so H14, H15 and later state tags
    cannot drift apart.
    """
    return evaluate_states(cells, _cell_family_key)
