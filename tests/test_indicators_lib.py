"""Tests for analytics/indicators_lib.py."""

import datetime
from collections.abc import Callable

import pandas as pd
import pytest

from analytics.indicators_lib import (
    SIGNAL_COLUMNS,
    detect_eqh_eql,
    detect_funding_extreme,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_orb_breakout,
    detect_order_block,
    detect_smt_divergence,
    detect_wick_fills,
    seasonality_stats,
)
from tests.conftest import _candle, _make_ohlcv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MS_PER_HOUR = 3_600_000
_BASE_TIME = 1_700_000_000_000


def _hourly_ts(hour_offset: int) -> int:
    """Return a UTC timestamp at 00:00 + hour_offset hours."""
    base = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)
    return int((base + datetime.timedelta(hours=hour_offset)).timestamp() * 1000)


def _assert_signal_columns(df: pd.DataFrame) -> None:
    assert list(df.columns) == SIGNAL_COLUMNS


# ---------------------------------------------------------------------------
# Seasonality stats
# ---------------------------------------------------------------------------


class TestSeasonalityStats:
    def test_returns_empty_on_empty_input(self) -> None:
        result = seasonality_stats(pd.DataFrame())
        assert result.empty

    def test_returns_empty_on_single_row(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 105)])
        assert seasonality_stats(df).empty

    def test_returns_correct_period_types(self) -> None:
        rows = [
            _candle(_hourly_ts(i), 100.0, 110.0, 90.0, 105.0)
            for i in range(48)  # 2 full days of hourly candles
        ]
        df = _make_ohlcv(rows)
        stats = seasonality_stats(df)
        assert set(stats["period_type"].unique()) == {
            "day_of_week",
            "hour_of_day",
            "week_of_month",
        }

    def test_avg_return_direction(self) -> None:
        rows = [
            _candle(_hourly_ts(i), 100.0, 110.0, 90.0, 110.0)  # all green (up 10%)
            for i in range(24)
        ]
        df = _make_ohlcv(rows)
        stats = seasonality_stats(df)
        dow_stats = stats[stats["period_type"] == "day_of_week"]
        assert all(dow_stats["avg_return_pct"] > 0)
        assert all(dow_stats["win_rate"] == 1.0)

    def test_win_rate_all_losses(self) -> None:
        rows = [
            _candle(_hourly_ts(i), 110.0, 115.0, 85.0, 90.0)  # all red
            for i in range(24)
        ]
        df = _make_ohlcv(rows)
        stats = seasonality_stats(df)
        dow_stats = stats[stats["period_type"] == "day_of_week"]
        assert all(dow_stats["win_rate"] == 0.0)


# ---------------------------------------------------------------------------
# Wick Fill
# ---------------------------------------------------------------------------


class TestDetectWickFills:
    def test_returns_empty_on_fewer_than_3_candles(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 80, 105)])
        result = detect_wick_fills(df)
        assert result.empty
        _assert_signal_columns(result)

    def test_detects_lower_wick_fill_long(self) -> None:
        # Candle with big lower wick (body=2, wick=10) → next candle dips in
        rows = [
            _candle(_BASE_TIME + 0, 100, 103, 90, 102),  # lower wick=10 > 0.5×body=1
            _candle(_BASE_TIME + 1, 101, 103, 91, 102),  # low=91 ≤ zone_top=100
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _BASE_TIME + 1

    def test_detects_upper_wick_fill_short(self) -> None:
        # Candle with big upper wick → next candle enters the zone
        rows = [
            _candle(_BASE_TIME + 0, 100, 115, 99, 101),  # upper wick=14 > 0.5×body=1
            _candle(_BASE_TIME + 1, 102, 114, 101, 103),  # high=114 ≥ zone_bot=101
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_no_signal_when_wick_too_small(self) -> None:
        # Tiny wicks relative to body
        rows = [
            _candle(_BASE_TIME + 0, 100, 101, 99, 109),  # body=9, wick=0.1
            _candle(_BASE_TIME + 1, 105, 107, 98, 106),
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=2.0)
        assert result.empty

    def test_signal_columns(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 103, 90, 102) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Marubozu Retest
# ---------------------------------------------------------------------------


class TestDetectMarubozuRetest:
    def test_returns_empty_on_fewer_than_3_candles(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 99, 108)])
        result = detect_marubozu_retest(df)
        assert result.empty

    def test_detects_bullish_marubozu_retest_long(self) -> None:
        # Bullish Marubozu: open=100, high=110, low=99.5, close=110
        # body=10, upper_wick=0 (≤0.1×10), lower_wick=0.5 (≤0.1×10)
        rows = [
            _candle(_BASE_TIME + 0, 100, 110, 99.5, 110),  # marubozu
            _candle(_BASE_TIME + 1, 108, 112, 99, 101),  # retest: low≤100, close>100
        ]
        df = _make_ohlcv(rows)
        result = detect_marubozu_retest(df, max_wick_ratio=0.1)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"

    def test_detects_bearish_marubozu_retest_short(self) -> None:
        # Bearish Marubozu: open=110, high=110.5, low=100, close=100
        # body=10, upper_wick=0.5 (≤0.1×10), lower_wick=0 (≤0.1×10)
        rows = [
            _candle(_BASE_TIME + 0, 110, 110.5, 100, 100),  # bearish marubozu
            _candle(_BASE_TIME + 1, 105, 111, 104, 109),  # retest: high≥110, close<110
        ]
        df = _make_ohlcv(rows)
        result = detect_marubozu_retest(df, max_wick_ratio=0.1)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"

    def test_no_signal_when_wick_too_large(self) -> None:
        # Candle with large wicks → not a Marubozu
        rows = [
            _candle(_BASE_TIME + 0, 100, 115, 85, 110),  # big wicks
            _candle(_BASE_TIME + 1, 108, 112, 99, 101),
        ]
        df = _make_ohlcv(rows)
        result = detect_marubozu_retest(df, max_wick_ratio=0.1)
        assert result.empty


