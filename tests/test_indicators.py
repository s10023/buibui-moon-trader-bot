"""Tests for analytics/indicators.py (AVWAP / Bollinger / ER / PA character)."""

from __future__ import annotations

import pandas as pd

from analytics.indicators import (
    anchored_vwap,
    bollinger_state,
    efficiency_ratio,
    pa_character,
)


def _hbar(open_time: int, price: float, volume: float) -> dict[str, object]:
    return {
        "open_time": open_time,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": volume,
    }


class TestAnchoredVwap:
    def test_hand_math(self) -> None:
        # typical == price here (flat bars). VWAP = (100*10 + 110*30)/40 = 107.5
        df = pd.DataFrame(
            [_hbar(0, 90.0, 99.0), _hbar(10, 100.0, 10.0), _hbar(20, 110.0, 30.0)]
        )
        assert anchored_vwap(df, anchor_ms=10) == 107.5

    def test_anchor_boundary_is_inclusive(self) -> None:
        df = pd.DataFrame([_hbar(10, 100.0, 10.0)])
        assert anchored_vwap(df, anchor_ms=10) == 100.0

    def test_no_rows_past_anchor_returns_none(self) -> None:
        df = pd.DataFrame([_hbar(0, 100.0, 10.0)])
        assert anchored_vwap(df, anchor_ms=999) is None

    def test_zero_volume_returns_none(self) -> None:
        df = pd.DataFrame([_hbar(10, 100.0, 0.0)])
        assert anchored_vwap(df, anchor_ms=0) is None

    def test_empty_frame_returns_none(self) -> None:
        # The `hourly_df.empty` early-return, distinct from an empty window.
        assert anchored_vwap(pd.DataFrame(), anchor_ms=0) is None


class TestEfficiencyRatio:
    def test_straight_line_is_one(self) -> None:
        close = pd.Series([float(i) for i in range(11)])
        assert abs(efficiency_ratio(close, 10) - 1.0) < 1e-9

    def test_round_trip_is_zero(self) -> None:
        # up 5 then back down 5: net 0, gross 10 -> ER 0.
        close = pd.Series([0.0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0])
        assert efficiency_ratio(close, 10) == 0.0

    def test_flat_series_denominator_zero(self) -> None:
        close = pd.Series([5.0] * 11)
        assert efficiency_ratio(close, 10) == 0.0

    def test_too_short_returns_zero(self) -> None:
        assert efficiency_ratio(pd.Series([1.0, 2.0]), 10) == 0.0


class TestPaCharacter:
    def test_impulse_up(self) -> None:
        # +1.0/bar over 10 bars, ATR 1.0 -> ER 1.0, speed 1.0 >= 0.8.
        close = pd.Series([float(i) for i in range(11)])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "impulse_up"
        assert abs(read.er - 1.0) < 1e-9
        assert abs(read.speed_atr - 1.0) < 1e-9

    def test_grind_down(self) -> None:
        # -0.5/bar, ATR 1.0 -> ER 1.0 directional, speed 0.5 < 0.8.
        close = pd.Series([10.0 - 0.5 * i for i in range(11)])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "grind_down"

    def test_impulse_down(self) -> None:
        # -1.0/bar, ATR 1.0 -> ER 1.0 directional, speed 1.0 >= 0.8, down.
        close = pd.Series([10.0 - float(i) for i in range(11)])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "impulse_down"

    def test_grind_up(self) -> None:
        # +0.5/bar, ATR 1.0 -> ER 1.0 directional, speed 0.5 < 0.8, up.
        close = pd.Series([0.5 * i for i in range(11)])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "grind_up"

    def test_chop(self) -> None:
        close = pd.Series([0.0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0])
        read = pa_character(close, atr14=1.0)
        assert read is not None
        assert read.label == "chop"

    def test_boundary_thresholds_inclusive(self) -> None:
        # ER exactly 0.40 counts as directional; speed exactly 0.8 as impulse.
        # net +4 over gross 10 -> ER 0.4; mean |d| = 1.0; ATR 1.25 -> speed 0.8.
        close = pd.Series([0.0, 1, 2, 3, 4, 5, 6, 7, 6, 5, 4])
        read = pa_character(close, atr14=1.25)
        assert read is not None
        assert abs(read.er - 0.40) < 1e-9
        assert abs(read.speed_atr - 0.8) < 1e-9
        assert read.label == "impulse_up"

    def test_short_series_returns_none(self) -> None:
        assert pa_character(pd.Series([1.0, 2.0]), atr14=1.0) is None

    def test_zero_atr_returns_none(self) -> None:
        close = pd.Series([float(i) for i in range(11)])
        assert pa_character(close, atr14=0.0) is None


class TestBollingerState:
    def test_constant_price_returns_none(self) -> None:
        # sd == 0 -> bands collapse -> None.
        assert bollinger_state(pd.Series([100.0] * 30), ref_price=100.0) is None

    def test_zero_mean_price_returns_none(self) -> None:
        # middle == 0 (bands undefined as a %) even with non-zero sd -> None.
        # Alternating -1/+1 over 20 bars: rolling mean 0, population sd 1.
        assert bollinger_state(pd.Series([-1.0, 1.0] * 10), ref_price=0.0) is None

    def test_short_series_returns_none(self) -> None:
        assert bollinger_state(pd.Series([100.0, 101.0]), ref_price=100.0) is None

    def test_pct_b_and_bandwidth_hand_math(self) -> None:
        # Alternating 99/101 over 20 bars: mean 100, population sd 1.
        # upper = 102, lower = 98, bandwidth = 4/100 = 0.04.
        # ref 101 -> %B = (101-98)/4 = 0.75. Short history -> pctile None.
        close = pd.Series([99.0, 101.0] * 10)
        read = bollinger_state(close, ref_price=101.0)
        assert read is not None
        assert abs(read.pct_b - 0.75) < 1e-9
        assert abs(read.bandwidth - 0.04) < 1e-9
        assert read.bw_pctile is None
        assert read.squeeze is None

    def test_pctile_with_enough_history(self) -> None:
        # 100 flat-ish bars then 60 alternating: enough bandwidth history
        # (>= 60 valid bandwidth values) -> pctile is not None and in [0, 1].
        close = pd.Series(
            [100.0 + (0.1 if i % 2 else -0.1) for i in range(100)] + [99.0, 101.0] * 30
        )
        read = bollinger_state(close, ref_price=100.0)
        assert read is not None
        assert read.bw_pctile is not None
        assert 0.0 <= read.bw_pctile <= 1.0
        assert read.squeeze is not None
