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
from tests.conftest import _candle, _make_ohlcv

_BASE_TIME = 1_700_000_000_000


def _make_signals(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open_time", "direction", "reason"])


def _make_signals_with_sl(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Signals DataFrame that includes a per-row sl_price column."""
    return pd.DataFrame(rows, columns=["open_time", "direction", "reason", "sl_price"])


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
# BacktestResult — long/short split properties
# ---------------------------------------------------------------------------


class TestBacktestResultDirectionSplit:
    def _make_mixed_result(self) -> BacktestResult:
        """3 longs (2W 1L) + 2 shorts (1W 1L) + 1 open long."""

        def _trade(i: int, direction: str, outcome: str) -> Trade:
            ep = 100.0
            if direction == "long":
                sl, tp = 98.0, 104.0  # risk=2, TP=+2R
                exit_p = tp if outcome == "win" else sl if outcome == "loss" else None
            else:
                sl, tp = 102.0, 96.0  # short: sl above entry, tp below; risk=2, TP=+2R
                exit_p = tp if outcome == "win" else sl if outcome == "loss" else None
            return Trade(
                signal_time=i,
                entry_time=i + 1,
                entry_price=ep,
                direction=direction,
                sl_price=sl,
                tp_price=tp,
                exit_time=i + 2 if outcome != "open" else None,
                exit_price=exit_p,
                outcome=outcome,
            )

        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = [
            _trade(0, "long", "win"),
            _trade(1, "long", "win"),
            _trade(2, "long", "loss"),
            _trade(3, "long", "open"),  # excluded from closed
            _trade(4, "short", "win"),
            _trade(5, "short", "loss"),
        ]
        return result

    def test_long_closed_trades_count(self) -> None:
        result = self._make_mixed_result()
        assert len(result.long_closed_trades) == 3

    def test_short_closed_trades_count(self) -> None:
        result = self._make_mixed_result()
        assert len(result.short_closed_trades) == 2

    def test_long_win_count(self) -> None:
        result = self._make_mixed_result()
        assert result.long_win_count == 2

    def test_long_win_rate(self) -> None:
        result = self._make_mixed_result()
        assert result.long_win_rate == pytest.approx(2 / 3)

    def test_short_win_count(self) -> None:
        result = self._make_mixed_result()
        assert result.short_win_count == 1

    def test_short_win_rate(self) -> None:
        result = self._make_mixed_result()
        assert result.short_win_rate == pytest.approx(0.5)

    def test_long_avg_r(self) -> None:
        result = self._make_mixed_result()
        # 2 wins (+2R each) + 1 loss (−1R) = avg 1R
        assert result.long_avg_r == pytest.approx(1.0)

    def test_short_avg_r(self) -> None:
        result = self._make_mixed_result()
        # 1 win (+2R) + 1 loss (−1R) = avg 0.5R
        assert result.short_avg_r == pytest.approx(0.5)

    def test_long_win_rate_none_when_no_longs(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = [
            Trade(
                signal_time=0,
                entry_time=1,
                entry_price=100.0,
                direction="short",
                sl_price=102.0,
                tp_price=96.0,
                exit_time=2,
                exit_price=96.0,
                outcome="win",
            )
        ]
        assert result.long_win_rate is None
        assert result.long_avg_r is None

    def test_short_win_rate_none_when_no_shorts(self) -> None:
        result = BacktestResult(symbol="BTCUSDT", timeframe="4h", strategy="fvg")
        result.trades = [
            Trade(
                signal_time=0,
                entry_time=1,
                entry_price=100.0,
                direction="long",
                sl_price=98.0,
                tp_price=104.0,
                exit_time=2,
                exit_price=104.0,
                outcome="win",
            )
        ]
        assert result.short_win_rate is None
        assert result.short_avg_r is None


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
# run_backtest — per-signal structural sl_price
# ---------------------------------------------------------------------------


class TestRunBacktestStructuralSL:
    """Tests for the per-signal sl_price path in run_backtest."""

    def test_long_sl_price_used_directly(self) -> None:
        """When signals have sl_price, long SL triggers at the structural level."""
        # Entry at candle 1 open=100; structural SL at 95 (not 98 from 2% sl_pct)
        # Candle 2: low=94 → hits structural SL at 95
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 97, 101),  # entry open=100
                _candle(_BASE_TIME + 2, 98, 99, 94, 96),  # low=94 ≤ sl=95 → loss
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 95.0,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02)
        assert len(result.trades) == 1
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].sl_price == pytest.approx(95.0)
        assert result.trades[0].exit_price == pytest.approx(95.0)

    def test_long_sl_price_tp_computed_from_risk_distance(self) -> None:
        """For long with structural SL, TP = entry + tp_r * abs(entry - sl_price)."""
        # Entry open=100, structural SL=95 → risk=5 → TP at 100 + 2*5 = 110
        # Candle 2: high=111 → hits TP at 110
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry open=100
                _candle(_BASE_TIME + 2, 101, 111, 100, 110),  # high=111 ≥ tp=110 → win
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 95.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "win"
        assert result.trades[0].tp_price == pytest.approx(110.0)
        assert result.trades[0].exit_price == pytest.approx(110.0)

    def test_short_sl_price_used_directly(self) -> None:
        """When signals have sl_price, short SL triggers at the structural level."""
        # Entry at candle 1 open=100; structural SL at 106 (not 102 from 2% sl_pct)
        # Candle 2: high=107 → hits structural SL at 106
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 101, 98, 99),  # entry open=100
                _candle(_BASE_TIME + 2, 101, 107, 99, 106),  # high=107 ≥ sl=106 → loss
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "short",
                    "reason": "test",
                    "sl_price": 106.0,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02)
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].sl_price == pytest.approx(106.0)
        assert result.trades[0].exit_price == pytest.approx(106.0)

    def test_short_sl_price_tp_computed_from_risk_distance(self) -> None:
        """For short with structural SL, TP = entry - tp_r * abs(entry - sl_price)."""
        # Entry open=100, structural SL=106 → risk=6 → TP at 100 - 2*6 = 88
        # Candle 2: low=87 → hits TP at 88
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 101, 98, 99),  # entry open=100
                _candle(_BASE_TIME + 2, 95, 96, 87, 88),  # low=87 ≤ tp=88 → win
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "short",
                    "reason": "test",
                    "sl_price": 106.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0
        )
        assert result.trades[0].outcome == "win"
        assert result.trades[0].tp_price == pytest.approx(88.0)
        assert result.trades[0].exit_price == pytest.approx(88.0)

    def test_no_sl_price_column_falls_back_to_sl_pct(self) -> None:
        """Signals without sl_price column use sl_pct fallback (backward compat)."""
        # Entry open=100, sl_pct=0.02 → SL at 98
        # Candle 2: low=96 ≤ 98 → loss
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),
                _candle(_BASE_TIME + 1, 100, 103, 97, 101),
                _candle(_BASE_TIME + 2, 99, 100, 96, 97),
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02)
        assert result.trades[0].outcome == "loss"
        assert result.trades[0].sl_price == pytest.approx(98.0)


# ---------------------------------------------------------------------------
# min_sl_pct — structural SL widening
# ---------------------------------------------------------------------------


class TestMinSlPct:
    def test_tight_sl_widened_for_long(self) -> None:
        """min_sl_pct widens a structural SL that lands too close to entry."""
        # Entry open=100, structural sl=99.9 (0.1% away), min_sl_pct=0.01 → SL at 99
        # Candle 2: low=98 → hits widened SL at 99 → loss
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 99, 102),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),
                _candle(_BASE_TIME + 2, 99, 100, 97, 98),
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 99.9,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", min_sl_pct=0.01)
        assert result.trades[0].sl_price == pytest.approx(99.0)
        assert result.trades[0].outcome == "loss"

    def test_tight_sl_widened_for_short(self) -> None:
        """min_sl_pct widens a structural SL above entry for a short."""
        # Entry open=100, structural sl=100.1 (0.1% above), min_sl_pct=0.01 → SL at 101
        # Candle 2: high=102 → hits widened SL at 101 → loss
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 99, 102),
                _candle(_BASE_TIME + 1, 100, 101, 99, 100),
                _candle(_BASE_TIME + 2, 99, 102, 97, 98),
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "short",
                    "reason": "test",
                    "sl_price": 100.1,
                }
            ]
        )
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", min_sl_pct=0.01)
        assert result.trades[0].sl_price == pytest.approx(101.0)
        assert result.trades[0].outcome == "loss"

    def test_wide_sl_not_affected(self) -> None:
        """Structural SL already wider than min_sl_pct is left unchanged."""
        # Entry=100, sl=95 (5% away), min_sl_pct=0.01 → sl stays at 95
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 99, 102),
                _candle(_BASE_TIME + 1, 100, 110, 90, 108),
            ]
        )
        signals = _make_signals_with_sl(
            [
                {
                    "open_time": _BASE_TIME + 0,
                    "direction": "long",
                    "reason": "test",
                    "sl_price": 95.0,
                }
            ]
        )
        result = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", tp_r=2.0, min_sl_pct=0.01
        )
        assert result.trades[0].sl_price == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# fee_pct — Trade.pnl_r with fees
# ---------------------------------------------------------------------------


class TestTradePnlRWithFees:
    def test_fee_pct_zero_unchanged(self) -> None:
        """fee_pct=0.0 must reproduce original pnl_r behaviour (win = +2R)."""
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
            fee_pct=0.0,
        )
        assert t.pnl_r == pytest.approx(2.0)

    def test_fee_pct_reduces_winning_trade_pnl(self) -> None:
        """With fees, a winning trade's pnl_r is lower than the fee-free case."""
        # entry=100, sl=98 → risk=2, exit=104 (raw +2R)
        # fee_drag = 2 * 0.0005 * 100 / 2 = 0.05R
        # net pnl_r = 2.0 - 0.05 = 1.95
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=104.0,
            exit_time=2,
            outcome="win",
            fee_pct=0.0005,
        )
        assert t.pnl_r == pytest.approx(1.95)

    def test_fee_pct_increases_losing_trade_loss(self) -> None:
        """With fees, a losing trade loses more than -1R."""
        # entry=100, sl=98 → risk=2, exit=98 (raw -1R)
        # fee_drag = 2 * 0.0005 * 100 / 2 = 0.05R
        # net pnl_r = -1.0 - 0.05 = -1.05
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
            fee_pct=0.0005,
        )
        assert t.pnl_r == pytest.approx(-1.05)

    def test_run_backtest_fee_pct_stored_on_result(self) -> None:
        """fee_pct passed to run_backtest is stored on BacktestResult."""
        ohlcv = _make_ohlcv(
            [_candle(_BASE_TIME + i, 100, 110, 90, 100) for i in range(5)]
        )
        signals = _make_signals([])
        result = run_backtest(ohlcv, signals, "BTCUSDT", "4h", "fvg", fee_pct=0.0005)
        assert result.fee_pct == pytest.approx(0.0005)

    def test_run_backtest_with_fee_pct_lowers_avg_r(self) -> None:
        """A winning trade's avg_r is lower when fee_pct > 0 vs fee_pct = 0."""
        ohlcv = _make_ohlcv(
            [
                _candle(_BASE_TIME + 0, 100, 105, 95, 102),  # signal candle
                _candle(_BASE_TIME + 1, 100, 103, 99, 101),  # entry open=100
                _candle(_BASE_TIME + 2, 101, 106, 99, 105),  # high=106 ≥ tp=104 → win
            ]
        )
        signals = _make_signals(
            [{"open_time": _BASE_TIME + 0, "direction": "long", "reason": "test"}]
        )
        no_fee = run_backtest(
            ohlcv, signals, "BTCUSDT", "4h", "fvg", sl_pct=0.02, tp_r=2.0, fee_pct=0.0
        )
        with_fee = run_backtest(
            ohlcv,
            signals,
            "BTCUSDT",
            "4h",
            "fvg",
            sl_pct=0.02,
            tp_r=2.0,
            fee_pct=0.0005,
        )
        assert no_fee.avg_r == pytest.approx(2.0)
        assert with_fee.avg_r is not None
        assert with_fee.avg_r < no_fee.avg_r


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