# ---------------------------------------------------------------------------
# ORB Breakout
# ---------------------------------------------------------------------------


class TestDetectOrbBreakout:
    def test_returns_empty_on_single_candle(self) -> None:
        df = _make_ohlcv([_candle(_hourly_ts(13), 100, 110, 90, 105)])
        assert detect_orb_breakout(df).empty

    def test_detects_long_breakout(self) -> None:
        rows = [
            _candle(
                _hourly_ts(13), 100, 110, 90, 105
            ),  # session candle: range [90, 110]
            _candle(_hourly_ts(14), 111, 120, 108, 115),  # close=115 > 110 → long
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, session_hour_utc=13)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _hourly_ts(14)

    def test_detects_short_breakout(self) -> None:
        rows = [
            _candle(_hourly_ts(13), 100, 110, 90, 105),  # range [90, 110]
            _candle(_hourly_ts(14), 89, 92, 80, 85),  # close=85 < 90 → short
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, session_hour_utc=13)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"

    def test_no_signal_when_close_inside_range(self) -> None:
        rows = [
            _candle(_hourly_ts(13), 100, 110, 90, 105),
            _candle(_hourly_ts(14), 103, 108, 95, 103),  # close=103 inside [90,110]
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, session_hour_utc=13)
        assert result.empty

    def test_no_signal_when_no_session_candle(self) -> None:
        rows = [
            _candle(_hourly_ts(10), 100, 110, 90, 105),
            _candle(_hourly_ts(11), 105, 115, 95, 112),
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, session_hour_utc=13)
        assert result.empty


# ---------------------------------------------------------------------------
# Liquidity Sweep
# ---------------------------------------------------------------------------


class TestDetectLiquiditySweep:
    def test_returns_empty_when_too_few_candles(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20)
        assert result.empty

    def test_detects_sweep_high_short(self) -> None:
        # Build lookback window with max high=110, then a sweep candle
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(20)]
        # Sweep: high=115 (exceeds 110), close=105 (below 110)
        rows.append(_candle(_BASE_TIME + 20, 108, 115, 100, 105))
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_detects_sweep_low_long(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(20)]
        # Sweep: low=85 (below 90), close=95 (above 90)
        rows.append(_candle(_BASE_TIME + 20, 92, 100, 85, 95))
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_signal_columns(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(25)]
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Fair Value Gap (FVG)
# ---------------------------------------------------------------------------


