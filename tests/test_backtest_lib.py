"""Tests for analytics/backtest_lib.py."""

import pandas as pd
import pytest

from analytics.backtest_lib import (
    BacktestResult,
    Trade,
    format_result,
    format_seasonality,
    run_backtest,
)

_BASE_TIME = 1_700_000_000_000


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


def _candle(
    open_time: int,
    open: float,
    high: float,
    low: float,
    close: float,
) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "open_time": open_time,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
    }


def _make_signals(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open_time", "direction", "reason"])


# ---------------------------------------------------------------------------
# Trade.pnl_r property
# ---------------------------------------------------------------------------


class TestTradePnlR:
    def test_long_win_returns_positive(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,  # risk = 2
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
        )
        assert t.pnl_r == pytest.approx(2.0)

    def test_long_loss_returns_negative(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=98.0,
            exit_time=2,
            outcome="loss",
        )
        assert t.pnl_r == pytest.approx(-1.0)

    def test_short_win_returns_positive(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="short",
            sl_price=102.0,  # risk = 2
            tp_price=96.0,
            exit_price=96.0,
            exit_time=2,
            outcome="win",
        )
        assert t.pnl_r == pytest.approx(2.0)

    def test_open_trade_returns_none(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
        )
        assert t.pnl_r is None

    def test_zero_risk_returns_none(self) -> None:
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=100.0,  # sl == entry → zero risk
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
        )
        assert t.pnl_r is None


# ---------------------------------------------------------------------------
# BacktestResult properties
# ---------------------------------------------------------------------------


class TestBacktestResult:
    def _make_result(self, outcomes: list[str]) -> BacktestResult:
        trades = []
        for i, outcome in enumerate(outcomes):
            ep = 100.0
            sl = 98.0
            tp = 104.0
            exit_p = tp if outcome == "win" else sl if outcome == "loss" else None
            trades.append(
                Trade(
                    signal_time=i,
                    entry_time=i + 1,
                    entry_price=ep,
                    direction="long",
                    sl_price=sl,
                    tp_price=tp,
                    exit_time=i + 2 if outcome != "open" else None,
                    exit_price=exit_p,
                    outcome=outcome,
                )
            )
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = trades
        return result

    def test_win_rate_two_wins_one_loss(self) -> None:
        result = self._make_result(["win", "win", "loss"])
        assert result.win_rate == pytest.approx(2 / 3)

    def test_win_rate_zero_on_no_closed_trades(self) -> None:
        result = self._make_result(["open", "open"])
        assert result.win_rate == 0.0

    def test_avg_r_with_wins_and_losses(self) -> None:
        result = self._make_result(["win", "loss"])
        # win = +2R, loss = -1R → avg = 0.5
        assert result.avg_r == pytest.approx(0.5)

    def test_total_r(self) -> None:
        result = self._make_result(["win", "win", "loss"])
        # 2R + 2R - 1R = 3R
        assert result.total_r == pytest.approx(3.0)

    def test_max_drawdown_r_all_wins(self) -> None:
        result = self._make_result(["win", "win", "win"])
        assert result.max_drawdown_r == 0.0

    def test_max_drawdown_r_with_losses(self) -> None:
        result = self._make_result(["win", "loss", "loss", "win"])
        # cumR: 2, 1, 0, 2 → peak=2, min after peak=0 → dd=2
        assert result.max_drawdown_r == pytest.approx(2.0)

    def test_closed_trades_excludes_open(self) -> None:
        result = self._make_result(["win", "open", "loss"])
        assert len(result.closed_trades) == 2

    def test_win_loss_counts(self) -> None:
        result = self._make_result(["win", "win", "loss", "open"])
        assert result.win_count == 2
        assert result.loss_count == 1


# ---------------------------------------------------------------------------
# run_backtest
# ---------------------------------------------------------------------------


class TestRunBacktest:
    def test_empty_signals_returns_empty_result(self) -> None:
        ohlcv = _make_ohlcv(
            [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(10)]
        )
        signals = _make_signals([])
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")
        assert len(result.trades) == 0

    def test_long_trade_hits_tp(self) -> None:
        # Signal on candle 0, entry on candle 1 at open=100
        # sl=2% → 98, tp=2R → 104
        # Candle 1: safe (low=99 > sl=98, high=103 < tp=104)
        # Candle 2: high=106 → hits TP
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry at open=100; safe
                _candle(_BASE_TIME + 2, 101, 106, 99, 105),  # high=106 ≥ tp=104 → win
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert len(result.trades) == 1
        assert result.trades[0].outcome == "win"
        assert result.trades[0].exit_price == pytest.approx(104.0)

    def test_long_trade_hits_sl(self) -> None:
        # Signal on candle 0, entry on candle 1 at open=100
        # sl=2% → 98, tp=2R → 104
        # Candle 2: low=96 → hits SL
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 103, 97, 101),  # entry
                _candle(_BASE_TIME + 2, 99, 100, 96, 97),  # low=96 ≤ sl=98 → loss
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].exit_price == pytest.approx(98.0)

    def test_short_trade_hits_tp(self) -> None:
        # Entry at open=100, sl=2% → 102, tp=2R → 96
        # Candle 2: low=95 → hits TP
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 101, 97, 98),  # entry
                _candle(_BASE_TIME + 2, 99, 100, 95, 96),  # low=95 ≤ tp=96 → win
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "win"
        assert result.trades[0].exit_price == pytest.approx(96.0)

    def test_short_trade_hits_sl(self) -> None:
        # Entry at open=100, sl=2% → 102
        # Candle 2: high=104 → hits SL
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 101, 97, 98),  # entry
                _candle(_BASE_TIME + 2, 100, 104, 96, 103),  # high=104 ≥ sl=102 → loss
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "short", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "loss"

    def test_trade_open_at_end_of_data(self) -> None:
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),  # entry, no exit
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "open"

    def test_signal_at_last_candle_is_skipped(self) -> None:
        ohlcv = _make_ohlcv([_candle(_BASE_TIME + 0, 100, 105, 95, 102)])
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")
        assert len(result.trades) == 0

    def test_result_metadata(self) -> None:
        ohlcv = _make_ohlcv(
            [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        )
        signals = _make_signals([])
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg")
        assert result.symbol == "BTCUSDT"
        assert result.timeframe == "4h"
        assert result.strategy == "fvg"


# ---------------------------------------------------------------------------
# format_result
# ---------------------------------------------------------------------------


class TestFormatResult:
    def test_contains_symbol_and_strategy(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        output = format_result(result)
        assert "BTCUSDT" in output
        assert "fvg" in output

    def test_shows_zero_trades(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        output = format_result(result)
        assert "0" in output


# ---------------------------------------------------------------------------
# format_seasonality
# ---------------------------------------------------------------------------


class TestFormatSeasonality:
    def test_empty_stats_returns_message(self) -> None:
        output = format_seasonality(pd.DataFrame())
        assert "No seasonality data" in output

    def test_contains_day_of_week_section(self) -> None:
        rows = [
            {
                "period_type": "day_of_week",
                "period_value": i,
                "avg_return_pct": 0.5,
                "win_rate": 0.6,
                "count": 10,
            }
            for i in range(7)
        ]
        stats = pd.DataFrame(rows)
        output = format_seasonality(stats)
        assert "Day Of Week" in output
        assert "Mon" in output
