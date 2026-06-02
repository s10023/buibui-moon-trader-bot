"""Tests for the outcome-ledger SL/TP fallback.

The live outcome-ledger writer must persist a non-NULL sl_price/tp_price for
*every* fired event, falling back to the same pct-based SL the alert formatter
already uses when an event carries no valid structural SL. See
docs/superpowers/specs/2026-06-01-outcome-ledger-sl-tp-fallback-design.md.
"""

import math

from analytics.signal.scanner import _resolve_outcome_sl_tp


class TestResolveOutcomeSlTp:
    """Pure per-event SL/TP resolver mirroring the alert formatter's fallback."""

    def test_long_uses_structural_sl_when_valid(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=95.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # structural sl_dist = 100 - 95 = 5 → sl = 95, tp = 100 + 5*2 = 110
        assert math.isclose(sl, 95.0)
        assert math.isclose(tp, 110.0)

    def test_long_falls_back_to_pct_when_no_structural(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=0.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # no structural → fallback sl_dist = 100*0.02 = 2 → sl = 98, tp = 100 + 2*2 = 104
        assert math.isclose(sl, 98.0)
        assert math.isclose(tp, 104.0)

    def test_short_falls_back_to_pct_when_no_structural(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="short",
            entry=100.0,
            struct_sl=0.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # fallback sl_dist = 2 → sl = 102, tp = 100 - 2*2 = 96
        assert math.isclose(sl, 102.0)
        assert math.isclose(tp, 96.0)

    def test_short_uses_structural_sl_when_valid(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="short",
            entry=100.0,
            struct_sl=104.0,
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        # structural sl_dist = 104 - 100 = 4 → sl = 104, tp = 100 - 4*2 = 92
        assert math.isclose(sl, 104.0)
        assert math.isclose(tp, 92.0)

    def test_min_sl_floor_widens_tiny_structural(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=99.9,  # sl_dist 0.1 < floor
            struct_tp=0.0,
            eff_sl_pct=0.02,
            min_sl_pct=0.01,  # floor = 1.0
            tp_r=2.0,
        )
        # floored sl_dist = max(0.1, 1.0) = 1.0 → sl = 99, tp = 100 + 1*2 = 102
        assert math.isclose(sl, 99.0)
        assert math.isclose(tp, 102.0)

    def test_structural_tp_preferred_over_sl_dist(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="long",
            entry=100.0,
            struct_sl=95.0,
            struct_tp=120.0,  # valid structural TP wins over 100+5*2=110
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        assert math.isclose(sl, 95.0)
        assert math.isclose(tp, 120.0)

    def test_short_structural_tp_preferred(self) -> None:
        sl, tp = _resolve_outcome_sl_tp(
            direction="short",
            entry=100.0,
            struct_sl=104.0,
            struct_tp=80.0,  # valid (0 < 80 < 100) wins over 100-4*2=92
            eff_sl_pct=0.02,
            min_sl_pct=0.0,
            tp_r=2.0,
        )
        assert math.isclose(sl, 104.0)
        assert math.isclose(tp, 80.0)
