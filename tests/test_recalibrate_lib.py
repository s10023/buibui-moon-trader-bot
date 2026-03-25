"""Tests for analytics/recalibrate_lib.py."""

import textwrap
from pathlib import Path

import duckdb
import pandas as pd

from analytics.data_store import init_schema
from analytics.recalibrate_lib import (
    compute_recalibrated_ratings,
    format_recalibration_report,
    win_rate_to_stars,
    write_confidence_to_source,
)

# ---------------------------------------------------------------------------
# win_rate_to_stars — boundary values
# ---------------------------------------------------------------------------


class TestWinRateToStars:
    def test_negative_avg_r_returns_1_star(self) -> None:
        assert win_rate_to_stars(-0.5, 20) == 1

    def test_zero_avg_r_returns_2_stars(self) -> None:
        assert win_rate_to_stars(0.0, 20) == 2

    def test_boundary_0_2_exclusive_returns_2_stars(self) -> None:
        assert win_rate_to_stars(0.19, 20) == 2

    def test_boundary_0_2_inclusive_returns_3_stars(self) -> None:
        assert win_rate_to_stars(0.2, 20) == 3

    def test_mid_range_3_stars(self) -> None:
        assert win_rate_to_stars(0.35, 20) == 3

    def test_boundary_0_5_exclusive_returns_3_stars(self) -> None:
        assert win_rate_to_stars(0.499, 20) == 3

    def test_boundary_0_5_inclusive_returns_4_stars(self) -> None:
        assert win_rate_to_stars(0.5, 20) == 4

    def test_boundary_0_9_exclusive_returns_4_stars(self) -> None:
        assert win_rate_to_stars(0.89, 20) == 4

    def test_boundary_0_9_inclusive_returns_5_stars(self) -> None:
        assert win_rate_to_stars(0.9, 20) == 5

    def test_high_avg_r_returns_5_stars(self) -> None:
        assert win_rate_to_stars(1.5, 20) == 5

    def test_insufficient_trades_returns_none(self) -> None:
        assert win_rate_to_stars(0.8, 5, min_trades=10) is None

    def test_exactly_min_trades_returns_rating(self) -> None:
        assert win_rate_to_stars(0.8, 10, min_trades=10) == 4

    def test_custom_min_trades(self) -> None:
        assert win_rate_to_stars(0.5, 3, min_trades=3) == 4
        assert win_rate_to_stars(0.5, 2, min_trades=3) is None


# ---------------------------------------------------------------------------
# compute_recalibrated_ratings — in-memory DuckDB
# ---------------------------------------------------------------------------