class TestDetectFvg:
    def test_returns_empty_on_fewer_than_3_candles(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(2)]
        df = _make_ohlcv(rows)
        assert detect_fvg(df).empty

    def test_detects_bullish_fvg_fill_long(self) -> None:
        # Bullish FVG: candle[0].high=100, candle[2].low=105 → gap [100, 105]
        # Fill candle: low=101 enters gap
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),  # impulse candle
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # nxt.low=105 > prev.high=100
            _candle(_BASE_TIME + 3, 109, 112, 101, 103),  # low=101 ≤ gap_top=105
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"
        assert result.iloc[0]["open_time"] == _BASE_TIME + 3

    def test_detects_bearish_fvg_fill_short(self) -> None:
        # Bearish FVG: candle[0].low=100, candle[2].high=95 → gap [95, 100], CE=97.5
        # Fill candle: high=98 ≥ CE=97.5 and close=92 < gap_top=100
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),  # impulse candle
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # nxt.high=95 < prev.low=100
            _candle(_BASE_TIME + 3, 91, 98, 88, 92),  # high=98 ≥ CE=97.5
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "short"

    def test_no_signal_when_no_gap(self) -> None:
        # Overlapping candles — no FVG
        rows = [
            _candle(_BASE_TIME + 0, 100, 105, 95, 103),
            _candle(_BASE_TIME + 1, 103, 108, 100, 106),
            _candle(_BASE_TIME + 2, 106, 110, 104, 108),
            _candle(_BASE_TIME + 3, 108, 112, 105, 109),
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_signal_columns(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Market Structure (BOS / CHoCH)
# ---------------------------------------------------------------------------


class TestDetectMarketStructure:
    def _make_zigzag_up(self) -> pd.DataFrame:
        """Alternating peak/valley candles in an uptrend.

        Pattern: valley, PEAK1, valley, PEAK2>PEAK1, valley, PEAK3>PEAK2
        With swing_lookback=1, peaks are detected as swing highs because they
        are higher than their immediate neighbours.
        """
        valley_high = 98.0  # all valley candles have high below peaks
        peaks = [110.0, 120.0, 130.0]
        rows: list[dict[str, object]] = []
        t = _BASE_TIME
        for peak in peaks:
            rows.append(_candle(t, 90, valley_high, 88, 95))  # valley
            rows.append(_candle(t + 1, 102, peak, 100, peak - 1))  # peak
            rows.append(_candle(t + 2, 96, valley_high, 88, 94))  # valley
            t += 3
        return _make_ohlcv(rows)

    def _make_zigzag_down(self) -> pd.DataFrame:
        """Alternating peak/valley candles in a downtrend.

        Valleys: 95, 85, 75 — each lower than the previous.
        """
        peak_low = 103.0  # all peak candles have low above valleys
        valleys = [95.0, 85.0, 75.0]
        rows: list[dict[str, object]] = []
        t = _BASE_TIME
        for valley in valleys:
            rows.append(_candle(t, 105, 108, peak_low, 106))  # peak
            rows.append(_candle(t + 1, 100, peak_low, valley, valley + 1))  # valley
            rows.append(_candle(t + 2, 105, 108, peak_low, 106))  # peak
            t += 3
        return _make_ohlcv(rows)

    def test_returns_empty_on_too_few_candles(self) -> None:
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        df = _make_ohlcv(rows)
        result = detect_market_structure(df, swing_lookback=5)
        assert result.empty

    def test_detects_bos_long_in_uptrend(self) -> None:
        df = self._make_zigzag_up()
        result = detect_market_structure(df, swing_lookback=1)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_detects_bos_short_in_downtrend(self) -> None:
        df = self._make_zigzag_down()
        result = detect_market_structure(df, swing_lookback=1)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_signal_columns(self) -> None:
        df = self._make_zigzag_up()
        result = detect_market_structure(df, swing_lookback=1)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Funding Rate Mean Reversion
# ---------------------------------------------------------------------------


class TestDetectFundingExtreme:
    def _make_funding(self, rates: list[float]) -> pd.DataFrame:
        rows = [
            {
                "symbol": "BTCUSDT",
                "funding_time": _BASE_TIME + i * _MS_PER_HOUR * 8,
                "funding_rate": r,
            }
            for i, r in enumerate(rates)
        ]
        return pd.DataFrame(rows, columns=["symbol", "funding_time", "funding_rate"])

    def test_returns_empty_on_empty_ohlcv(self) -> None:
        funding = self._make_funding([0.001])
        result = detect_funding_extreme(pd.DataFrame(), funding)
        assert result.empty

    def test_returns_empty_on_empty_funding(self) -> None:
        ohlcv = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 100)])
        result = detect_funding_extreme(ohlcv, pd.DataFrame())
        assert result.empty

    def test_detects_positive_extreme_short(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([0.002, 0.003, 0.002, 0.002, 0.003])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_detects_negative_extreme_long(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([-0.002, -0.003, -0.002, -0.002, -0.003])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_no_signal_when_funding_within_threshold(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([0.0001, 0.0002, -0.0001, 0.0003, -0.0002])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        assert result.empty

    def test_signal_columns(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + i * _MS_PER_HOUR * 8, 100, 110, 90, 100)
                for i in range(5)
            ]
        )
        funding = self._make_funding([0.002, 0.002, 0.002, 0.002, 0.002])
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# SMT Divergence
# ---------------------------------------------------------------------------


class TestDetectSmtDivergence:
    def _make_aligned_ohlcv(
        self,
        highs_p: list[float],
        lows_p: list[float],
        highs_s: list[float],
        lows_s: list[float],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        n = len(highs_p)
        rows_p = [
            _candle(
                _BASE_TIME + i,
                100.0,
                highs_p[i],
                lows_p[i],
                100.0,
                symbol="BTCUSDT",
            )
            for i in range(n)
        ]
        rows_s = [
            _candle(
                _BASE_TIME + i,
                100.0,
                highs_s[i],
                lows_s[i],
                100.0,
                symbol="ETHUSDT",
            )
            for i in range(n)
        ]
        return _make_ohlcv(rows_p), _make_ohlcv(rows_s)

    def test_returns_empty_on_empty_primary(self) -> None:
        secondary = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 100)])
        result = detect_smt_divergence(pd.DataFrame(), secondary)
        assert result.empty

    def test_returns_empty_on_empty_secondary(self) -> None:
        primary = _make_ohlcv([_candle(_BASE_TIME, 100, 110, 90, 100)])
        result = detect_smt_divergence(primary, pd.DataFrame())
        assert result.empty

    def test_detects_bearish_smt(self) -> None:
        # Primary makes new high, secondary does not
        n = 15
        highs_p = [100.0 + i for i in range(n)]  # primary always rising
        lows_p = [90.0] * n
        highs_s = [100.0] * n  # secondary flat (never makes new high after window)
        highs_s[-1] = 99.0  # secondary's last is below window max
        lows_s = [90.0] * n

        df_p, df_s = self._make_aligned_ohlcv(highs_p, lows_p, highs_s, lows_s)
        result = detect_smt_divergence(df_p, df_s, lookback=10)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) >= 1

    def test_detects_bullish_smt(self) -> None:
        # Primary makes new low, secondary does not
        n = 15
        highs_p = [110.0] * n
        lows_p = [100.0 - i for i in range(n)]  # primary always falling
        highs_s = [110.0] * n
        lows_s = [100.0] * n  # secondary flat
        lows_s[-1] = 101.0  # secondary's last is above window min

        df_p, df_s = self._make_aligned_ohlcv(highs_p, lows_p, highs_s, lows_s)
        result = detect_smt_divergence(df_p, df_s, lookback=10)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) >= 1

    def test_no_divergence_when_both_correlated(self) -> None:
        # Both symbols move identically
        n = 15
        highs = [100.0 + i for i in range(n)]
        lows = [90.0] * n
        df_p, df_s = self._make_aligned_ohlcv(highs, lows, highs, lows)
        result = detect_smt_divergence(df_p, df_s, lookback=10)
        # No short signals (both make new highs together)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    def test_signal_columns(self) -> None:
        n = 15
        highs_p = [100.0 + i for i in range(n)]
        lows_p = [90.0] * n
        highs_s = [100.0] * n
        highs_s[-1] = 99.0
        lows_s = [90.0] * n
        df_p, df_s = self._make_aligned_ohlcv(highs_p, lows_p, highs_s, lows_s)
        result = detect_smt_divergence(df_p, df_s, lookback=10)
        _assert_signal_columns(result)


