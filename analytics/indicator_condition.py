"""H8 M1 indicator-state conditioning audit — pure, read-only.

Tags each backtest trade with the M1 indicator state as-of its entry and
emits a pre-committed BUILD/AVOID/NO-EDGE/INSUFFICIENT verdict per
(axis-state x direction). Mirrors tools/warning_value_audit.py.

audit_guard is SIGN-INVERTED: DISABLE == reliably positive (-> BUILD),
ENABLE == reliably negative (-> AVOID). Do not "fix" this to the intuitive map.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from analytics.backtest.engine import _compute_atr14
from analytics.brief.indicators import build_indicator_state
from analytics.brief.types import IndicatorState
from analytics.regime import classify_series

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


def tag_trades(
    entries: pd.DataFrame, market_by_pair: dict[tuple[str, str], pd.DataFrame]
) -> pd.DataFrame:
    """Add one column per axis (state as-of entry) to ``entries``.

    ``entries`` must have ``symbol`` and ``entry_time`` columns.
    ``market_by_pair`` must carry ``(symbol, "1d")`` and ``(symbol, "1h")``
    OHLCV — M1 indicator state is always computed from 1d + 1h regardless of
    the trade's own timeframe (mirrors the brief panel).

    **Causal core (load-bearing):** for a trade at ``entry_time = t``, only
    bars with ``open_time <= t`` are visible — the entry bar itself is the
    last usable bar. Any bar with ``open_time > t`` MUST be excluded before
    calling ``build_indicator_state``; this is what
    ``test_tag_trades_is_causal_and_mutation_proof`` locks. Rows whose
    (symbol) has no 1d/1h OHLCV, or where the pre-entry slice is too short
    for M1, get all-None axes (excluded per-axis downstream, never dropped
    globally).
    """
    rows: list[dict[str, object]] = []
    for _, tr in entries.iterrows():
        sym = str(tr["symbol"])
        t = int(tr["entry_time"])
        d1 = market_by_pair.get((sym, "1d"))
        h1 = market_by_pair.get((sym, "1h"))
        axes: dict[str, str | None] = dict.fromkeys(_AXES)
        if d1 is not None and h1 is not None:
            c1d = d1[d1["open_time"] <= t].reset_index(drop=True)
            c1h = h1[h1["open_time"] <= t].reset_index(drop=True)
            if len(c1d) >= _MIN_1D_BARS and len(c1h) >= _MIN_1H_BARS:
                ref_close = float(c1d["close"].iloc[-1])
                atr14 = _compute_atr14(
                    c1d["high"].to_numpy(dtype=float),
                    c1d["low"].to_numpy(dtype=float),
                    c1d["close"].to_numpy(dtype=float),
                    len(c1d) - 1,
                )
                atr_last = float(atr14) if atr14 is not None else 0.0
                regime_series = classify_series(c1d, "1d")
                regime_label = (
                    str(regime_series.iloc[-1]) if len(regime_series) else None
                )
                state, _notes = build_indicator_state(
                    c1d, c1h, regime_series, ref_close, atr_last, as_of_ms=t
                )
                if state is not None:
                    axes = axis_states(state, regime_label, ref_close)
        rows.append({**tr.to_dict(), **axes})
    return pd.DataFrame(rows)
