"""Tests for analytics/backtest_runner.py — format_sweep_table + ADR pre-filter."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from analytics.backtest_config import BacktestSweepConfig, StrategyOverride
from analytics.backtest_lib import BacktestResult, Trade
from analytics.backtest_runner import (
    _apply_legacy_adr_pre_filter,
    format_sweep_table,
)


def _make_result(
    symbol: str,
    timeframe: str,
    strategy: str,
    wins: int,
    losses: int,
    avg_r: float,
) -> BacktestResult:
    """Build a BacktestResult stub with the given win/loss counts and avg_r."""
    trades: list[Trade] = []
    # Add wins
    for _ in range(wins):
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
        )
        trades.append(t)
    # Add losses with pnl_r calibrated so avg_r comes out to avg_r
    # avg_r = (wins * win_r + losses * loss_r) / total
    # win_r = 2.0 (entry=100, sl=98, tp=104 → risk=2, gain=4 → 2R)
    # solve for loss_r: avg_r * total = wins * 2 + losses * loss_r
    total = wins + losses
    win_r = 2.0
    loss_r = (avg_r * total - wins * win_r) / losses if losses > 0 else -1.0

    for _ in range(losses):
        # entry=100, sl=98 → risk=2; to get loss_r we want exit such that (entry-exit)/risk = |loss_r|
        exit_price = 100.0 - abs(loss_r) * 2.0
        t = Trade(
            signal_time=0,
            entry_time=1,
            entry_price=100.0,
            direction="long",
            sl_price=98.0,
            tp_price=104.0,
            exit_price=exit_price,
            exit_time=2,
            outcome="loss",
        )
        trades.append(t)

    result = BacktestResult(symbol=symbol, timeframe=timeframe, strategy=strategy)
    result.trades = trades
    return result


class TestFormatSweepTable:
    def test_basic_output_has_header_and_rows(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 30, 18, 0.8),
            _make_result("ETHUSDT", "1d", "bos", 25, 20, 0.5),
            _make_result("SOLUSDT", "1h", "liquidity_sweep", 22, 18, 0.3),
        ]
        table = format_sweep_table(results, min_trades=10)
        assert "Symbol" in table
        assert "BTCUSDT" in table
        assert "ETHUSDT" in table
        assert "SOLUSDT" in table
        assert "fvg" in table

    def test_sorted_by_avg_r_descending(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 20, 20, 0.3),
            _make_result("ETHUSDT", "4h", "bos", 30, 10, 1.2),
            _make_result("SOLUSDT", "4h", "liquidity_sweep", 25, 15, 0.7),
        ]
        table = format_sweep_table(results, min_trades=5)
        eth_pos = table.index("ETHUSDT")
        sol_pos = table.index("SOLUSDT")
        btc_pos = table.index("BTCUSDT")
        assert eth_pos < sol_pos < btc_pos

    def test_min_trades_filter_excludes_low_count(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 15, 10, 0.9),  # 25 closed
            _make_result("ETHUSDT", "4h", "bos", 5, 3, 0.4),  # 8 closed — below min
        ]
        table = format_sweep_table(results, min_trades=20)
        assert "BTCUSDT" in table
        assert "ETHUSDT" not in table
        assert "Hidden: 1" in table

    def test_all_below_min_trades_shows_no_results(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 3, 2, 0.5),
        ]
        table = format_sweep_table(results, min_trades=20)
        assert "No results" in table

    def test_empty_results_list(self) -> None:
        table = format_sweep_table([], min_trades=20)
        assert "No results" in table

    def test_footer_hidden_count_correct(self) -> None:
        results = [
            _make_result("BTCUSDT", "4h", "fvg", 25, 15, 0.8),  # 40 closed
            _make_result("ETHUSDT", "4h", "bos", 2, 1, 0.2),  # 3 closed
            _make_result("SOLUSDT", "4h", "wick_fill", 1, 1, 0.1),  # 2 closed
        ]
        table = format_sweep_table(results, min_trades=20)
        assert "Hidden: 2" in table

    def test_win_pct_and_avg_r_appear_in_row(self) -> None:
        results = [_make_result("BTCUSDT", "4h", "fvg", 20, 20, 0.5)]
        table = format_sweep_table(results, min_trades=5)
        assert "50.0%" in table
        assert "+0.50R" in table


def _signals(directions: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": list(range(len(directions))),
            "direction": directions,
        }
    )


class TestApplyLegacyAdrPreFilter:
    """Per-direction split behaviour for the non-live-parity ADR pre-filter."""

    def test_both_exempt_returns_signals_untouched(self) -> None:
        cfg = BacktestSweepConfig(
            adr_suppress_threshold=0.7,
            strategy_params={
                "bos": StrategyOverride(adr_exempt_long=True, adr_exempt_short=True)
            },
        )
        sigs = _signals(["long", "short", "long"])
        ohlcv = pd.DataFrame()
        with patch("analytics.backtest_runner._filter_signals_by_adr") as mock_filter:
            out = _apply_legacy_adr_pre_filter(cfg, "bos", ohlcv, sigs)
        mock_filter.assert_not_called()
        pd.testing.assert_frame_equal(out, sigs)

    def test_neither_exempt_filters_full_set(self) -> None:
        cfg = BacktestSweepConfig(adr_suppress_threshold=0.7)
        sigs = _signals(["long", "short"])
        ohlcv = pd.DataFrame()
        sentinel = pd.DataFrame({"open_time": [99], "direction": ["short"]})
        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            return_value=sentinel,
        ) as mock_filter:
            out = _apply_legacy_adr_pre_filter(cfg, "bos", ohlcv, sigs)
        mock_filter.assert_called_once()
        # Full DataFrame went to _filter_signals_by_adr (no split).
        _, called_sigs, called_threshold = mock_filter.call_args.args
        pd.testing.assert_frame_equal(called_sigs, sigs)
        assert called_threshold == 0.7
        pd.testing.assert_frame_equal(out, sentinel)

    def test_short_exempt_only_long_goes_through_filter(self) -> None:
        cfg = BacktestSweepConfig(
            adr_suppress_threshold=0.7,
            strategy_params={"bos": StrategyOverride(adr_exempt_short=True)},
        )
        sigs = _signals(["long", "short", "long", "short"])
        ohlcv = pd.DataFrame()
        # Mock returns the input unchanged so the concat path is exercised.
        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            side_effect=lambda _o, s, _t: s,
        ) as mock_filter:
            out = _apply_legacy_adr_pre_filter(cfg, "bos", ohlcv, sigs)
        # Only the long slice was passed to _filter_signals_by_adr.
        _, called_sigs, _ = mock_filter.call_args.args
        assert list(called_sigs["direction"]) == ["long", "long"]
        # Result is ordered by open_time and round-trips every input row.
        assert list(out["open_time"]) == [0, 1, 2, 3]
        assert list(out["direction"]) == ["long", "short", "long", "short"]

    def test_long_exempt_only_short_goes_through_filter(self) -> None:
        cfg = BacktestSweepConfig(
            adr_suppress_threshold=0.7,
            strategy_params={"bos": StrategyOverride(adr_exempt_long=True)},
        )
        sigs = _signals(["short", "long", "short"])
        ohlcv = pd.DataFrame()
        # Drop all short rows to verify the concat preserves the exempt long.
        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            side_effect=lambda _o, s, _t: s.iloc[:0],
        ):
            out = _apply_legacy_adr_pre_filter(cfg, "bos", ohlcv, sigs)
        assert list(out["direction"]) == ["long"]
        assert list(out["open_time"]) == [1]

    def test_per_direction_beats_strategy_wide(self) -> None:
        # adr_exempt=True (strategy-wide) but adr_exempt_short=False — short
        # signals should still be filtered.
        cfg = BacktestSweepConfig(
            adr_suppress_threshold=0.7,
            strategy_params={
                "bos": StrategyOverride(adr_exempt=True, adr_exempt_short=False)
            },
        )
        sigs = _signals(["long", "short"])
        ohlcv = pd.DataFrame()
        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            side_effect=lambda _o, s, _t: s,
        ) as mock_filter:
            out = _apply_legacy_adr_pre_filter(cfg, "bos", ohlcv, sigs)
        _, called_sigs, _ = mock_filter.call_args.args
        assert list(called_sigs["direction"]) == ["short"]
        assert list(out["direction"]) == ["long", "short"]
