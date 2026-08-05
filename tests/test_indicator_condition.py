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
    build_condition_cells,
    evaluate_conditions,
    tag_trades,
)

CFG = IndicatorConditionConfig()


def test_map_disable_positive_lift_is_build() -> None:
    # audit_guard DISABLE == with-state slice reliably POSITIVE -> BUILD.
    assert (
        _map_verdict(
            "DISABLE",
            n_supp=120,
            n_ok=True,
            lift=0.20,
            lift_lo=0.05,
            lift_hi=0.35,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        == "BUILD"
    )


def test_map_enable_negative_lift_is_avoid() -> None:
    assert (
        _map_verdict(
            "ENABLE",
            n_supp=120,
            n_ok=True,
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
            "DISABLE",
            n_supp=120,
            n_ok=True,
            lift=0.20,
            lift_lo=0.05,
            lift_hi=0.35,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        != "AVOID"
    )


def test_map_family_fail_is_no_edge() -> None:
    # DSR/PBO family gate not cleared -> NO-EDGE even with a clean lift.
    assert (
        _map_verdict(
            "DISABLE",
            n_supp=120,
            n_ok=True,
            lift=0.20,
            lift_lo=0.05,
            lift_hi=0.35,
            dsr=0.80,
            pbo=0.2,
            cfg=CFG,
        )
        == "NO-EDGE"
    )


def test_map_concentrate_is_no_edge() -> None:
    assert (
        _map_verdict(
            "CONCENTRATE",
            n_supp=120,
            n_ok=True,
            lift=0.20,
            lift_lo=0.05,
            lift_hi=0.35,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        == "NO-EDGE"
    )


def test_map_insufficient_underpowered_stays_insufficient() -> None:
    # n below the per-cell floor -> genuinely not enough data.
    assert (
        _map_verdict(
            "INSUFFICIENT",
            n_supp=CFG.min_n - 1,
            n_ok=False,
            lift=0.0,
            lift_lo=0.0,
            lift_hi=0.0,
            dsr=None,
            pbo=None,
            cfg=CFG,
        )
        == "INSUFFICIENT"
    )


def test_map_insufficient_but_powered_is_no_edge() -> None:
    # audit_guard returns ONE INSUFFICIENT for two different situations:
    # "n < min_n" and "powered, but the CI/Holm gate never cleared". Collapsing
    # them makes an all-NO-EDGE outcome structurally unreachable and reports a
    # tested-null cell as if it had never been tested.
    assert (
        _map_verdict(
            "INSUFFICIENT",
            n_supp=CFG.min_n * 4,
            n_ok=True,
            lift=0.01,
            lift_lo=-0.20,
            lift_hi=0.22,
            dsr=None,
            pbo=None,
            cfg=CFG,
        )
        == "NO-EDGE"
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


# --------------------------------------------------------------------------- #
# Task 4: build_condition_cells + evaluate_conditions (the two-leg gate)      #
# --------------------------------------------------------------------------- #

_OTHER_AXES = (
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


def test_evaluate_builds_on_strong_positive_state() -> None:
    rng = np.random.default_rng(0)
    n = 400
    # 'bullish' EMA trades average +0.5R, others average -0.1R (both low-noise).
    with_r = rng.normal(0.5, 0.3, n)
    without_r = rng.normal(-0.1, 0.3, n)
    df = pd.DataFrame(
        {
            "direction": ["long"] * (2 * n),
            "strategy": ["s"] * (2 * n),
            "ema_stack": (["bullish"] * n) + (["bearish"] * n),
            "pnl_r": list(with_r) + list(without_r),
            **{a: ["x"] * (2 * n) for a in _OTHER_AXES},
        }
    )
    cells = build_condition_cells(df, axes=("ema_stack",))
    verdicts = evaluate_conditions(cells, IndicatorConditionConfig())
    bull = [
        v
        for v in verdicts
        if v.axis == "ema_stack" and v.state == "bullish" and v.direction == "long"
    ]
    assert bull and bull[0].verdict == "BUILD"
    assert bull[0].lift > 0.4


def test_evaluate_powered_null_is_no_edge_with_a_real_lift() -> None:
    """A powered cell that simply shows no effect must read NO-EDGE, and must
    carry its ACTUAL measured lift and family statistics.

    Before the fix, ``evaluate_conditions`` short-circuited every INSUFFICIENT
    decision -- including powered ones -- and emitted a fabricated
    ``lift = 0.0`` with ``lift_ci = [0.0, 0.0]`` and ``dsr = pbo = None``. So
    the published table showed a zero lift these cells do not have, and was
    indistinguishable from a cell that was never tested.
    """
    rng = np.random.default_rng(7)
    n = 300
    df = pd.DataFrame(
        {
            "direction": ["long"] * (2 * n),
            "strategy": ["s"] * (2 * n),
            # Both slices drawn from the SAME null distribution -> powered, but
            # no effect to find.
            "ema_stack": (["bullish"] * n) + (["bearish"] * n),
            "pnl_r": list(rng.normal(0.0, 1.0, n)) + list(rng.normal(0.0, 1.0, n)),
            **{a: ["x"] * (2 * n) for a in _OTHER_AXES},
        }
    )
    cells = build_condition_cells(df, axes=("ema_stack",))
    verdicts = evaluate_conditions(cells, CFG)
    bull = next(v for v in verdicts if v.state == "bullish" and v.direction == "long")
    assert bull.n_with >= CFG.min_n  # genuinely powered
    assert bull.verdict == "NO-EDGE"  # tested, no effect -- NOT "INSUFFICIENT"
    assert bull.lift_lo < bull.lift < bull.lift_hi  # a real CI, not [0, 0]
    assert bull.dsr is not None and bull.pbo is not None


def test_evaluate_underpowered_stays_insufficient() -> None:
    """The other half of the split must not regress: a cell below ``min_n``
    is still INSUFFICIENT, and still carries no fabricated statistics."""
    rng = np.random.default_rng(11)
    n_small, n_big = 5, 300
    df = pd.DataFrame(
        {
            "direction": ["long"] * (n_small + n_big),
            "strategy": ["s"] * (n_small + n_big),
            "ema_stack": (["bullish"] * n_small) + (["bearish"] * n_big),
            "pnl_r": list(rng.normal(0.0, 1.0, n_small))
            + list(rng.normal(0.0, 1.0, n_big)),
            **{a: ["x"] * (n_small + n_big) for a in _OTHER_AXES},
        }
    )
    cells = build_condition_cells(df, axes=("ema_stack",))
    verdicts = evaluate_conditions(cells, CFG)
    bull = next(v for v in verdicts if v.state == "bullish" and v.direction == "long")
    assert bull.n_with < CFG.min_n
    assert bull.verdict == "INSUFFICIENT"
    assert bull.dsr is None and bull.pbo is None


def test_evaluate_avoid_is_reachable_end_to_end() -> None:
    """The point of the whole fix: a reliably-negative powered state must be
    able to reach AVOID.

    H8's published NO was therefore real for BUILD but largely untested for
    AVOID -- it could report "this state is good" or nothing, but essentially
    never "avoid this state". This test fails on the pre-fix code.
    """
    rng = np.random.default_rng(3)
    n = 400
    df = pd.DataFrame(
        {
            "direction": ["long"] * (2 * n),
            "strategy": ["s"] * (2 * n),
            # 'bullish' trades average -0.5R against +0.1R elsewhere.
            "ema_stack": (["bullish"] * n) + (["bearish"] * n),
            "pnl_r": list(rng.normal(-0.5, 0.3, n)) + list(rng.normal(0.1, 0.3, n)),
            **{a: ["x"] * (2 * n) for a in _OTHER_AXES},
        }
    )
    cells = build_condition_cells(df, axes=("ema_stack",))
    verdicts = evaluate_conditions(cells, CFG)
    bull = next(v for v in verdicts if v.state == "bullish" and v.direction == "long")
    assert bull.verdict == "AVOID"
    assert bull.lift < 0 and bull.lift_hi < 0


def test_map_mintrl_fail_is_no_edge() -> None:
    """Design doc §7 pre-registered ``n >= MinTRL(0.95)`` on the with-state
    slice, and the code never implemented it -- so every previously published
    BUILD cell cleared a gate missing a pre-committed leg. A cell that fails it
    is NO-EDGE: the effect may be real, but the slice is too short for the
    claimed Sharpe to be told from luck at 95% confidence.
    """
    assert (
        _map_verdict(
            "DISABLE",
            n_supp=120,
            n_ok=False,
            lift=0.20,
            lift_lo=0.05,
            lift_hi=0.35,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        == "NO-EDGE"
    )


def test_map_mintrl_fail_blocks_avoid_too() -> None:
    # The leg must bind in BOTH directions, or it just re-creates the
    # one-directional blindness this branch exists to remove.
    assert (
        _map_verdict(
            "ENABLE",
            n_supp=120,
            n_ok=False,
            lift=-0.20,
            lift_lo=-0.35,
            lift_hi=-0.05,
            dsr=0.97,
            pbo=0.2,
            cfg=CFG,
        )
        == "NO-EDGE"
    )


def test_evaluate_reports_mintrl_on_resolved_cells() -> None:
    """The MinTRL figure is rendered in the audit table so a reader can see
    WHICH leg a NO-EDGE cell failed. It must therefore be populated on every
    resolved cell, and must be finite for a real effect."""
    rng = np.random.default_rng(0)
    n = 400
    df = pd.DataFrame(
        {
            "direction": ["long"] * (2 * n),
            "strategy": ["s"] * (2 * n),
            "ema_stack": (["bullish"] * n) + (["bearish"] * n),
            "pnl_r": list(rng.normal(0.5, 0.3, n)) + list(rng.normal(-0.1, 0.3, n)),
            **{a: ["x"] * (2 * n) for a in _OTHER_AXES},
        }
    )
    cells = build_condition_cells(df, axes=("ema_stack",))
    bull = next(
        v
        for v in evaluate_conditions(cells, CFG)
        if v.state == "bullish" and v.direction == "long"
    )
    assert bull.mintrl is not None
    assert np.isfinite(bull.mintrl)
    assert float(bull.n_with) >= bull.mintrl  # a strong effect clears it
    assert bull.verdict == "BUILD"
