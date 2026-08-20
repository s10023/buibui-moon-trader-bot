"""ST56 — the wick-anchor construction.

Pre-registration: ``docs/audits/2026-08-20-st56-wick-fill-anchor-power.md``.
The legality test below is the load-bearing one: the wick is defined by its own
candle's extreme, which is the shape that withdrew the structural-touch BUILD.
"""

import numpy as np
import pytest

from analytics.wick_anchor import (
    ExitResult,
    classify_production_geometry,
    entry_index_for_fire,
    simulate_exit,
    wick_anchor_stop,
)


class TestWickAnchorStop:
    def test_long_anchor_below_entry_is_kept_verbatim(self) -> None:
        assert wick_anchor_stop("long", entry=100.0, wick=99.9) == 99.9

    def test_short_anchor_above_entry_is_kept_verbatim(self) -> None:
        assert wick_anchor_stop("short", entry=100.0, wick=100.1) == 100.1

    def test_no_floor_is_applied_however_close_the_wick(self) -> None:
        """The whole construction is that the 0.5% floor does NOT apply here.

        A production floor would snap this to 99.5; the anchor arm must not.
        """
        assert wick_anchor_stop("long", entry=100.0, wick=99.99) == 99.99

    def test_wrong_side_drops_rather_than_defaulting(self) -> None:
        assert wick_anchor_stop("long", entry=100.0, wick=100.5) is None
        assert wick_anchor_stop("short", entry=100.0, wick=99.5) is None

    def test_anchor_exactly_at_entry_drops(self) -> None:
        assert wick_anchor_stop("long", entry=100.0, wick=100.0) is None


class TestProductionGeometry:
    """The three paths must be distinguishable — the row named only one."""

    def test_structural_when_the_wick_survives_both_checks(self) -> None:
        got = classify_production_geometry(
            "long", entry=100.0, struct_sl=98.0, sl_pct=0.02, min_sl_pct=0.005
        )
        assert got == "structural"

    def test_fallback_when_the_side_of_entry_test_fails(self) -> None:
        got = classify_production_geometry(
            "long", entry=100.0, struct_sl=101.0, sl_pct=0.02, min_sl_pct=0.005
        )
        assert got == "fallback"

    def test_floor_when_the_wick_is_nearer_than_min_sl_pct(self) -> None:
        """The mechanism ST56 never named: side test passes, floor clamps it away."""
        got = classify_production_geometry(
            "long", entry=100.0, struct_sl=99.9, sl_pct=0.02, min_sl_pct=0.005
        )
        assert got == "floor"

    def test_floor_and_fallback_are_not_conflated(self) -> None:
        floor = classify_production_geometry(
            "short", entry=100.0, struct_sl=100.1, sl_pct=0.02, min_sl_pct=0.005
        )
        fallback = classify_production_geometry(
            "short", entry=100.0, struct_sl=99.0, sl_pct=0.02, min_sl_pct=0.005
        )
        assert floor == "floor"
        assert fallback == "fallback"


class TestEntryLegality:
    def test_entry_is_the_bar_after_the_fire_bar(self) -> None:
        assert entry_index_for_fire(fire_idx=10, n_bars=100) == 11

    def test_a_fire_on_the_last_bar_has_no_legal_entry(self) -> None:
        assert entry_index_for_fire(fire_idx=99, n_bars=100) is None

    def test_entry_is_never_the_fire_bar_itself(self) -> None:
        """MUTATION guard: an off-by-one here reintroduces same-bar entry."""
        for fire_idx in range(50):
            got = entry_index_for_fire(fire_idx=fire_idx, n_bars=100)
            assert got is not None
            assert got > fire_idx


class TestSimulateExit:
    @staticmethod
    def _bars(highs: list[float], lows: list[float]) -> tuple[np.ndarray, np.ndarray]:
        return np.array(highs, dtype=float), np.array(lows, dtype=float)

    def test_long_take_profit_pays_tp_r(self) -> None:
        highs, lows = self._bars([100.0, 106.0], [99.0, 99.5])
        got = simulate_exit(highs, lows, 0, "long", sl=98.0, tp=105.0, tp_r=2.5)
        assert got == ExitResult(r=2.5, bars_held=1, ambiguous=False, r_optimistic=2.5)

    def test_long_stop_loss_pays_minus_one(self) -> None:
        highs, lows = self._bars([100.0, 101.0], [99.0, 97.0])
        got = simulate_exit(highs, lows, 0, "long", sl=98.0, tp=105.0, tp_r=2.5)
        assert got.r == -1.0

    def test_short_take_profit_pays_tp_r(self) -> None:
        highs, lows = self._bars([100.0, 100.5], [99.0, 94.0])
        got = simulate_exit(highs, lows, 0, "short", sl=102.0, tp=95.0, tp_r=2.0)
        assert got.r == 2.0

    def test_both_touched_in_one_bar_resolves_pessimistically(self) -> None:
        """Intrabar order is unknowable, so the loser wins. Flagged, not hidden."""
        highs, lows = self._bars([100.0, 106.0], [99.0, 97.0])
        got = simulate_exit(highs, lows, 0, "long", sl=98.0, tp=105.0, tp_r=2.5)
        assert got.r == -1.0
        assert got.ambiguous is True
        assert got.r_optimistic == 2.5, "the other reading must be recoverable"

    def test_unresolved_by_end_of_data_returns_none(self) -> None:
        highs, lows = self._bars([100.0, 101.0], [99.0, 99.5])
        got = simulate_exit(highs, lows, 0, "long", sl=98.0, tp=105.0, tp_r=2.5)
        assert got.r is None

    def test_the_entry_bar_itself_can_resolve(self) -> None:
        highs, lows = self._bars([106.0], [99.0])
        got = simulate_exit(highs, lows, 0, "long", sl=98.0, tp=105.0, tp_r=2.5)
        assert got.r == 2.5
        assert got.bars_held == 0

    def test_rejects_an_unknown_direction(self) -> None:
        highs, lows = self._bars([100.0], [99.0])
        with pytest.raises(ValueError):
            simulate_exit(highs, lows, 0, "sideways", sl=98.0, tp=105.0, tp_r=2.5)
