"""Tests for analytics/param_sweep.py — directional OOS metrics (Gate 3)."""

from __future__ import annotations

from analytics.backtest_lib import BacktestResult, Trade
from analytics.param_sweep import (
    AuditRow,
    SweepRow,
    _directional_split_hint,
    format_audit_results,
    format_sweep_results,
)

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