# ---------------------------------------------------------------------------
# Bug fix tests (TDD — written before fixes)
# ---------------------------------------------------------------------------


class TestFvgBugFixes:
    def test_fvg_long_entry_at_ce_not_gap_top(self) -> None:
        # Bullish FVG: gap_bot=100, gap_top=105, CE=102.5
        # Fill candle: low=103 (enters gap_top but NOT CE) → no signal
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # gap [100, 105]
            _candle(
                _BASE_TIME + 3, 109, 112, 103, 111
            ),  # low=103 > CE=102.5 → no signal
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_fvg_long_fires_when_price_reaches_ce(self) -> None:
        # Fill candle: low=102 ≤ CE=102.5 → signal fires
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # gap [100, 105]
            _candle(_BASE_TIME + 3, 109, 112, 102, 111),  # low=102 ≤ CE=102.5 → signal
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"

    def test_fvg_long_does_not_fire_when_price_blasts_through_gap(self) -> None:
        # Fill candle closes BELOW gap_bot=100 → no signal (blasted through)
        rows = [
            _candle(_BASE_TIME + 0, 95, 100, 93, 99),
            _candle(_BASE_TIME + 1, 102, 108, 101, 107),
            _candle(_BASE_TIME + 2, 107, 112, 105, 110),  # gap [100, 105]
            _candle(_BASE_TIME + 3, 109, 112, 98, 99),  # low=98, close=99 < gap_bot=100
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_fvg_short_entry_at_ce_not_gap_bot(self) -> None:
        # Bearish FVG: gap_top=100, gap_bot=95, CE=97.5
        # Fill candle: high=97 (above gap_bot but NOT CE) → no signal
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # gap [95, 100]
            _candle(_BASE_TIME + 3, 91, 97, 88, 92),  # high=97 < CE=97.5 → no signal
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty

    def test_fvg_short_does_not_fire_when_price_blasts_through(self) -> None:
        # Fill candle closes ABOVE gap_top=100 → no signal
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # gap [95, 100]
            _candle(
                _BASE_TIME + 3, 91, 102, 88, 101
            ),  # high=102, close=101 > gap_top=100
        ]
        df = _make_ohlcv(rows)
        result = detect_fvg(df)
        assert result.empty


