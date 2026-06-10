"""Tests for analytics/backtest_runner.py — format_sweep_table + ADR pre-filter."""

from __future__ import annotations

from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.backtest_config import BacktestSweepConfig, StrategyOverride
from analytics.backtest_lib import BacktestResult, Trade
from analytics.backtest_runner import (
    _apply_legacy_adr_pre_filter,
    _apply_strategy_timeframes_directional_filter,
    _build_funding_series_by_symbol,
    _collect_sweep_results,
    format_sweep_table,
)
from analytics.data_store import init_schema, upsert_funding_rates, upsert_ohlcv


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
            out = _apply_legacy_adr_pre_filter(cfg, "bos", "1h", ohlcv, sigs)
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
            out = _apply_legacy_adr_pre_filter(cfg, "bos", "1h", ohlcv, sigs)
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
            out = _apply_legacy_adr_pre_filter(cfg, "bos", "1h", ohlcv, sigs)
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
            out = _apply_legacy_adr_pre_filter(cfg, "bos", "1h", ohlcv, sigs)
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
            out = _apply_legacy_adr_pre_filter(cfg, "bos", "1h", ohlcv, sigs)
        _, called_sigs, _ = mock_filter.call_args.args
        assert list(called_sigs["direction"]) == ["short"]
        assert list(out["direction"]) == ["long", "short"]

    def test_per_tf_direction_only_exempts_named_tf(self) -> None:
        # adr_exempt_short_per_tf = {"15m": true} exempts shorts on 15m only;
        # the same cfg leaves shorts non-exempt on 1h.
        cfg = BacktestSweepConfig(
            adr_suppress_threshold=0.7,
            strategy_params={
                "bos": StrategyOverride(adr_exempt_short_per_tf={"15m": True})
            },
        )
        sigs = _signals(["long", "short", "long", "short"])
        ohlcv = pd.DataFrame()
        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            side_effect=lambda _o, s, _t: s,
        ) as mock_filter_15m:
            out_15m = _apply_legacy_adr_pre_filter(cfg, "bos", "15m", ohlcv, sigs)
        # 15m: shorts are exempt → only longs go through filter.
        _, called_15m, _ = mock_filter_15m.call_args.args
        assert list(called_15m["direction"]) == ["long", "long"]
        assert list(out_15m["open_time"]) == [0, 1, 2, 3]

        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            side_effect=lambda _o, s, _t: s,
        ) as mock_filter_1h:
            _apply_legacy_adr_pre_filter(cfg, "bos", "1h", ohlcv, sigs)
        # 1h: neither side exempt → full DataFrame filtered.
        _, called_1h, _ = mock_filter_1h.call_args.args
        pd.testing.assert_frame_equal(called_1h, sigs)

    def test_per_tf_direction_beats_directional_and_strategy_wide(self) -> None:
        # adr_exempt=True + adr_exempt_long=True + adr_exempt_long_per_tf["15m"]=False.
        # On 15m the per-tf-direction wins → longs are NOT exempt.
        cfg = BacktestSweepConfig(
            adr_suppress_threshold=0.7,
            strategy_params={
                "bos": StrategyOverride(
                    adr_exempt=True,
                    adr_exempt_long=True,
                    adr_exempt_long_per_tf={"15m": False},
                )
            },
        )
        sigs = _signals(["long", "short"])
        ohlcv = pd.DataFrame()
        with patch(
            "analytics.backtest_runner._filter_signals_by_adr",
            side_effect=lambda _o, s, _t: s,
        ) as mock_filter:
            out = _apply_legacy_adr_pre_filter(cfg, "bos", "15m", ohlcv, sigs)
        _, called, _ = mock_filter.call_args.args
        assert list(called["direction"]) == ["long"]
        assert list(out["direction"]) == ["long", "short"]


