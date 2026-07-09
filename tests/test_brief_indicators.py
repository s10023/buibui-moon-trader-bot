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