class TestWickFillBugFixes:
    def test_wick_fill_long_does_not_fire_when_close_below_zone(self) -> None:
        # Lower wick zone: zone_bot=90, zone_top=100
        # Fill candle: low=91 enters zone but close=89 < zone_bot=90 → no signal
        rows = [
            _candle(_BASE_TIME + 0, 100, 103, 90, 102),  # lower wick=10 > 0.5×body=1
            _candle(
                _BASE_TIME + 1, 101, 103, 91, 89
            ),  # low=91 ≤ zone_top, close=89 < zone_bot
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty

    def test_wick_fill_short_does_not_fire_when_close_above_zone(self) -> None:
        # Upper wick zone: zone_bot=101, zone_top=115
        # Fill candle: high=114 enters zone but close=116 > zone_top=115 → no signal
        rows = [
            _candle(_BASE_TIME + 0, 100, 115, 99, 101),  # upper wick=14 > 0.5×body=1
            _candle(
                _BASE_TIME + 1, 102, 114, 101, 116
            ),  # high=114 ≥ zone_bot, close=116 > zone_top
        ]
        df = _make_ohlcv(rows)
        result = detect_wick_fills(df, min_wick_body_ratio=0.5)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty


class TestFundingReversionBugFixes:
    def _make_ohlcv_15m(self, n: int, base_time: int) -> pd.DataFrame:
        """15-minute candles."""
        rows = [
            _candle(base_time + i * 15 * 60 * 1000, 100, 110, 90, 100) for i in range(n)
        ]
        return _make_ohlcv(rows)

    def _make_funding_row(self, funding_time: int, rate: float) -> dict[str, object]:
        return {"symbol": "BTCUSDT", "funding_time": funding_time, "funding_rate": rate}

    def test_funding_reversion_fires_once_per_funding_period(self) -> None:
        # 8h window = 32 × 15m candles; same extreme funding rate → only 1 signal
        n = 32
        ohlcv = self._make_ohlcv_15m(n, _BASE_TIME)
        funding = pd.DataFrame(
            [self._make_funding_row(_BASE_TIME, 0.002)],
            columns=["symbol", "funding_time", "funding_rate"],
        )
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        assert len(result) == 1

    def test_funding_reversion_fires_again_on_new_funding_period(self) -> None:
        # Two separate 8h periods with extreme funding → 2 signals
        ms_per_8h = 8 * 60 * 60 * 1000
        ms_per_15m = 15 * 60 * 1000
        rows = [
            _candle(_BASE_TIME + i * ms_per_15m, 100, 110, 90, 100)
            for i in range(64)  # 2 full 8h periods
        ]
        ohlcv = _make_ohlcv(rows)
        funding = pd.DataFrame(
            [
                self._make_funding_row(_BASE_TIME, 0.002),
                self._make_funding_row(_BASE_TIME + ms_per_8h, 0.002),
            ],
            columns=["symbol", "funding_time", "funding_rate"],
        )
        result = detect_funding_extreme(ohlcv, funding, threshold=0.001)
        assert len(result) == 2


class TestOrbTimeframeBugFix:
    def test_orb_does_not_fire_on_hourly_candles(self) -> None:
        # 1h candles: detect_orb_breakout should reject timeframe_minutes >= 60
        rows = [
            _candle(_hourly_ts(13), 100, 110, 90, 105),
            _candle(_hourly_ts(14), 111, 120, 108, 115),  # would be long signal
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, session_hour_utc=13, timeframe_minutes=60)
        assert result.empty

    def test_orb_fires_on_15m_candles(self) -> None:
        rows = [
            _candle(_hourly_ts(13), 100, 110, 90, 105),
            _candle(_hourly_ts(14), 111, 120, 108, 115),
        ]
        df = _make_ohlcv(rows)
        result = detect_orb_breakout(df, session_hour_utc=13, timeframe_minutes=15)
        assert len(result) == 1
        assert result.iloc[0]["direction"] == "long"


class TestBosNoLookaheadBias:
    def test_bos_does_not_use_future_candles(self) -> None:
        # Build a series where a swing high at candle i=5 is ONLY the rolling max
        # because of candles i=6..i+swing_lookback (future candles).
        # With center=False, candle i=5 should NOT be detected as a swing high
        # until we have enough trailing candles — and the signal should not fire
        # on the SAME candle that creates the new swing high.
        #
        # Pattern: flat 100s, then spike to 120 at i=5, then drop back
        # With center=True, i=5 is immediately a swing high (uses i=6..i+5)
        # With center=False, i=5 is only swing high after i=10 confirms (trailing)
        swing_lookback = 5
        rows = []
        for i in range(5):
            rows.append(_candle(_BASE_TIME + i, 100, 105, 95, 100))
        # spike at i=5
        rows.append(_candle(_BASE_TIME + 5, 110, 120, 108, 115))
        # drop back — these would "confirm" with center=True lookahead
        for i in range(6, 16):
            rows.append(_candle(_BASE_TIME + i, 100, 105, 95, 100))

        df = _make_ohlcv(rows)
        result = detect_market_structure(df, swing_lookback=swing_lookback)
        # With center=False, the spike candle (open_time=_BASE_TIME+5) must NOT
        # appear as a signal open_time — the signal can only fire AFTER trailing
        # candles confirm the swing.
        spike_time = _BASE_TIME + 5
        if not result.empty:
            assert spike_time not in result["open_time"].values


# ---------------------------------------------------------------------------
# Equal Highs / Equal Lows (EQH / EQL)
# ---------------------------------------------------------------------------


class TestDetectEqhEql:
    """Tests for detect_eqh_eql — EQH/EQL liquidity sweep detector."""

    # Minimum lookback + 1 signal candle needed: lookback=7 + 1 = 8 rows minimum
    _LOOKBACK = 10
    _MS = _MS_PER_HOUR

    def _base_df(self, n: int = 12, base: float = 100.0) -> pd.DataFrame:
        """Build n flat candles at base price (open=high=low=close=base)."""
        rows = [
            _candle(_BASE_TIME + i * self._MS, base, base, base, base) for i in range(n)
        ]
        return _make_ohlcv(rows)

    # ---- No-signal cases ----

    def test_returns_empty_when_not_enough_candles(self) -> None:
        df = _make_ohlcv([_candle(_BASE_TIME, 100, 105, 95, 100)])
        result = detect_eqh_eql(df, lookback=self._LOOKBACK)
        assert result.empty
        _assert_signal_columns(result)

    def test_no_signal_when_no_equal_highs(self) -> None:
        # All candles have strictly different highs → no EQH pair
        rows = [
            _candle(_BASE_TIME + i * self._MS, 100, 100 + i * 5, 90, 100)
            for i in range(self._LOOKBACK + 1)
        ]
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK)
        assert result.empty

    def test_no_signal_when_candle_breaks_out_above_eqh(self) -> None:
        # Signal candle wicks above EQH AND closes above it → breakout, not sweep
        rows = [
            _candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 118),
            _candle(_BASE_TIME + 1 * self._MS, 100, 120, 95, 117),  # equal high pair
        ]
        for i in range(2, self._LOOKBACK - 1):
            rows.append(_candle(_BASE_TIME + i * self._MS, 100, 110, 90, 100))
        # Signal candle: high > 120 and close > 120 → no sweep
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 118, 125, 115, 122))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.003)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    # ---- Short signal (EQH sweep) ----

    def test_short_signal_fires_when_candle_sweeps_eqh(self) -> None:
        # Two swing highs at ~120 in the lookback window.
        # Signal candle: high=121 (> 120), close=118 (< 120) → short.
        rows: list[dict[str, object]] = []
        # Candle 0: swing high at 120
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        # Candle 4: second swing high at 120
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        # Signal candle: wick above 120, closes below 120
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1

    def test_short_signal_open_time_is_signal_candle(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        sig_ts = _BASE_TIME + self._LOOKBACK * self._MS
        rows.append(_candle(sig_ts, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        assert result.iloc[0]["open_time"] == sig_ts

    def test_short_sl_price_is_max_post_eqh_deviation(self) -> None:
        # Use lookback=16 so there's room between EQH pair (indices 4+8) and a
        # post-EQH deviation candle (index 12, high=130).
        # Pre-EQH candle at index 0 (high=125) must be IGNORED — SL only scans
        # from the later EQH candle onwards.
        lookback = 16
        rows: list[dict[str, object]] = []
        rows.append(
            _candle(_BASE_TIME + 0 * self._MS, 118, 125, 105, 120)
        )  # pre-EQH, high=125 (must be ignored)
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115)
        )  # EQH swing high 1
        for i in range(5, 8):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + 8 * self._MS, 110, 120, 100, 115)
        )  # EQH swing high 2
        for i in range(9, 12):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + 12 * self._MS, 118, 130, 115, 126)
        )  # post-EQH deviation, high=130
        for i in range(13, lookback):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 112, 100, 111))
        rows.append(
            _candle(_BASE_TIME + lookback * self._MS, 115, 121, 110, 118)
        )  # signal candle
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=lookback, tolerance_pct=0.01)
        sl = float(result.iloc[0]["sl_price"])
        assert (
            sl == 130.0
        )  # post-EQH deviation, not pre-EQH (125) or signal candle (121)

    def test_short_context_contains_two_timestamps(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        ctx = str(result.iloc[0]["context"])
        assert ctx.startswith("EQH:")
        # Two timestamps separated by ·
        assert " · " in ctx

    # ---- Long signal (EQL sweep) ----

    def test_long_signal_fires_when_candle_sweeps_eql(self) -> None:
        # Two swing lows at ~80. Signal candle: low=79 (< 80), close=82 (> 80) → long.
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        # Signal candle: wick below 80, closes above 80
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 79, 82))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 1

    def test_long_sl_price_is_min_post_eql_deviation(self) -> None:
        # Use lookback=16 so there's room between EQL pair (indices 4+8) and a
        # post-EQL deviation candle (index 12, low=70).
        # Pre-EQL candle at index 0 (low=75) must be IGNORED — SL only scans
        # from the later EQL candle onwards.
        lookback = 16
        rows: list[dict[str, object]] = []
        rows.append(
            _candle(_BASE_TIME + 0 * self._MS, 75, 84, 75, 74)
        )  # pre-EQL, low=75 (must be ignored)
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + 4 * self._MS, 88, 92, 80, 85)
        )  # EQL swing low 1
        for i in range(5, 8):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + 8 * self._MS, 88, 92, 80, 85)
        )  # EQL swing low 2
        for i in range(9, 12):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + 12 * self._MS, 75, 84, 70, 74)
        )  # post-EQL deviation, low=70
        for i in range(13, lookback):
            rows.append(_candle(_BASE_TIME + i * self._MS, 88, 92, 85, 90))
        rows.append(
            _candle(_BASE_TIME + lookback * self._MS, 85, 90, 79, 82)
        )  # signal candle
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=lookback, tolerance_pct=0.01)
        sl = float(result.iloc[0]["sl_price"])
        assert sl == 70.0  # post-EQL deviation, not pre-EQL (75) or signal candle (79)

    def test_long_context_contains_two_timestamps(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 79, 82))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        ctx = str(result.iloc[0]["context"])
        assert ctx.startswith("EQL:")
        assert " · " in ctx

    def test_no_long_signal_when_candle_closes_below_eql(self) -> None:
        # Signal candle wicks below EQL AND closes below it → not a reversal
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 105, 80, 85))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 90, 95, 80, 85))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 90, 95, 85, 92))
        # Close = 78 < eql_level = 80 → no signal
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 85, 90, 75, 78))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        long_signals = result[result["direction"] == "long"]
        assert long_signals.empty

    def test_signal_columns_match(self) -> None:
        rows: list[dict[str, object]] = []
        rows.append(_candle(_BASE_TIME + 0 * self._MS, 100, 120, 95, 115))
        for i in range(1, 4):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + 4 * self._MS, 110, 120, 100, 115))
        for i in range(5, self._LOOKBACK):
            rows.append(_candle(_BASE_TIME + i * self._MS, 110, 115, 100, 112))
        rows.append(_candle(_BASE_TIME + self._LOOKBACK * self._MS, 115, 121, 110, 118))
        df = _make_ohlcv(rows)
        result = detect_eqh_eql(df, lookback=self._LOOKBACK, tolerance_pct=0.01)
        _assert_signal_columns(result)


