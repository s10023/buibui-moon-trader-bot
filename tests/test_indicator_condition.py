"""Tests for analytics/indicator_condition.py (H8 M1 indicator-conditioning audit)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.brief.types import (
    BbState,
    EmaState,
    IndicatorState,
    MondayState,
    PaState,
)
from analytics.indicator_condition import (
    _AXES,
    IndicatorConditionConfig,
    _map_verdict,
    axis_states,
    tag_trades,
)

CFG = IndicatorConditionConfig()


def test_map_disable_positive_lift_is_build() -> None:
    # audit_guard DISABLE == with-state slice reliably POSITIVE -> BUILD.
    assert (
        _map_verdict(
            "DISABLE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.97, pbo=0.2, cfg=CFG
        )
        == "BUILD"
    )


def test_map_enable_negative_lift_is_avoid() -> None:
    assert (
        _map_verdict(
            "ENABLE",
            lift=-0.20,
            lift_lo=-0.35,
            lift_hi=-0.05,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        == "AVOID"
    )


def test_map_disable_is_never_avoid() -> None:
    # Guardrail: the intuitive-but-wrong DISABLE->AVOID map must be impossible.
    assert (
        _map_verdict(
            "DISABLE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.97, pbo=0.2, cfg=CFG
        )
        != "AVOID"
    )


def test_map_family_fail_is_no_edge() -> None:
    # DSR/PBO family gate not cleared -> NO-EDGE even with a clean lift.
    assert (
        _map_verdict(
            "DISABLE", lift=0.20, lift_lo=0.05, lift_hi=0.35, dsr=0.80, pbo=0.2, cfg=CFG
        )
        == "NO-EDGE"
    )


def test_map_concentrate_is_no_edge() -> None:
    assert (
        _map_verdict(
            "CONCENTRATE",
            lift=0.20,
            lift_lo=0.05,
            lift_hi=0.35,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        == "NO-EDGE"
    )


def test_map_insufficient_passthrough() -> None:
    assert (
        _map_verdict(
            "INSUFFICIENT",
            lift=0.0,
            lift_lo=0.0,
            lift_hi=0.0,
            dsr=None,
            pbo=None,
            cfg=CFG,
        )
        == "INSUFFICIENT"
    )


# --------------------------------------------------------------------------- #
# Task 2: axis_states                                                          #
# --------------------------------------------------------------------------- #


def _state(**kw: object) -> IndicatorState:
    base: dict[str, object] = {
        "ema": None,
        "range_state": None,
        "monday": None,
        "candles": None,
        "pa": None,
        "bb": None,
        "vwap": None,
        "profile": None,
    }
    base.update(kw)
    return IndicatorState(**base)  # type: ignore[arg-type]


def test_axis_states_reads_ema_bb_pa_monday() -> None:
    st = _state(
        ema=EmaState(
            above_20=True,
            above_50=True,
            above_200=False,
            stack="bullish",
            slope_200="rising",
        ),
        bb=BbState(pct_b=0.05, bandwidth=0.02, bw_pctile=0.1, squeeze=True),
        pa=PaState(label="grind_up", er=0.5, speed_atr=0.4),
        monday=MondayState(state="inside", pos=0.4),
    )
    ax = axis_states(st, regime_label="trend", ref_close=100.0)
    assert ax["ema_stack"] == "bullish"
    assert ax["ema_slope"] == "rising"
    assert ax["bb_squeeze"] == "squeeze"
    assert ax["bb_pctb"] == "low"  # 0.05 < 0.2
    assert ax["pa_char"] == "grind_up"
    assert ax["monday_range"] == "inside"
    assert ax["regime"] == "trend"


def test_axis_states_missing_subblocks_are_none() -> None:
    ax = axis_states(_state(), regime_label=None, ref_close=100.0)
    assert set(ax) == set(_AXES)
    assert all(v is None for v in ax.values())


# --------------------------------------------------------------------------- #
# Task 3: tag_trades — as-of-entry causal tagging + look-ahead mutation guard  #
# --------------------------------------------------------------------------- #

_DAY = 86_400_000


def _synth_ohlcv(n: int, start: int, tf_ms: int, closes: list[float]) -> pd.DataFrame:
    ot = [start + i * tf_ms for i in range(n)]
    c = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "open_time": ot,
            "open": c,
            "high": c * 1.01,
            "low": c * 0.99,
            "close": c,
            "volume": np.full(n, 1000.0),
        }
    )


def test_tag_trades_is_causal_and_mutation_proof() -> None:
    """A trade at t = the entry bar's open_time. Bars after t must not
    change its state.

    Deviation from the plan's illustrative version: the plan's snippet used
    only 40 1d bars, which leaves EmaState.stack == None both before and
    after the mutation (e50/e200 both need len(close) >= their span), so the
    assertion passes vacuously without exercising the causal guard at all.
    This version uses FLAT closes (all bars == 300.0) over 220 1d bars so
    e20 == e50 == e200 exactly for every row (stack == "mixed", a
    deterministic, non-accidental baseline), then mutates the SINGLE bar
    immediately after entry (k+1) to a wild value. If that bar ever leaked
    into the as-of-entry slice, EWM math *guarantees* a flip to "bullish"
    (a fresh shock added to identical prior EMA values always lands the
    faster-span EMA above the slower ones: e20 > e50 > e200), so the mutation
    can only fail to move the result if the guard is genuinely causal — no
    vacuous pass is possible here.
    """
    start = 1_700_000_000_000
    n_d1 = 220
    closes_d1 = [300.0] * n_d1
    d1 = _synth_ohlcv(n_d1, start, _DAY, closes_d1)
    h1_tf_ms = _DAY // 24
    n_h1 = n_d1 * 24
    h1 = _synth_ohlcv(n_h1, start, h1_tf_ms, [300.0] * n_h1)

    k = 210  # >= 200 bars of pre-entry history for EMA200 to resolve
    t = int(d1["open_time"].iloc[k])
    entries = pd.DataFrame(
        [
            {
                "symbol": "TST",
                "tf": "1d",
                "strategy": "s",
                "direction": "long",
                "entry_time": t,
                "pnl_r": 1.0,
            }
        ]
    )
    market = {("TST", "1d"): d1, ("TST", "1h"): h1}

    tagged = tag_trades(entries, market)
    base = tagged.iloc[0]["ema_stack"]
    assert base == "mixed"  # flat closes -> e20 == e50 == e200 exactly

    # Mutate the bar immediately AFTER entry (k+1) to a wild value.
    d1_future = d1.copy()
    future_idx = k + 1
    d1_future.loc[future_idx, ["close", "high", "low", "open"]] = 1_000_000.0

    tagged2 = tag_trades(entries, {("TST", "1d"): d1_future, ("TST", "1h"): h1})
    assert tagged2.iloc[0]["ema_stack"] == base  # causal: future bar is invisible
