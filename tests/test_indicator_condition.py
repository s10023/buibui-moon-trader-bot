"""Tests for analytics/indicator_condition.py (H8 M1 indicator-conditioning audit)."""

from __future__ import annotations

from analytics.indicator_condition import IndicatorConditionConfig, _map_verdict

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
