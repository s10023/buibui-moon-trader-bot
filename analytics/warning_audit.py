"""H9 warning-value audit lib (pure — no DB/IO).

The live alert path computes six candle-anatomy warnings at fire time
(``signals.alert_formatter._build_candle_warnings``) but never persists the
flags, so this module re-derives them historically from OHLCV using the SAME
helpers — import, never reimplement — then evaluates whether any warning
predicts avg_r via :mod:`analytics.audit_guard` (block-bootstrap CI clearing
±bar + Holm haircut, one family per audited source).

Scope guard: the volume-spike / low-volume conviction notes are excluded
(already audited via ``tools/gate_audit.py``); CME-gap warnings are out of
scope. See ``docs/superpowers/plans/2026-07-17-h9-warning-value-audit.md``.
"""

from __future__ import annotations

import pandas as pd

from signals.alert_formatter import (
    _has_consecutive_candles,
    _has_equal_levels,
    _is_doji,
    _is_inside_bar,
    _is_marubozu,
    _wick_rejection_against,
)

WARNING_KEYS: tuple[str, ...] = (
    "w1_marubozu",
    "w2_equal_levels",
    "w5_wick_rejection",
    "w6_consecutive",
    "w7_doji",
    "w8_inside_bar",
)

VERDICT_SUPPRESS = "SUPPRESS-CANDIDATE"
VERDICT_REVERSE = "REVERSE"
VERDICT_COSMETIC = "COSMETIC"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


def compute_warning_flags(
    window: pd.DataFrame,
    direction: str,
    price: float | None = None,
) -> dict[str, bool] | None:
    """Re-derive the six warning flags for the window's LAST candle.

    ``window`` holds OHLCV rows ending AT the signal candle (last row = signal
    candle). Returns ``None`` when the window has < 2 rows — the live path
    emits no candle warnings there either. ``price`` (for W2's equal-levels
    scan) defaults to the signal-candle close, the exact value the live path
    passes (``SignalEvent.price``). Precedence mirrors
    ``_build_candle_warnings``: doji wins over marubozu; wick-rejection is
    skipped on a doji.
    """
    if len(window) < 2:
        return None
    last = window.iloc[-1]
    o = float(last["open"])
    h = float(last["high"])
    lo = float(last["low"])
    c = float(last["close"])
    prev = window.iloc[-2]
    prev_h = float(prev["high"])
    prev_l = float(prev["low"])
    p = c if price is None else price
    doji = _is_doji(o, h, lo, c)
    return {
        "w1_marubozu": (not doji) and _is_marubozu(o, h, lo, c),
        "w2_equal_levels": _has_equal_levels(window, p, direction),
        "w5_wick_rejection": (not doji)
        and _wick_rejection_against(o, h, lo, c, direction),
        "w6_consecutive": _has_consecutive_candles(window, direction),
        "w7_doji": doji,
        "w8_inside_bar": _is_inside_bar(h, lo, prev_h, prev_l),
    }