class TestLiquiditySweepMinSize:
    def test_liquidity_sweep_ignores_micro_poke(self) -> None:
        # Rolling max high = 110; sweep candle high = 110.001 (0.0009% above) → no signal
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(20)]
        rows.append(_candle(_BASE_TIME + 20, 108, 110.001, 100, 105))
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20, min_sweep_pct=0.001)
        short_signals = result[result["direction"] == "short"]
        assert short_signals.empty

    def test_liquidity_sweep_fires_on_meaningful_sweep(self) -> None:
        # Rolling max high = 110; sweep candle high = 110.25 (0.23% above) → signal
        rows = [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(20)]
        rows.append(_candle(_BASE_TIME + 20, 108, 110.25, 100, 105))
        df = _make_ohlcv(rows)
        result = detect_liquidity_sweep(df, lookback=20, min_sweep_pct=0.001)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1


# ---------------------------------------------------------------------------
# Shared: empty DataFrame always returns correct columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        lambda: detect_wick_fills(pd.DataFrame()),
        lambda: detect_marubozu_retest(pd.DataFrame()),
        lambda: detect_orb_breakout(pd.DataFrame()),
        lambda: detect_liquidity_sweep(pd.DataFrame()),
        lambda: detect_fvg(pd.DataFrame()),
        lambda: detect_market_structure(pd.DataFrame()),
        lambda: detect_smt_divergence(pd.DataFrame(), pd.DataFrame()),
        lambda: detect_funding_extreme(pd.DataFrame(), pd.DataFrame()),
        lambda: detect_eqh_eql(pd.DataFrame()),
        lambda: detect_order_block(pd.DataFrame()),
    ],
)
def test_empty_input_returns_signal_columns(fn: Callable[[], pd.DataFrame]) -> None:
    result = fn()
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == SIGNAL_COLUMNS


