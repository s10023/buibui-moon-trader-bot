"""Tests for analytics/brief/indicators.py (per-symbol indicator states)."""

from __future__ import annotations

import math

import pandas as pd

from analytics.brief.indicators import (
    _ema_state,
    _monday_state,
    _range_state,
    build_indicator_state,
)

DAY_MS = 86_400_000
# 2024-01-01 00:00 UTC — a Monday (matches tests/_brief_fixtures.py).
START_MS = 1_704_067_200_000


def _daily_frame(n_days: int, base: float = 100.0) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for i in range(n_days):
        o = base + 10.0 * math.sin(i / 7.0)
        c = o * (1.0 + 0.01 * math.sin(i / 3.0))
        rows.append(
            {
                "open_time": START_MS + i * DAY_MS,
                "open": o,
                "high": max(o, c) * 1.01,
                "low": min(o, c) * 0.99,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


class TestEmaState:
    def test_full_history_has_all_spans(self) -> None:
        df = _daily_frame(250)
        state = _ema_state(df, ref_close=200.0)
        assert state is not None
        assert state.above_20 is True
        assert state.above_50 is True
        assert state.above_200 is True
        assert state.stack in ("bullish", "bearish", "mixed")
        assert state.slope_200 in ("rising", "falling")

    def test_warmup_shorter_than_span_is_none(self) -> None:
        df = _daily_frame(30)  # >= 20, < 50, < 200
        state = _ema_state(df, ref_close=1.0)
        assert state is not None
        assert state.above_20 is False
        assert state.above_50 is None
        assert state.above_200 is None
        assert state.stack is None
        assert state.slope_200 is None

    def test_empty_frame_is_none(self) -> None:
        assert _ema_state(_daily_frame(0), ref_close=1.0) is None


class TestRangeState:
    def test_run_length_and_bounds(self) -> None:
        df = _daily_frame(10)
        regime = pd.Series(["trend"] * 6 + ["range"] * 4)
        state = _range_state(df, regime, ref_close=float(df["close"].iloc[-1]))
        assert state is not None
        assert state.label == "range"
        assert state.bars == 4
        assert state.since_ms == START_MS + 6 * DAY_MS
        assert state.range_low is not None and state.range_high is not None
        assert state.range_low == float(df["low"].astype(float).tail(4).min())
        assert state.range_high == float(df["high"].astype(float).tail(4).max())
        assert state.pos is not None and 0.0 <= state.pos <= 1.0

    def test_trend_run_has_no_bounds(self) -> None:
        df = _daily_frame(10)
        regime = pd.Series(["range"] * 5 + ["trend"] * 5)
        state = _range_state(df, regime, ref_close=100.0)
        assert state is not None
        assert state.label == "trend"
        assert state.bars == 5
        assert state.range_low is None and state.pos is None

    def test_empty_regime_is_none(self) -> None:
        assert _range_state(_daily_frame(0), pd.Series(dtype=object), 1.0) is None

    def test_misaligned_regime_series_is_none(self) -> None:
        df = _daily_frame(10)
        regime = pd.Series(["range"] * 7)  # shorter than the frame
        assert _range_state(df, regime, ref_close=100.0) is None

    def test_reordered_same_length_regime_series_is_none(self) -> None:
        # Same length but a reordered index -> not positionally aligned with
        # the frame. classify_series always returns index=df.index, so this
        # never happens in production; the guard must still reject it rather
        # than silently mislabel bars.
        df = _daily_frame(10)  # default RangeIndex 0..9
        regime = pd.Series(["range"] * 10, index=list(range(9, -1, -1)))
        assert _range_state(df, regime, ref_close=100.0) is None


class TestMondayState:
    def test_forming_on_monday(self) -> None:
        df = _daily_frame(15)
        monday_as_of = START_MS + 14 * DAY_MS + 3_600_000  # Mon 01:00 UTC
        state = _monday_state(df, ref_close=100.0, as_of_ms=monday_as_of)
        assert state is not None
        assert state.state == "forming"
        assert state.pos is None

    def test_inside_has_position(self) -> None:
        df = _daily_frame(16)
        tuesday_as_of = START_MS + 15 * DAY_MS + 3_600_000  # Tue 01:00 UTC
        mon_high = float(df["high"].iloc[14])
        mon_low = float(df["low"].iloc[14])
        mid = (mon_high + mon_low) / 2.0
        state = _monday_state(df, ref_close=mid, as_of_ms=tuesday_as_of)
        assert state is not None
        assert state.state == "inside"
        assert state.pos is not None and abs(state.pos - 0.5) < 1e-9

    def test_above_and_below(self) -> None:
        df = _daily_frame(16)
        tuesday_as_of = START_MS + 15 * DAY_MS + 3_600_000
        assert _monday_state(df, 10_000.0, tuesday_as_of).state == "above"  # type: ignore[union-attr]
        assert _monday_state(df, 1.0, tuesday_as_of).state == "below"  # type: ignore[union-attr]


class TestBuildIndicatorState:
    def test_scaffold_builds_first_three_states(self) -> None:
        df = _daily_frame(250)
        regime = pd.Series(["trend"] * 250)
        tuesday_as_of = START_MS + 250 * DAY_MS  # frame is fully completed
        state, notes = build_indicator_state(
            completed_1d=df,
            completed_1h=pd.DataFrame(
                columns=["open_time", "open", "high", "low", "close", "volume"]
            ),
            regime_series_1d=regime,
            ref_close=100.0,
            atr14=2.0,
            as_of_ms=tuesday_as_of,
        )
        assert state is not None
        assert state.ema is not None
        assert state.range_state is not None
        assert notes == []

    def test_all_blocks_failing_collapses_to_none(self) -> None:
        # Non-empty but non-numeric frames: every sub-block raises inside the
        # independence-contract wrapper -> all None -> state collapses. A
        # non-Monday as_of keeps _monday_state from returning a "forming".
        poison = pd.DataFrame(
            [
                {
                    "open_time": START_MS + DAY_MS,
                    "open": "x",
                    "high": "x",
                    "low": "x",
                    "close": "x",
                    "volume": "x",
                }
            ]
        )
        regime = pd.Series(["range"], index=poison.index)  # index-aligned
        state, notes = build_indicator_state(
            completed_1d=poison,
            completed_1h=poison.copy(),
            regime_series_1d=regime,
            ref_close=100.0,
            atr14=2.0,
            as_of_ms=START_MS + DAY_MS,  # Tuesday
        )
        assert state is None
        # ema/range/candle/pa/bb/vwap/profile all raise -> >= 7 failure notes.
        assert sum("failed" in n for n in notes) >= 7


class TestCandleHits:
    def test_bullish_engulfing_on_last_bar(self) -> None:
        from analytics.brief.indicators import _candle_hits

        # Bar 0 bearish (100 -> 98), bar 1 bullish engulfing (97 -> 101).
        df = pd.DataFrame(
            [
                {
                    "open_time": START_MS,
                    "open": 100.0,
                    "high": 100.5,
                    "low": 97.5,
                    "close": 98.0,
                    "volume": 1000.0,
                },
                {
                    "open_time": START_MS + DAY_MS,
                    "open": 97.0,
                    "high": 101.5,
                    "low": 96.5,
                    "close": 101.0,
                    "volume": 1000.0,
                },
            ]
        )
        hits = _candle_hits(df)
        assert ("engulfing", "long") in [(h.pattern, h.direction) for h in hits]

    def test_pattern_on_earlier_bar_is_ignored(self) -> None:
        from analytics.brief.indicators import _candle_hits

        # Same engulfing pair, then a plain drifting third bar: engulfing
        # fired on bar 1, which is no longer the last bar -> not reported.
        df = pd.DataFrame(
            [
                {
                    "open_time": START_MS,
                    "open": 100.0,
                    "high": 100.5,
                    "low": 97.5,
                    "close": 98.0,
                    "volume": 1000.0,
                },
                {
                    "open_time": START_MS + DAY_MS,
                    "open": 97.0,
                    "high": 101.5,
                    "low": 96.5,
                    "close": 101.0,
                    "volume": 1000.0,
                },
                {
                    "open_time": START_MS + 2 * DAY_MS,
                    "open": 101.0,
                    "high": 101.6,
                    "low": 100.8,
                    "close": 101.5,
                    "volume": 1000.0,
                },
            ]
        )
        hits = _candle_hits(df)
        assert ("engulfing", "long") not in [(h.pattern, h.direction) for h in hits]

    def test_no_patterns_is_empty_list_not_none(self) -> None:
        from analytics.brief.indicators import _candle_hits

        hits = _candle_hits(_daily_frame(30))
        assert isinstance(hits, list)


class TestPaState:
    def test_labels_flow_through(self) -> None:
        from analytics.brief.indicators import _pa_state

        rows = [
            {
                "open_time": START_MS + i * DAY_MS,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.0 + i,
                "volume": 1000.0,
            }
            for i in range(15)
        ]
        state = _pa_state(pd.DataFrame(rows), atr14=1.0)
        assert state is not None
        assert state.label == "impulse_up"
        assert state.er > 0.99

    def test_zero_atr_is_none(self) -> None:
        from analytics.brief.indicators import _pa_state

        assert _pa_state(_daily_frame(15), atr14=0.0) is None


H1_MS = 3_600_000


def _hourly_frame(n_hours: int, price: float = 100.0) -> pd.DataFrame:
    rows = [
        {
            "open_time": START_MS + i * H1_MS,
            "open": price,
            "high": price * 1.001,
            "low": price * 0.999,
            "close": price,
            "volume": 100.0,
        }
        for i in range(n_hours)
    ]
    return pd.DataFrame(rows)


class TestVwapState:
    def test_weekly_and_monthly_anchor(self) -> None:
        from analytics.brief.indicators import _vwap_state

        # START_MS is Mon 2024-01-01 00:00 UTC: week + month anchor coincide.
        hourly = _hourly_frame(48)
        as_of = START_MS + 2 * DAY_MS
        state = _vwap_state(hourly, ref_close=102.0, atr14=2.0, as_of_ms=as_of)
        assert state is not None
        assert state.weekly_price is not None
        assert abs(state.weekly_price - 100.0) < 0.2  # flat 100 bars
        assert state.weekly_dist_atr is not None
        assert state.weekly_dist_atr > 0  # price above VWAP -> positive
        assert state.monthly_price is not None

    def test_no_bars_past_anchor_is_none(self) -> None:
        from analytics.brief.indicators import _vwap_state

        # as_of in the NEXT week/month with no 1h bars after the anchors.
        hourly = _hourly_frame(24)
        as_of = START_MS + 40 * DAY_MS  # 2024-02-10, anchors past the data
        assert _vwap_state(hourly, 100.0, 2.0, as_of) is None

    def test_zero_atr_is_none(self) -> None:
        from analytics.brief.indicators import _vwap_state

        assert _vwap_state(_hourly_frame(24), 100.0, 0.0, START_MS + DAY_MS) is None


class TestProfileState:
    def test_inside_value_area(self) -> None:
        from analytics.brief.indicators import _profile_state

        hourly = _hourly_frame(24 * 10)
        as_of = START_MS + 10 * DAY_MS
        state = _profile_state(hourly, ref_close=100.0, atr14=2.0, as_of_ms=as_of)
        assert state is not None
        assert state.vs_value == "inside"
        assert state.val <= state.poc <= state.vah

    def test_above_value_area(self) -> None:
        from analytics.brief.indicators import _profile_state

        hourly = _hourly_frame(24 * 10)
        as_of = START_MS + 10 * DAY_MS
        state = _profile_state(hourly, ref_close=200.0, atr14=2.0, as_of_ms=as_of)
        assert state is not None
        assert state.vs_value == "above"
        assert state.poc_dist_atr < 0  # POC far below price

    def test_empty_window_is_none(self) -> None:
        from analytics.brief.indicators import _profile_state

        # All bars older than the 60d window.
        hourly = _hourly_frame(24)
        as_of = START_MS + 100 * DAY_MS
        assert _profile_state(hourly, 100.0, 2.0, as_of) is None


class TestBbStateAdapter:
    def test_flows_through(self) -> None:
        from analytics.brief.indicators import _bb_state

        state = _bb_state(_daily_frame(60), ref_close=100.0)
        assert state is not None
        assert isinstance(state.pct_b, float)

    def test_empty_frame_is_none(self) -> None:
        from analytics.brief.indicators import _bb_state

        assert _bb_state(_daily_frame(0), ref_close=100.0) is None

    def test_constant_price_read_none_is_none(self) -> None:
        from analytics.brief.indicators import _bb_state

        # Flat closes -> sd 0 -> bollinger_state None -> adapter None.
        flat = pd.DataFrame(
            [
                {
                    "open_time": START_MS + i * DAY_MS,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000.0,
                }
                for i in range(30)
            ]
        )
        assert _bb_state(flat, ref_close=100.0) is None


class TestFailureIsolation:
    def test_poisoned_hourly_frame_fails_only_hourly_blocks(self) -> None:
        # 1h frame with rows but NO volume column: vwap + profile raise
        # KeyError inside the adapter -> their sub-blocks None + 2 notes;
        # the 1d-based sub-blocks survive.
        df = _daily_frame(250)
        regime = pd.Series(["trend"] * 250)
        # open_time must land INSIDE the week/month anchor windows and the
        # 60d profile window, otherwise vwap/profile return None (empty
        # window) instead of raising, and no note is emitted.
        bad_hourly = pd.DataFrame(
            [
                {
                    "open_time": START_MS + 249 * DAY_MS,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                }
            ]
        )
        state, notes = build_indicator_state(
            completed_1d=df,
            completed_1h=bad_hourly,
            regime_series_1d=regime,
            ref_close=100.0,
            atr14=2.0,
            as_of_ms=START_MS + 250 * DAY_MS,
        )
        assert state is not None
        assert state.ema is not None
        assert state.pa is not None
        assert state.vwap is None
        assert state.profile is None
        assert sum("failed" in n for n in notes) == 2
        assert any(n.startswith("indicator vwap failed") for n in notes)
        assert any(n.startswith("indicator profile failed") for n in notes)