class TestApplyStrategyTimeframesDirectionalFilter:
    """Per-direction strategy_timeframes mask (Bucket C — backtest-side parity)."""

    def test_no_overrides_is_noop(self) -> None:
        cfg = BacktestSweepConfig()
        sigs = _signals(["long", "short", "long"])
        out = _apply_strategy_timeframes_directional_filter(
            cfg, "inside_bar", "4h", sigs
        )
        pd.testing.assert_frame_equal(out, sigs)

    def test_empty_signals_is_noop(self) -> None:
        cfg = BacktestSweepConfig(
            strategy_timeframes_long={"inside_bar": ["15m", "1h", "1d"]}
        )
        sigs = _signals([])
        out = _apply_strategy_timeframes_directional_filter(
            cfg, "inside_bar", "4h", sigs
        )
        assert out.empty

    def test_long_dropped_on_excluded_tf(self) -> None:
        # inside_bar long restricted to ["15m", "1h", "1d"] — 4h long must drop.
        cfg = BacktestSweepConfig(
            strategy_timeframes={"inside_bar": ["15m", "1h", "4h", "1d"]},
            strategy_timeframes_long={"inside_bar": ["15m", "1h", "1d"]},
        )
        sigs = _signals(["long", "short", "long", "short"])
        out = _apply_strategy_timeframes_directional_filter(
            cfg, "inside_bar", "4h", sigs
        )
        # Only shorts survive on 4h.
        assert list(out["direction"]) == ["short", "short"]
        assert list(out["open_time"]) == [1, 3]

    def test_long_kept_on_allowed_tf(self) -> None:
        # Same cfg, 15m is in the allowlist — nothing dropped.
        cfg = BacktestSweepConfig(
            strategy_timeframes={"inside_bar": ["15m", "1h", "4h", "1d"]},
            strategy_timeframes_long={"inside_bar": ["15m", "1h", "1d"]},
        )
        sigs = _signals(["long", "short", "long"])
        out = _apply_strategy_timeframes_directional_filter(
            cfg, "inside_bar", "15m", sigs
        )
        pd.testing.assert_frame_equal(out, sigs)

    def test_short_dropped_on_excluded_tf(self) -> None:
        # hammer_hanging_man short restricted to ["15m", "1d"] — 1h short drops.
        cfg = BacktestSweepConfig(
            strategy_timeframes={"hammer_hanging_man": ["15m", "1h", "4h", "1d"]},
            strategy_timeframes_short={"hammer_hanging_man": ["15m", "1d"]},
        )
        sigs = _signals(["short", "long", "short", "long"])
        out = _apply_strategy_timeframes_directional_filter(
            cfg, "hammer_hanging_man", "1h", sigs
        )
        assert list(out["direction"]) == ["long", "long"]
        assert list(out["open_time"]) == [1, 3]

    def test_both_directions_excluded_returns_empty(self) -> None:
        # Both long and short restricted away from 4h.
        cfg = BacktestSweepConfig(
            strategy_timeframes={"foo": ["15m", "4h"]},
            strategy_timeframes_long={"foo": ["15m"]},
            strategy_timeframes_short={"foo": ["15m"]},
        )
        sigs = _signals(["long", "short"])
        out = _apply_strategy_timeframes_directional_filter(cfg, "foo", "4h", sigs)
        assert out.empty

    def test_directional_only_no_base(self) -> None:
        # No base list but strategy_timeframes_long set — directional list IS
        # the allowlist; tf 4h not in it → drop longs.
        cfg = BacktestSweepConfig(
            strategy_timeframes_long={"pin_bar": ["15m", "1d"]},
        )
        sigs = _signals(["long", "short", "long"])
        out = _apply_strategy_timeframes_directional_filter(cfg, "pin_bar", "4h", sigs)
        assert list(out["direction"]) == ["short"]

    def test_unrestricted_strategy_passes_through(self) -> None:
        # Restrictions on inside_bar do not affect pin_bar.
        cfg = BacktestSweepConfig(
            strategy_timeframes={"inside_bar": ["15m", "1h", "1d"]},
            strategy_timeframes_long={"inside_bar": ["15m", "1h"]},
        )
        sigs = _signals(["long", "short", "long"])
        out = _apply_strategy_timeframes_directional_filter(cfg, "pin_bar", "4h", sigs)
        pd.testing.assert_frame_equal(out, sigs)

    def test_resets_index_after_drop(self) -> None:
        cfg = BacktestSweepConfig(
            strategy_timeframes={"inside_bar": ["15m", "4h"]},
            strategy_timeframes_long={"inside_bar": ["15m"]},
        )
        sigs = _signals(["long", "short", "long", "short"])
        out = _apply_strategy_timeframes_directional_filter(
            cfg, "inside_bar", "4h", sigs
        )
        # Index reset to contiguous range after dropping longs.
        assert list(out.index) == list(range(len(out)))