# ---------------------------------------------------------------------------
# Order Block
# ---------------------------------------------------------------------------


class TestDetectOrderBlock:
    def test_returns_empty_on_too_few_candles(self) -> None:
        rows = [
            _candle(_BASE_TIME, 100, 110, 90, 105),
            _candle(_BASE_TIME + 1, 105, 112, 100, 108),
        ]
        result = detect_order_block(_make_ohlcv(rows))
        assert result.empty

    def test_signal_columns(self) -> None:
        result = detect_order_block(_make_ohlcv([]))
        assert list(result.columns) == SIGNAL_COLUMNS

    def test_detects_bearish_ob_short_signal(self) -> None:
        # Bearish OB setup:
        #   candle 0 (OB): bullish, open=100, close=110
        #   candle 1 (displacement): close=94 < ob_low(100) * (1 - 0.005=99.5) → bearish disp
        #   candle 2 (retest): high=108 >= ob_zone_bot(100), low=101 <= ob_zone_top(110),
        #                      close=103 < ob_zone_top(110) → short signal fires
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(
                _BASE_TIME + 1, 109, 110, 88, 93
            ),  # displacement: close 93 < 99*0.995=98.5
            _candle(
                _BASE_TIME + 2, 95, 108, 101, 103
            ),  # retest enters [100, 110], close<110
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1
        assert short_signals.iloc[0]["sl_price"] == 112.0
        assert "ob_short" in short_signals.iloc[0]["reason"]

    def test_detects_bullish_ob_long_signal(self) -> None:
        # Bullish OB setup:
        #   candle 0 (OB): bearish, open=110, close=100
        #   candle 1 (displacement): close=117 > ob_high(112) * (1 + 0.005=112.56) → bullish disp
        #   candle 2 (retest): low=101 <= ob_zone_top(110), high=109 >= ob_zone_bot(100),
        #                      close=106 > ob_zone_bot(100) → long signal fires
        rows = [
            _candle(_BASE_TIME + 0, 110, 112, 99, 100),  # OB: bearish
            _candle(
                _BASE_TIME + 1, 101, 118, 100, 117
            ),  # displacement: close 117 > 112*1.005
            _candle(
                _BASE_TIME + 2, 115, 109, 101, 106
            ),  # retest enters [100, 110], close>100
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        long_signals = result[result["direction"] == "long"]
        assert len(long_signals) == 1
        assert long_signals.iloc[0]["sl_price"] == 99.0
        assert "ob_long" in long_signals.iloc[0]["reason"]

    def test_no_signal_when_displacement_insufficient(self) -> None:
        # Displacement candle doesn't close far enough below ob_low
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(
                _BASE_TIME + 1, 109, 110, 98, 99
            ),  # close=99 ≥ 99*0.995=98.5 → no disp
            _candle(_BASE_TIME + 2, 95, 108, 95, 103),  # potential retest (won't fire)
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        assert result[result["direction"] == "short"].empty

    def test_no_signal_when_retest_close_breaks_zone(self) -> None:
        # Retest candle enters OB zone but closes above the zone top → not a valid retest
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
            _candle(
                _BASE_TIME + 2, 95, 115, 101, 112
            ),  # enters zone but close=112 >= zone_top(110)
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        assert result[result["direction"] == "short"].empty

    def test_one_signal_per_ob(self) -> None:
        # Multiple candles retest the OB — only first one fires
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),  # OB: bullish
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
            _candle(_BASE_TIME + 2, 95, 108, 101, 103),  # first retest → signal
            _candle(
                _BASE_TIME + 3, 103, 109, 102, 104
            ),  # second entry into zone → no extra signal
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        short_signals = result[result["direction"] == "short"]
        assert len(short_signals) == 1
        assert int(short_signals.iloc[0]["open_time"]) == _BASE_TIME + 2

    def test_lookback_limits_ob_scan_window(self) -> None:
        # Place OB at candle 0, but lookback=2 so only last 2 candles scanned for OBs
        # The OB at index 0 should be excluded.
        rows = [
            _candle(
                _BASE_TIME + 0, 100, 112, 99, 110
            ),  # OB: bullish — outside lookback
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),  # displacement
            _candle(_BASE_TIME + 2, 95, 108, 101, 103),  # retest
        ]
        df = _make_ohlcv(rows)
        # lookback=1: start_idx = max(0, 3-1)=2, so only candle at index 2 checked as OB
        result = detect_order_block(df, lookback=1, displacement_pct=0.005)
        assert result.empty

    def test_context_contains_ob_type_and_time(self) -> None:
        rows = [
            _candle(_BASE_TIME + 0, 100, 112, 99, 110),
            _candle(_BASE_TIME + 1, 109, 110, 88, 93),
            _candle(_BASE_TIME + 2, 95, 108, 101, 103),
        ]
        df = _make_ohlcv(rows)
        result = detect_order_block(df, lookback=10, displacement_pct=0.005)
        assert not result.empty
        assert "Bearish OB" in result.iloc[0]["context"]