def _seed_backtest_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Insert sample backtest_runs rows for testing."""
    rows = [
        # bos: avg_r=0.6 over 30 closed trades → 4★
        {
            "run_id": "aaa",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "strategy": "bos",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": False,
            "smt_trend_filter": 1,
            "secondary_symbol": None,
            "total_signals": 30,
            "closed_trades": 30,
            "win_count": 18,
            "loss_count": 12,
            "win_rate": 0.6,
            "avg_r": 0.6,
            "total_r": 18.0,
            "max_drawdown_r": 3.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        # fvg: avg_r=-0.1 over 20 closed trades → 1★
        {
            "run_id": "bbb",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "fvg",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": False,
            "smt_trend_filter": 1,
            "secondary_symbol": None,
            "total_signals": 20,
            "closed_trades": 20,
            "win_count": 8,
            "loss_count": 12,
            "win_rate": 0.4,
            "avg_r": -0.1,
            "total_r": -2.0,
            "max_drawdown_r": 4.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        # pin_bar: only 5 trades — below default min_trades=10 → excluded
        {
            "run_id": "ccc",
            "symbol": "ETHUSDT",
            "timeframe": "4h",
            "strategy": "pin_bar",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0,
            "day_filter": False,
            "smt_trend_filter": 1,
            "secondary_symbol": None,
            "total_signals": 5,
            "closed_trades": 5,
            "win_count": 4,
            "loss_count": 1,
            "win_rate": 0.8,
            "avg_r": 1.2,
            "total_r": 6.0,
            "max_drawdown_r": 0.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
    ]
    df = pd.DataFrame(rows)
    conn.register("_seed_df", df)
    try:
        conn.execute(
            "INSERT INTO backtest_runs SELECT "
            "run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, "
            "days, sl_pct, tp_r, fee_pct, day_filter, smt_trend_filter, "
            "secondary_symbol, total_signals, closed_trades, win_count, loss_count, "
            "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms, sweep_id "
            "FROM _seed_df"
        )
    finally:
        conn.unregister("_seed_df")


class TestComputeRecalibratedRatings:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty_dict(self) -> None:
        conn = self._make_conn()
        result = compute_recalibrated_ratings(conn)
        conn.close()
        assert result == {}

    def test_bos_gets_correct_stars(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # bos avg_r=0.6 → 4★
        assert result["bos"] == 4

    def test_fvg_gets_correct_stars(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # fvg avg_r=-0.1 → 1★
        assert result["fvg"] == 1

    def test_insufficient_trades_excluded(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # pin_bar has only 5 trades — should not appear in results
        assert "pin_bar" not in result

    def test_custom_min_trades_includes_pin_bar(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=5)
        conn.close()
        # pin_bar avg_r=1.2 → 5★ when min_trades=5
        assert result["pin_bar"] == 5

    def test_returns_only_strategies_with_data(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        assert set(result.keys()) == {"bos", "fvg"}


# ---------------------------------------------------------------------------
# format_recalibration_report
# ---------------------------------------------------------------------------


class TestFormatRecalibrationReport:
    def test_returns_non_empty_string(self) -> None:
        old = {"bos": 3, "fvg": 4}
        new = {"bos": 4, "fvg": 1}
        win_rates = pd.DataFrame(
            [
                {
                    "strategy": "bos",
                    "timeframe": "4h",
                    "total_trades": 30,
                    "win_rate": 0.6,
                    "avg_r": 0.6,
                },
                {
                    "strategy": "fvg",
                    "timeframe": "1h",
                    "total_trades": 20,
                    "win_rate": 0.4,
                    "avg_r": -0.1,
                },
            ]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert len(report) > 0

    def test_contains_old_and_new_strategy_names(self) -> None:
        old = {"bos": 3, "fvg": 4}
        new = {"bos": 4, "fvg": 1}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "bos" in report
        assert "fvg" in report

    def test_shows_change_marker_for_changed_strategy(self) -> None:
        old = {"bos": 3}
        new = {"bos": 4}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report

    def test_shows_no_data_for_missing_new_rating(self) -> None:
        old = {"pin_bar": 2}
        new: dict[str, int] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "no data" in report

    def test_unchanged_strategy_shows_equals_marker(self) -> None:
        old = {"bos": 3}
        new = {"bos": 3}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "=" in report

    def test_empty_new_ratings_all_no_data(self) -> None:
        old = {"bos": 3, "fvg": 4}
        new: dict[str, int] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "No data" in report or "no data" in report


# ---------------------------------------------------------------------------
# write_confidence_to_source — patches indicators_lib.py source in-place
# ---------------------------------------------------------------------------

_FAKE_SOURCE = textwrap.dedent("""\
    STRATEGY_REGISTRY: dict[str, StrategySpec] = {
        "fvg": StrategySpec(
            name="fvg",
            description="Fair Value Gap.",
            confidence=4,
        ),
        "bos": StrategySpec(
            name="bos",
            description="Break of Structure.",
            confidence=3,
        ),
        "pin_bar": StrategySpec(
            name="pin_bar",
            description="Pin Bar.",
            confidence=2,
        ),
    }
""")


class TestWriteConfidenceToSource:
    def test_patches_single_strategy(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": 5}, src)
        assert patched == ["fvg"]
        assert "confidence=5" in src.read_text()

    def test_patches_multiple_strategies(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": 5, "bos": 1}, src)
        assert set(patched) == {"fvg", "bos"}
        content = src.read_text()
        assert "confidence=5" in content
        assert "confidence=1" in content

    def test_unknown_strategy_not_in_patched(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"nonexistent": 3}, src)
        assert patched == []
        assert src.read_text() == _FAKE_SOURCE  # file unchanged

    def test_same_value_still_patched(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"bos": 3}, src)
        assert "bos" in patched  # regex matched and wrote

    def test_does_not_corrupt_other_strategies(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        write_confidence_to_source({"fvg": 5}, src)
        content = src.read_text()
        # bos and pin_bar untouched
        assert '"bos": StrategySpec' in content
        assert '"pin_bar": StrategySpec' in content
