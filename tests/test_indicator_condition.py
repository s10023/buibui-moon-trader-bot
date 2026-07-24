"""Tests for analytics/indicator_condition.py (H8 M1 indicator-conditioning audit)."""

from __future__ import annotations

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