def _make_in_memory_conn() -> duckdb.DuckDBPyConnection:
    """Create a fresh in-memory DuckDB connection with the full schema."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


class TestBuildFundingSeriesBySymbol:
    """Builder returns a Series per symbol; missing symbols are omitted."""

    def test_returns_series_for_seeded_symbol(self) -> None:
        conn = _make_in_memory_conn()
        upsert_funding_rates(
            conn,
            pd.DataFrame(
                {
                    "symbol": ["BTCUSDT", "BTCUSDT"],
                    "funding_time": [1000, 2000],
                    "funding_rate": [0.01, 0.02],
                }
            ),
        )
        out = _build_funding_series_by_symbol(conn, ["BTCUSDT", "ETHUSDT"], 0, 9999)
        assert "BTCUSDT" in out
        # ETHUSDT has no rows → omitted (engine falls to funding_r = 0.0)
        assert "ETHUSDT" not in out
        s = out["BTCUSDT"]
        assert list(s.index) == [1000, 2000]
        assert list(s.to_numpy()) == [pytest.approx(0.01), pytest.approx(0.02)]

    def test_empty_db_returns_empty_dict(self) -> None:
        conn = _make_in_memory_conn()
        out = _build_funding_series_by_symbol(conn, ["BTCUSDT"], 0, 9999)
        assert out == {}

    def test_time_range_filter_respected(self) -> None:
        conn = _make_in_memory_conn()
        upsert_funding_rates(
            conn,
            pd.DataFrame(
                {
                    "symbol": ["BTCUSDT", "BTCUSDT"],
                    "funding_time": [1000, 5000],
                    "funding_rate": [0.01, 0.02],
                }
            ),
        )
        # Request only up to 3000 — should see only the first row.
        out = _build_funding_series_by_symbol(conn, ["BTCUSDT"], 0, 3000)
        assert "BTCUSDT" in out
        assert list(out["BTCUSDT"].index) == [1000]


def _make_ohlcv_df(symbol: str, timeframe: str, n: int = 5) -> pd.DataFrame:
    """Minimal OHLCV DataFrame for seeding the DB."""
    base = 1_700_000_000_000  # arbitrary ms timestamp
    return pd.DataFrame(
        {
            "symbol": [symbol] * n,
            "timeframe": [timeframe] * n,
            "open_time": [base + i * 3_600_000 for i in range(n)],
            "open": [100.0 + i for i in range(n)],
            "high": [105.0 + i for i in range(n)],
            "low": [95.0 + i for i in range(n)],
            "close": [101.0 + i for i in range(n)],
            "volume": [1_000.0] * n,
            "taker_buy_volume": [500.0] * n,
        }
    )


class TestCollectSweepResultsPlumbing:
    """Prove slippage_pct + funding_series reach run_backtest in the sweep path."""

    def test_slippage_and_funding_series_passed_to_run_backtest(self) -> None:
        conn = _make_in_memory_conn()

        # Seed OHLCV so the cell is not skipped.
        ohlcv_df = _make_ohlcv_df("BTCUSDT", "1h", n=20)
        upsert_ohlcv(conn, ohlcv_df)

        # Seed a funding row so the builder returns a non-empty Series.
        upsert_funding_rates(
            conn,
            pd.DataFrame(
                {
                    "symbol": ["BTCUSDT"],
                    "funding_time": [1_700_000_000_000],
                    "funding_rate": [0.0001],
                }
            ),
        )

        start_ms = 0
        end_ms = 9_999_999_999_999

        cfg = BacktestSweepConfig(
            symbols=["BTCUSDT"],
            timeframes=["1h"],
            strategies=["fvg"],
            slippage_pct=0.0002,
            save_results=False,
        )

        # Minimal signals DataFrame with the required columns.
        fake_signals = pd.DataFrame(
            {
                "open_time": [1_700_000_000_000],
                "direction": ["long"],
                "reason": ["fvg"],
                "sl_price": [99.0],
                "context": [None],
                "low_volume": [False],
                "tp_price": [103.0],
            }
        )

        fake_bt_result = BacktestResult(
            symbol="BTCUSDT", timeframe="1h", strategy="fvg"
        )

        with (
            patch(
                "analytics.backtest_runner.detect_signals_for_strategy",
                return_value=fake_signals,
            ),
            patch(
                "analytics.backtest_runner.run_backtest",
                return_value=fake_bt_result,
            ) as mock_run_backtest,
        ):
            _collect_sweep_results(
                conn,
                cfg,
                cfg.tp_r,
                ["BTCUSDT"],
                ["fvg"],
                start_ms,
                end_ms,
            )

        assert mock_run_backtest.called, "run_backtest was not called"
        kwargs = mock_run_backtest.call_args.kwargs

        assert kwargs.get("slippage_pct") == pytest.approx(0.0002)
        # funding_series should be the BTC Series we seeded (not None).
        funding_series = kwargs.get("funding_series")
        assert funding_series is not None, (
            "funding_series was None — not threaded through"
        )
        assert 1_700_000_000_000 in funding_series.index
