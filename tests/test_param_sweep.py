"""Tests for analytics/param_sweep.py — directional OOS metrics (Gate 3)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pandas as pd

from analytics.backtest_lib import BacktestResult, Trade
from analytics.param_sweep import (
    AuditRow,
    SweepRow,
    _audit_strategy_worker,
    _directional_split_hint,
    _recommended_row,
    _row_to_trialperf,
    _sweep_grid_worker,
    format_audit_results,
    format_sweep_results,
)
from analytics.sweep_guard import CommitGateVerdict

_BASE_TIME = 1_700_000_000_000


def _make_result(
    long_trades: list[Trade] | None = None,
    short_trades: list[Trade] | None = None,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    strategy: str = "fvg",
) -> BacktestResult:
    """Build a BacktestResult with specific long/short trades for unit testing."""
    result = BacktestResult(symbol=symbol, timeframe=timeframe, strategy=strategy)
    for t in long_trades or []:
        result.trades.append(t)
    for t in short_trades or []:
        result.trades.append(t)
    return result


def _win(direction: str, r: float = 1.0) -> Trade:
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + r * 5.0 if direction == "long" else entry - r * 5.0
    t = Trade(
        signal_time=_BASE_TIME,
        entry_time=_BASE_TIME + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
    t.outcome = "win"
    t.exit_price = tp
    return t


def _loss(direction: str) -> Trade:
    entry = 100.0
    sl = entry - 5.0 if direction == "long" else entry + 5.0
    tp = entry + 10.0 if direction == "long" else entry - 10.0
    t = Trade(
        signal_time=_BASE_TIME,
        entry_time=_BASE_TIME + 1,
        entry_price=entry,
        direction=direction,
        sl_price=sl,
        tp_price=tp,
        fee_pct=0.0,
    )
    t.outcome = "loss"
    t.exit_price = sl
    return t


def _make_sweep_row(
    tp_r: float = 2.0,
    long_trades: list[Trade] | None = None,
    short_trades: list[Trade] | None = None,
    overfit: bool = False,
) -> SweepRow:
    is_result = _make_result()
    oos_result = _make_result(long_trades=long_trades, short_trades=short_trades)
    return SweepRow(
        params={"tp_r": tp_r},
        is_result=is_result,
        oos_result=oos_result,
        is_score=1.0,
        oos_score=0.5,
        decay=0.5,
        overfit=overfit,
    )


# ---------------------------------------------------------------------------
# SweepRow directional properties
# ---------------------------------------------------------------------------


class TestSweepRowDirectional:
    def test_long_oos_avg_r_computed_from_oos_result(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long", 2.0), _win("long", 2.0)],
            short_trades=[_loss("short")],
        )
        assert row.long_oos_avg_r is not None
        assert row.long_oos_avg_r > 0

    def test_short_oos_avg_r_computed_from_oos_result(self) -> None:
        row = _make_sweep_row(
            long_trades=[_loss("long")],
            short_trades=[_win("short", 3.0), _win("short", 3.0)],
        )
        assert row.short_oos_avg_r is not None
        assert row.short_oos_avg_r > 0

    def test_long_oos_n_counts_long_closed_trades(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long"), _loss("long"), _win("long")])
        assert row.long_oos_n == 3

    def test_short_oos_n_counts_short_closed_trades(self) -> None:
        row = _make_sweep_row(short_trades=[_win("short"), _win("short")])
        assert row.short_oos_n == 2

    def test_no_trades_returns_none_avg_r(self) -> None:
        row = _make_sweep_row()
        assert row.long_oos_avg_r is None
        assert row.short_oos_avg_r is None
        assert row.long_oos_n == 0
        assert row.short_oos_n == 0


# ---------------------------------------------------------------------------
# _directional_split_hint
# ---------------------------------------------------------------------------


class TestDirectionalSplitHint:
    def test_returns_empty_when_no_trades(self) -> None:
        row = _make_sweep_row()
        assert _directional_split_hint(row) == ""

    def test_returns_empty_when_insufficient_trades(self) -> None:
        # Only 2 long trades — below threshold of 3
        row = _make_sweep_row(
            long_trades=[_win("long"), _win("long")],
            short_trades=[_win("short"), _win("short"), _win("short")],
        )
        assert _directional_split_hint(row) == ""

    def test_returns_empty_when_delta_below_threshold(self) -> None:
        """If long and short OOS avg_r are very close, no hint."""
        row = _make_sweep_row(
            long_trades=[_win("long", 2.0)] * 5,
            short_trades=[_win("short", 2.0)] * 5,
        )
        # Both are the same avg_r — delta = 0, below 0.1 threshold
        assert _directional_split_hint(row) == ""

    def test_returns_hint_when_delta_large(self) -> None:
        """Longs winning at 3R, shorts losing: hint should fire."""
        row = _make_sweep_row(
            long_trades=[_win("long", 3.0)] * 5,
            short_trades=[_loss("short")] * 5,
        )
        hint = _directional_split_hint(row)
        assert "↕" in hint
        assert "↑" in hint
        assert "↓" in hint

    def test_hint_identifies_worse_direction(self) -> None:
        """When longs underperform, hint says 'consider tp_r_long override'."""
        row = _make_sweep_row(
            long_trades=[_loss("long")] * 5,
            short_trades=[_win("short", 3.0)] * 5,
        )
        hint = _directional_split_hint(row)
        assert "tp_r_long" in hint

    def test_hint_identifies_worse_short(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long", 3.0)] * 5,
            short_trades=[_loss("short")] * 5,
        )
        hint = _directional_split_hint(row)
        assert "tp_r_short" in hint


# ---------------------------------------------------------------------------
# format_sweep_results — directional columns present
# ---------------------------------------------------------------------------


class TestFormatSweepResultsDirectional:
    def test_header_contains_directional_columns(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long")] * 3,
            short_trades=[_win("short")] * 3,
        )
        output = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "↑OOS" in output
        assert "↓OOS" in output

    def test_directional_split_hint_shown_in_recommendation(self) -> None:
        row = _make_sweep_row(
            long_trades=[_loss("long")] * 5,
            short_trades=[_win("short", 3.0)] * 5,
        )
        output = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "↕" in output

    def test_no_hint_when_directions_agree(self) -> None:
        row = _make_sweep_row(
            long_trades=[_win("long", 2.0)] * 5,
            short_trades=[_win("short", 2.0)] * 5,
        )
        output = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "↕" not in output

    def test_empty_rows_returns_no_results(self) -> None:
        assert format_sweep_results([], "fvg", "BTCUSDT", "4h") == "  No results."


# ---------------------------------------------------------------------------
# Commit gate wiring (P0a-2)
# ---------------------------------------------------------------------------


def _verdict(decision: str, reasons: list[str] | None = None) -> CommitGateVerdict:
    return CommitGateVerdict(
        decision=decision,
        dsr=0.97 if decision == "COMMIT" else 0.40,
        pbo=0.20,
        min_trl=12.0,
        n_obs=40,
        n_trials=99,
        reasons=reasons or [],
    )


class TestCommitGateWiring:
    def test_row_to_trialperf_pools_is_and_oos(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")], short_trades=[_loss("short")])
        # add an IS trade so pooling is observable
        row.is_result.trades.append(_win("long", 2.0))
        tp = _row_to_trialperf(row)
        # 1 IS win + 1 OOS win + 1 OOS loss = 3 closed trades
        assert len(tp.returns) == 3
        assert len(tp.times) == len(tp.returns)
        assert tp.returns.count(-1.0) == 1  # the short loss

    def test_recommended_row_skips_overfit(self) -> None:
        overfit = _make_sweep_row(tp_r=1.0, overfit=True, long_trades=[_win("long")])
        clean = _make_sweep_row(tp_r=2.0, overfit=False, long_trades=[_win("long")])
        assert _recommended_row([overfit, clean]) is clean
        assert _recommended_row([overfit]) is None

    def test_format_renders_commit_pass(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")] * 3)
        out = format_sweep_results(
            [row], "fvg", "BTCUSDT", "4h", gate=_verdict("COMMIT")
        )
        assert "COMMIT-GATE: PASS" in out
        assert "DSR=0.97" in out

    def test_format_renders_do_not_commit(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")] * 3)
        out = format_sweep_results(
            [row],
            "fvg",
            "BTCUSDT",
            "4h",
            gate=_verdict("DO_NOT_COMMIT", ["DSR 0.40 < 0.95"]),
        )
        assert "DO-NOT-COMMIT" in out
        assert "DSR 0.40 < 0.95" in out

    def test_format_no_gate_is_backcompat(self) -> None:
        row = _make_sweep_row(long_trades=[_win("long")] * 3)
        out = format_sweep_results([row], "fvg", "BTCUSDT", "4h")
        assert "COMMIT-GATE" not in out


# ---------------------------------------------------------------------------
# AuditRow directional fields
# ---------------------------------------------------------------------------


class TestAuditRowDirectional:
    def _make_audit_row(
        self,
        long_oos: float | None = None,
        short_oos: float | None = None,
        long_n: int = 0,
        short_n: int = 0,
        verdict: str = "good",
    ) -> AuditRow:
        return AuditRow(
            strategy="bos",
            best_is_avg_r=0.5,
            best_oos_avg_r=0.3,
            best_tp_r=2.0,
            oos_trades=long_n + short_n,
            is_trades=10,
            verdict=verdict,
            best_long_oos_avg_r=long_oos,
            best_short_oos_avg_r=short_oos,
            long_oos_n=long_n,
            short_oos_n=short_n,
        )

    def test_fields_default_to_none_and_zero(self) -> None:
        row = AuditRow(
            strategy="bos",
            best_is_avg_r=0.5,
            best_oos_avg_r=0.3,
            best_tp_r=2.0,
            oos_trades=5,
            is_trades=10,
            verdict="good",
        )
        assert row.best_long_oos_avg_r is None
        assert row.best_short_oos_avg_r is None
        assert row.long_oos_n == 0
        assert row.short_oos_n == 0

    def test_format_audit_shows_directional_columns(self) -> None:
        row = self._make_audit_row(long_oos=0.4, short_oos=0.1, long_n=8, short_n=6)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "↑OOS" in output
        assert "↓OOS" in output

    def test_split_candidates_shown_when_delta_large(self) -> None:
        """When |long - short| >= 0.1 and n >= 3 each, show split candidate section."""
        row = self._make_audit_row(long_oos=0.5, short_oos=0.1, long_n=5, short_n=4)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" in output
        assert "bos" in output

    def test_no_split_candidates_when_delta_small(self) -> None:
        row = self._make_audit_row(long_oos=0.3, short_oos=0.25, long_n=5, short_n=5)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" not in output

    def test_no_split_candidates_when_insufficient_n(self) -> None:
        row = self._make_audit_row(long_oos=0.5, short_oos=0.1, long_n=2, short_n=5)
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" not in output

    def test_no_split_candidates_for_no_edge_strategies(self) -> None:
        row = self._make_audit_row(
            long_oos=0.5, short_oos=0.1, long_n=5, short_n=5, verdict="no_edge"
        )
        output = format_audit_results([row], "BTCUSDT", "4h", 180)
        assert "Directional split candidates" not in output


class TestAtrFloorForwarding:
    """F9 wiring: workers must forward atr_sl_multiplier/atr_sl_floor to run_backtest."""

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["open_time", "open", "high", "low", "close", "volume"]
        )

    @staticmethod
    def _empty_result() -> BacktestResult:
        return BacktestResult(symbol="BTCUSDT", timeframe="1h", strategy="bos")

    def test_sweep_grid_worker_forwards_floor_flags(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _sweep_grid_worker(
                params={"tp_r": 2.5},
                ohlcv_is=self._empty_df(),
                signals_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                signals_oos=self._empty_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                strategy="bos",
                fee_pct=0.0005,
                is_min=1,
                atr_sl_multiplier=2.5,
                atr_sl_floor=True,
            )

        assert len(captured) == 2  # IS + OOS
        for kwargs in captured:
            assert kwargs["atr_sl_multiplier"] == 2.5
            assert kwargs["atr_sl_floor"] is True
            assert kwargs["tp_r"] == 2.5

    def test_sweep_grid_worker_defaults_floor_off(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _sweep_grid_worker(
                params={"tp_r": 2.0},
                ohlcv_is=self._empty_df(),
                signals_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                signals_oos=self._empty_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                strategy="bos",
                fee_pct=0.0005,
                is_min=1,
            )

        assert len(captured) == 2
        for kwargs in captured:
            assert kwargs["atr_sl_multiplier"] is None
            assert kwargs["atr_sl_floor"] is False

    def test_audit_strategy_worker_forwards_floor_flags(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_run_backtest(*args: Any, **kwargs: Any) -> BacktestResult:
            captured.append(kwargs)
            return self._empty_result()

        with patch("analytics.param_sweep.run_backtest", side_effect=fake_run_backtest):
            _audit_strategy_worker(
                strat="bos",
                signals_is=self._empty_df(),
                signals_oos=self._empty_df(),
                ohlcv_is=self._empty_df(),
                ohlcv_oos=self._empty_df(),
                symbol="BTCUSDT",
                timeframe="1h",
                tp_values=[1.0, 2.0],
                is_min=1,
                fee_pct=0.0005,
                atr_sl_multiplier=2.0,
                atr_sl_floor=True,
            )

        # 2 tp_values × (IS + OOS) = 4 calls
        assert len(captured) == 4
        for kwargs in captured:
            assert kwargs["atr_sl_multiplier"] == 2.0
            assert kwargs["atr_sl_floor"] is True
