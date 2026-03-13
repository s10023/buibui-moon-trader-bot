"""Tests for analytics/indicators_lib.py."""

import datetime
from collections.abc import Callable

import pandas as pd
import pytest

from analytics.indicators_lib import (
    SIGNAL_COLUMNS,
    detect_funding_extreme,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_orb_breakout,
    detect_smt_divergence,
    detect_wick_fills,
    seasonality_stats,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MS_PER_HOUR = 3_600_000
_BASE_TIME = 1_700_000_000_000


def _candle(
    open_time: int,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _make_ohlcv(rows: list[dict[str, object]]) -> pd.DataFrame:
    cols = [
        "symbol",
        "timeframe",
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    return pd.DataFrame(rows, columns=cols)


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
        # Bearish FVG: candle[0].low=100, candle[2].high=95 → gap [95, 100]
        # Fill candle: high=97 enters gap
        rows = [
            _candle(_BASE_TIME + 0, 105, 108, 100, 102),
            _candle(_BASE_TIME + 1, 98, 99, 92, 93),  # impulse candle
            _candle(_BASE_TIME + 2, 92, 95, 88, 90),  # nxt.high=95 < prev.low=100
            _candle(_BASE_TIME + 3, 91, 97, 88, 92),  # high=97 ≥ gap_bot=95
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
    ],
)
def test_empty_input_returns_signal_columns(fn: Callable[[], pd.DataFrame]) -> None:
    result = fn()
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == SIGNAL_COLUMNS
