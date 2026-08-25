"""Tests for analytics/recalibrate_lib.py."""

import statistics
import textwrap
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics.data_store import (
    get_directional_confidence_ratings,
    init_schema,
)
from analytics.recalibrate_lib import (
    MIN_DSR_SD,
    PruneThresholdExceeded,
    UnratedPruneThresholdExceeded,
    _sharpe,
    compute_directional_ratings,
    compute_dsr_ratings,
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
    prune_stale_ratings,
    prune_undeclared_ratings,
    prune_unrated_ratings,
    win_rate_to_stars,
    write_confidence_to_db,
    write_confidence_to_source,
)
from analytics.signal_config import SignalWatchConfig

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
        # bos: avg_r=0.6 over 30 closed trades on 4h → 4★
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
            "day_filter": "off",
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
        # fvg: avg_r=-0.1 over 20 closed trades on 1h → 1★
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
            "day_filter": "off",
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
        # pin_bar: only 5 trades on 4h — below default min_trades=10 → excluded
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
            "day_filter": "off",
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
        # fib_golden_zone: 4h=0.7 (4★), 1h=-0.2 (1★) — different ratings per TF
        {
            "run_id": "ddd",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "strategy": "fib_golden_zone",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "smt_trend_filter": 1,
            "secondary_symbol": None,
            "total_signals": 15,
            "closed_trades": 15,
            "win_count": 10,
            "loss_count": 5,
            "win_rate": 0.667,
            "avg_r": 0.7,
            "total_r": 10.5,
            "max_drawdown_r": 2.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
        {
            "run_id": "eee",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "fib_golden_zone",
            "data_start_ms": 0,
            "data_end_ms": 1,
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
            "fee_pct": 0.0005,
            "day_filter": "off",
            "smt_trend_filter": 1,
            "secondary_symbol": None,
            "total_signals": 12,
            "closed_trades": 12,
            "win_count": 4,
            "loss_count": 8,
            "win_rate": 0.333,
            "avg_r": -0.2,
            "total_r": -2.4,
            "max_drawdown_r": 3.0,
            "run_at_ms": 1000,
            "sweep_id": None,
        },
    ]
    conn.executemany(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        [
            [
                r["run_id"],
                r["symbol"],
                r["timeframe"],
                r["strategy"],
                r["data_start_ms"],
                r["data_end_ms"],
                r["days"],
                r["sl_pct"],
                r["tp_r"],
                r["fee_pct"],
                r["day_filter"],
                r["smt_trend_filter"],
                r["secondary_symbol"],
                r["total_signals"],
                r["closed_trades"],
                r["win_count"],
                r["loss_count"],
                r["win_rate"],
                r["avg_r"],
                r["total_r"],
                r["max_drawdown_r"],
                r["run_at_ms"],
                r["sweep_id"],
            ]
            for r in rows
        ],
    )


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

    def test_bos_gets_correct_stars_per_tf(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # bos avg_r=0.6 on 4h → 4★
        assert result["bos"] == {"4h": 4}

    def test_fvg_gets_correct_stars_per_tf(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # fvg avg_r=-0.1 on 1h → 1★
        assert result["fvg"] == {"1h": 1}

    def test_insufficient_trades_excluded(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # pin_bar has only 5 trades — should not appear
        assert "pin_bar" not in result

    def test_custom_min_trades_includes_pin_bar(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=5)
        conn.close()
        # pin_bar avg_r=1.2 on 4h → 5★ when min_trades=5
        assert result["pin_bar"] == {"4h": 5}

    def test_per_tf_divergence(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        # fib_golden_zone: 4h=4★, 1h=1★ — different per TF
        assert result["fib_golden_zone"]["4h"] == 4
        assert result["fib_golden_zone"]["1h"] == 1

    def test_returns_only_strategies_with_data(self) -> None:
        conn = self._make_conn()
        _seed_backtest_runs(conn)
        result = compute_recalibrated_ratings(conn, min_trades=10)
        conn.close()
        assert set(result.keys()) == {"bos", "fvg", "fib_golden_zone"}


# ---------------------------------------------------------------------------
# format_recalibration_report
# ---------------------------------------------------------------------------


class TestFormatRecalibrationReport:
    def test_returns_non_empty_string(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}, "fvg": {"1h": 1}}
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

    def test_contains_strategy_names(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}, "fvg": {"1h": 1}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "bos" in report
        assert "fvg" in report

    def test_shows_change_marker_for_changed_strategy(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 4}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report

    def test_shows_no_data_for_missing_new_rating(self) -> None:
        old: dict[str, dict[str, int] | int] = {"pin_bar": 2}
        new: dict[str, dict[str, int]] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "no data" in report

    def test_unchanged_strategy_shows_equals_marker(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3}
        new: dict[str, dict[str, int]] = {"bos": {"4h": 3}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "=" in report

    def test_empty_new_ratings_all_no_data(self) -> None:
        old: dict[str, dict[str, int] | int] = {"bos": 3, "fvg": 4}
        new: dict[str, dict[str, int]] = {}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "No data" in report or "no data" in report

    def test_per_tf_dict_old_ratings_resolved_correctly(self) -> None:
        # old has per-TF dict; new has different rating for 4h
        old: dict[str, dict[str, int] | int] = {
            "fib_golden_zone": {"default": 1, "4h": 3}
        }
        new: dict[str, dict[str, int]] = {"fib_golden_zone": {"4h": 4}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        report = format_recalibration_report(old, new, win_rates)
        assert "3→4" in report

    def test_flags_high_star_low_dsr_as_suspect(self) -> None:
        old: dict[str, dict[str, int] | int] = {"fvg": 3}
        new: dict[str, dict[str, int]] = {"fvg": {"1h": 5}}
        win_rates = pd.DataFrame(
            [
                {
                    "strategy": "fvg",
                    "timeframe": "1h",
                    "total_trades": 40,
                    "win_rate": 0.7,
                    "avg_r": 1.0,
                }
            ]
        )
        dsr_ratings = {"fvg": {"1h": {"combined": 0.40, "long": None, "short": None}}}
        report = format_recalibration_report(
            old, new, win_rates, dsr_ratings=dsr_ratings
        )
        assert "Suspect" in report
        assert "fvg/1h" in report.split("Suspect", 1)[1]

    def test_does_not_flag_high_dsr_cell(self) -> None:
        old: dict[str, dict[str, int] | int] = {"fvg": 3}
        new: dict[str, dict[str, int]] = {"fvg": {"1h": 5}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        dsr_ratings = {"fvg": {"1h": {"combined": 0.99, "long": None, "short": None}}}
        report = format_recalibration_report(
            old, new, win_rates, dsr_ratings=dsr_ratings
        )
        assert "Suspect" not in report

    def test_does_not_flag_low_star_low_dsr(self) -> None:
        # 3★ cell with low DSR is not "suspect" — only high-conviction (★≥4) cells.
        old: dict[str, dict[str, int] | int] = {"fvg": 3}
        new: dict[str, dict[str, int]] = {"fvg": {"1h": 3}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        dsr_ratings = {"fvg": {"1h": {"combined": 0.10, "long": None, "short": None}}}
        report = format_recalibration_report(
            old, new, win_rates, dsr_ratings=dsr_ratings
        )
        assert "Suspect" not in report

    def test_reports_high_star_null_dsr_as_unscoreable(self) -> None:
        # A ★≥4 cell whose DSR is None has too few scoreable trades to deflate, so
        # the overfit check could not run on it. Omitting it silently reads as
        # "checked and clean" — the 2026-08-11 decay review found 42 of 66 rated
        # cells (21 of 30 5★) invisible to a warning written for exactly them.
        old: dict[str, dict[str, int] | int] = {"fvg": 3}
        new: dict[str, dict[str, int]] = {"fvg": {"1h": 5}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        dsr_ratings: dict[str, dict[str, dict[str, float | None]]] = {
            "fvg": {"1h": {"combined": None, "long": None, "short": None}}
        }
        report = format_recalibration_report(
            old, new, win_rates, dsr_ratings=dsr_ratings
        )
        assert "Unscoreable" in report
        assert "fvg/1h" in report.split("Unscoreable", 1)[1]

    def test_unscoreable_cell_is_not_called_suspect(self) -> None:
        # Absence of evidence is not evidence of overfit: a None-DSR cell must not
        # be folded into the Suspect list, which asserts a measured DSR < threshold.
        old: dict[str, dict[str, int] | int] = {"fvg": 3}
        new: dict[str, dict[str, int]] = {"fvg": {"1h": 5}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        dsr_ratings: dict[str, dict[str, dict[str, float | None]]] = {
            "fvg": {"1h": {"combined": None, "long": None, "short": None}}
        }
        report = format_recalibration_report(
            old, new, win_rates, dsr_ratings=dsr_ratings
        )
        # Assert on the Suspect *line*, not the bare word — the Unscoreable line
        # legitimately cross-references it to say that absence there means nothing.
        assert "⚠ Suspect" not in report

    def test_does_not_report_low_star_null_dsr(self) -> None:
        # Only high-conviction (★≥4) cells make a claim worth checking; a 3★ cell
        # with no DSR is unremarkable and would drown the line in noise.
        old: dict[str, dict[str, int] | int] = {"fvg": 3}
        new: dict[str, dict[str, int]] = {"fvg": {"1h": 3}}
        win_rates = pd.DataFrame(
            columns=["strategy", "timeframe", "total_trades", "win_rate", "avg_r"]
        )
        dsr_ratings: dict[str, dict[str, dict[str, float | None]]] = {
            "fvg": {"1h": {"combined": None, "long": None, "short": None}}
        }
        report = format_recalibration_report(
            old, new, win_rates, dsr_ratings=dsr_ratings
        )
        assert "Unscoreable" not in report


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
    def test_patches_single_strategy_int(self, tmp_path: Path) -> None:
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

    def test_patches_per_tf_dict(self, tmp_path: Path) -> None:
        src = tmp_path / "indicators_lib.py"
        src.write_text(_FAKE_SOURCE)
        patched = write_confidence_to_source({"fvg": {"default": 1, "4h": 4}}, src)
        assert patched == ["fvg"]
        content = src.read_text()
        assert '"default": 1' in content
        assert '"4h": 4' in content

    def test_patches_dict_over_existing_dict(self, tmp_path: Path) -> None:
        # source already has a dict confidence; patch replaces it
        source = textwrap.dedent("""\
            "fvg": StrategySpec(
                name="fvg",
                confidence={"default": 1, "4h": 3},
            ),
        """)
        src = tmp_path / "indicators_lib.py"
        src.write_text(source)
        patched = write_confidence_to_source({"fvg": {"default": 2, "4h": 5}}, src)
        assert patched == ["fvg"]
        content = src.read_text()
        assert '"default": 2' in content
        assert '"4h": 5' in content

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


# ---------------------------------------------------------------------------
# StrategySpec.get_confidence — TF resolution
# ---------------------------------------------------------------------------

from analytics.strategies import StrategySpec  # noqa: E402


class TestStrategySpecGetConfidence:
    def test_int_confidence_returns_same_for_any_tf(self) -> None:
        spec = StrategySpec(name="x", description="", confidence=3)
        assert spec.get_confidence("15m") == 3
        assert spec.get_confidence("1h") == 3
        assert spec.get_confidence("4h") == 3

    def test_dict_confidence_returns_tf_value(self) -> None:
        spec = StrategySpec(
            name="x", description="", confidence={"default": 1, "4h": 4}
        )
        assert spec.get_confidence("4h") == 4

    def test_dict_confidence_falls_back_to_default(self) -> None:
        spec = StrategySpec(
            name="x", description="", confidence={"default": 2, "4h": 4}
        )
        assert spec.get_confidence("1h") == 2
        assert spec.get_confidence("15m") == 2

    def test_dict_confidence_falls_back_to_3_when_no_default(self) -> None:
        spec = StrategySpec(name="x", description="", confidence={"4h": 4})
        assert spec.get_confidence("1h") == 3


# ---------------------------------------------------------------------------
# write_confidence_to_db
# ---------------------------------------------------------------------------


class TestWriteConfidenceToDb:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_writes_ratings_to_db(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}
        win_rates = pd.DataFrame(
            [
                {"strategy": "fvg", "timeframe": "1h", "avg_r": 0.35, "win_rate": 0.55},
                {"strategy": "fvg", "timeframe": "4h", "avg_r": 0.72, "win_rate": 0.60},
            ]
        )
        write_confidence_to_db(conn, "signal_watch", ratings, win_rates)
        from analytics.data_store import get_confidence_ratings

        result = get_confidence_ratings(conn, "signal_watch")
        assert result == {"fvg": {"1h": 3, "4h": 4}, "bos": {"15m": 1}}

    def test_different_configs_do_not_interfere(self) -> None:
        conn = self._conn()
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(conn, "signal_watch", {"fvg": {"1h": 2}}, empty_wr)
        write_confidence_to_db(
            conn, "signal_watch_weekdays", {"fvg": {"1h": 4}}, empty_wr
        )
        from analytics.data_store import get_confidence_ratings

        assert get_confidence_ratings(conn, "signal_watch")["fvg"]["1h"] == 2
        assert get_confidence_ratings(conn, "signal_watch_weekdays")["fvg"]["1h"] == 4

    def test_writes_directional_stars_to_db(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn, "signal_watch", ratings, empty_wr, directional_ratings=dir_ratings
        )
        result = get_directional_confidence_ratings(conn, "signal_watch")
        assert result["fvg"]["1h"]["long"] == 5
        assert result["fvg"]["1h"]["short"] == 1

    def test_combined_stars_unaffected_by_directional(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 3}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn, "signal_watch", ratings, empty_wr, directional_ratings=dir_ratings
        )
        from analytics.data_store import get_confidence_ratings

        combined = get_confidence_ratings(conn, "signal_watch")
        assert combined["fvg"]["1h"] == 3

    def test_writes_dsr_annotation_per_direction(self) -> None:
        conn = self._conn()
        ratings = {"fvg": {"1h": 4}}
        dir_ratings = {"fvg": {"1h": {"long": 5, "short": 1}}}
        dsr_ratings: dict[str, dict[str, dict[str, float | None]]] = {
            "fvg": {"1h": {"combined": 0.80, "long": 0.91, "short": 0.20}}
        }
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn,
            "signal_watch",
            ratings,
            empty_wr,
            directional_ratings=dir_ratings,
            dsr_ratings=dsr_ratings,
        )
        rows = dict(
            conn.execute(
                "SELECT direction, dsr FROM confidence_ratings "
                "WHERE strategy = 'fvg' AND tf = '1h'"
            ).fetchall()
        )
        assert rows["combined"] == pytest.approx(0.80)
        assert rows["long"] == pytest.approx(0.91)
        assert rows["short"] == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# compute_directional_ratings
# ---------------------------------------------------------------------------


def _seed_directional_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed backtest_runs with directional long/short split data."""
    conn.execute(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "dir_bos",
            "BTCUSDT",
            "4h",
            "bos",
            0,
            1,
            90,
            0.02,
            2.0,
            0.0,
            "off",
            1,
            None,
            20,
            20,
            12,
            8,
            0.6,
            0.4,
            8.0,
            4.0,
            1000,
            None,
            # long: 10 trades, avg_r=0.9 → 5★ at min_trades=5
            10,
            8,
            0.8,
            0.9,
            # short: 10 trades, avg_r=-0.1 → 1★
            10,
            4,
            0.4,
            -0.1,
            9.0,  # long_total_r
            -1.0,  # short_total_r
            None,  # adr_suppress_threshold (added via ALTER TABLE)
            None,  # recovery_factor (added via ALTER TABLE)
            None,  # volume_suppress (added via ALTER TABLE, last column)
        ],
    )


class TestComputeDirectionalRatings:
    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty(self) -> None:
        conn = self._make_conn()
        result = compute_directional_ratings(conn)
        conn.close()
        assert result == {}

    def test_directional_stars_computed_per_direction(self) -> None:
        conn = self._make_conn()
        _seed_directional_runs(conn)
        result = compute_directional_ratings(conn, min_trades=5)
        conn.close()
        assert "bos" in result
        assert "4h" in result["bos"]
        # long avg_r=0.9 → 5★; short avg_r=-0.1 → 1★
        assert result["bos"]["4h"]["long"] == 5
        assert result["bos"]["4h"]["short"] == 1

    def test_direction_excluded_when_below_min_trades(self) -> None:
        conn = self._make_conn()
        _seed_directional_runs(conn)
        result = compute_directional_ratings(conn, min_trades=15)
        conn.close()
        # 10 trades per direction < min_trades=15 → neither direction rated
        assert result == {}


class TestPruneStaleRatings:
    """prune_stale_ratings deletes rows with a mismatched day_filter."""

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def _seed(
        self,
        conn: duckdb.DuckDBPyConnection,
        config_name: str,
        day_filter: str | None,
        strategy: str = "fvg",
        tf: str = "1h",
    ) -> None:
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn,
            config_name,
            {strategy: {tf: 3}},
            empty_wr,
            day_filter=day_filter,
        )

    def _count(
        self, conn: duckdb.DuckDBPyConnection, config_name: str
    ) -> dict[str | None, int]:
        rows = conn.execute(
            "SELECT day_filter, COUNT(*) FROM confidence_ratings "
            "WHERE config_name = ? GROUP BY day_filter",
            [config_name],
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def test_returns_zero_when_no_stale_rows(self) -> None:
        conn = self._conn()
        self._seed(conn, "signal_watch_weekdays", "mon_fri")
        n = prune_stale_ratings(conn, "signal_watch_weekdays", "mon_fri")
        assert n == 0
        assert self._count(conn, "signal_watch_weekdays") == {"mon_fri": 1}

    def test_removes_rows_with_mismatched_day_filter(self) -> None:
        conn = self._conn()
        self._seed(conn, "signal_watch_weekdays", "weekdays", strategy="fvg", tf="1h")
        self._seed(conn, "signal_watch_weekdays", "weekdays", strategy="bos", tf="4h")
        self._seed(conn, "signal_watch_weekdays", "mon_fri", strategy="orb", tf="15m")
        n = prune_stale_ratings(conn, "signal_watch_weekdays", "mon_fri")
        # Two stale "weekdays" rows removed (fvg/1h and bos/4h).
        assert n == 2
        assert self._count(conn, "signal_watch_weekdays") == {"mon_fri": 1}

    def test_does_not_touch_other_configs(self) -> None:
        conn = self._conn()
        self._seed(conn, "signal_watch_weekdays", "weekdays")
        self._seed(conn, "signal_watch", "tue_thu")
        n = prune_stale_ratings(conn, "signal_watch_weekdays", "mon_fri")
        assert n == 1
        assert self._count(conn, "signal_watch_weekdays") == {}
        assert self._count(conn, "signal_watch") == {"tue_thu": 1}

    def test_keeps_rows_with_null_day_filter(self) -> None:
        """Rows where day_filter is NULL (legacy / unset) are left alone."""
        conn = self._conn()
        self._seed(conn, "signal_watch_weekdays", None)
        self._seed(conn, "signal_watch_weekdays", "weekdays", strategy="bos")
        n = prune_stale_ratings(conn, "signal_watch_weekdays", "mon_fri")
        assert n == 1
        # NULL-day_filter row preserved; "weekdays" row removed.
        result = self._count(conn, "signal_watch_weekdays")
        assert result.get(None) == 1
        assert "weekdays" not in result


class TestPruneUndeclaredRatings:
    """prune_undeclared_ratings deletes rows for cells the config no longer declares.

    **Fixture discipline is the whole point of this class.** Every expected
    survivor below is hand-enumerated from the config literal; nothing here
    calls ``declared_cells`` to build its expectation. Asserting that the prune
    agrees with the resolver it is implemented on top of would pass no matter
    how wrong both are — the exact defect that let #614's critical bug survive
    three review rounds.

    ``test_keeps_strategy_absent_from_strategy_timeframes`` is the load-bearing
    one: a strategy listed in ``strategies`` but omitted from
    ``strategy_timeframes`` runs on *all* configured timeframes. Reading only
    ``strategy_timeframes`` is what inflated the 2026-08-12 orphan measurement
    117 → 194; the same bug HERE would delete 77 live ratings.
    """

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def _seed(
        self,
        conn: duckdb.DuckDBPyConnection,
        config_name: str,
        strategy: str,
        tf: str,
        direction: str = "combined",
    ) -> None:
        empty_wr: pd.DataFrame = pd.DataFrame(
            columns=["strategy", "timeframe", "avg_r", "win_rate"]
        )
        write_confidence_to_db(
            conn,
            config_name,
            {strategy: {tf: 3}},
            empty_wr,
            day_filter="tue_thu",
            directional_ratings=(
                None if direction == "combined" else {strategy: {tf: {direction: 3}}}
            ),
        )

    def _cells(
        self, conn: duckdb.DuckDBPyConnection, config_name: str
    ) -> set[tuple[str, str, str]]:
        rows = conn.execute(
            "SELECT strategy, tf, direction FROM confidence_ratings "
            "WHERE config_name = ?",
            [config_name],
        ).fetchall()
        return {(str(r[0]), str(r[1]), str(r[2])) for r in rows}

    def test_returns_zero_when_every_rated_cell_is_declared(self) -> None:
        cfg = SignalWatchConfig(
            strategies=["fvg"], timeframes=["1h"], day_filter="tue_thu"
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", "fvg", "1h")
        n = prune_undeclared_ratings(conn, "signal_watch", cfg)
        assert n == 0
        assert ("fvg", "1h", "combined") in self._cells(conn, "signal_watch")

    def test_removes_row_for_cell_the_config_no_longer_declares(self) -> None:
        cfg = SignalWatchConfig(
            strategies=["fvg"], timeframes=["1h"], day_filter="tue_thu"
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", "fvg", "1h")
        self._seed(conn, "signal_watch", "orb", "1d")
        n = prune_undeclared_ratings(conn, "signal_watch", cfg)
        assert n == 1
        assert self._cells(conn, "signal_watch") == {("fvg", "1h", "combined")}

    def test_keeps_strategy_absent_from_strategy_timeframes(self) -> None:
        """A strategy omitted from strategy_timeframes runs on ALL timeframes.

        This is the 2026-08-12 measurement bug as a deletion. ``bos`` is not in
        ``strategy_timeframes``, so it is declared on 15m AND 1h; both ratings
        must survive. A resolver that reads only ``strategy_timeframes`` sees
        ``bos`` as declared nowhere and deletes both.
        """
        cfg = SignalWatchConfig(
            strategies=["fvg", "bos"],
            timeframes=["15m", "1h"],
            strategy_timeframes={"fvg": ["1h"]},
            day_filter="tue_thu",
        )
        conn = self._conn()
        # Hand-enumerated declared set: fvg×1h (narrowed), bos×15m, bos×1h.
        self._seed(conn, "signal_watch", "fvg", "1h")
        self._seed(conn, "signal_watch", "bos", "15m")
        self._seed(conn, "signal_watch", "bos", "1h")
        # Undeclared: fvg×15m is excluded by the narrowing.
        self._seed(conn, "signal_watch", "fvg", "15m")
        n = prune_undeclared_ratings(conn, "signal_watch", cfg)
        assert n == 1
        assert self._cells(conn, "signal_watch") == {
            ("fvg", "1h", "combined"),
            ("bos", "15m", "combined"),
            ("bos", "1h", "combined"),
        }

    def test_respects_directional_narrowing(self) -> None:
        """A long-only narrowing makes the SHORT rating for that cell an orphan."""
        cfg = SignalWatchConfig(
            strategies=["fvg"],
            timeframes=["1h"],
            strategy_timeframes={"fvg": ["1h"]},
            strategy_timeframes_short={"fvg": []},
            day_filter="tue_thu",
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", "fvg", "1h", direction="long")
        self._seed(conn, "signal_watch", "fvg", "1h", direction="short")
        n = prune_undeclared_ratings(conn, "signal_watch", cfg)
        # Only the short row goes. `write_confidence_to_db` also emits a
        # `combined` row per call, and that one is direction-agnostic — judged
        # against the base declaration, which still declares fvg x 1h.
        assert n == 1
        assert self._cells(conn, "signal_watch") == {
            ("fvg", "1h", "long"),
            ("fvg", "1h", "combined"),
        }

    def test_legacy_combined_row_judged_against_base_declaration(self) -> None:
        """A ``combined`` row is direction-agnostic → judged on the base set."""
        cfg = SignalWatchConfig(
            strategies=["fvg"],
            timeframes=["1h"],
            strategy_timeframes={"fvg": ["1h"]},
            strategy_timeframes_short={"fvg": []},
            day_filter="tue_thu",
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", "fvg", "1h", direction="combined")
        n = prune_undeclared_ratings(conn, "signal_watch", cfg)
        assert n == 0
        assert self._cells(conn, "signal_watch") == {("fvg", "1h", "combined")}

    def test_does_not_touch_other_configs(self) -> None:
        cfg = SignalWatchConfig(
            strategies=["fvg"], timeframes=["1h"], day_filter="tue_thu"
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", "orb", "1d")
        self._seed(conn, "signal_watch_all", "orb", "1d")
        n = prune_undeclared_ratings(conn, "signal_watch", cfg)
        assert n == 1
        assert self._cells(conn, "signal_watch") == set()
        assert self._cells(conn, "signal_watch_all") == {("orb", "1d", "combined")}

    def _seed_wide(
        self, conn: duckdb.DuckDBPyConnection, n_undeclared: int
    ) -> SignalWatchConfig:
        """One declared cell plus ``n_undeclared`` undeclared ones.

        Sized past the guard's minimum-rows floor on purpose: the share ceiling
        is evidence about a resolver, and a 3-of-4 table is not evidence.
        """
        cfg = SignalWatchConfig(
            strategies=["fvg"], timeframes=["1h"], day_filter="tue_thu"
        )
        self._seed(conn, "signal_watch", "fvg", "1h")
        for i in range(n_undeclared):
            self._seed(conn, "signal_watch", f"ghost_{i}", "1d")
        return cfg

    def test_aborts_without_deleting_when_share_exceeds_threshold(self) -> None:
        """A resolver bug shows up as mass deletion — fail loudly, delete nothing."""
        conn = self._conn()
        cfg = self._seed_wide(conn, n_undeclared=24)
        with pytest.raises(PruneThresholdExceeded) as exc:
            prune_undeclared_ratings(conn, "signal_watch", cfg)
        assert exc.value.n_undeclared == 24
        assert exc.value.n_total == 25
        # Nothing deleted — the whole point of the guard.
        assert len(self._cells(conn, "signal_watch")) == 25

    def test_threshold_can_be_raised_to_allow_a_known_large_prune(self) -> None:
        conn = self._conn()
        cfg = self._seed_wide(conn, n_undeclared=24)
        n = prune_undeclared_ratings(conn, "signal_watch", cfg, max_share=1.0)
        assert n == 24
        assert self._cells(conn, "signal_watch") == {("fvg", "1h", "combined")}

    def test_small_table_does_not_trip_the_share_guard(self) -> None:
        """The ceiling is evidence about a resolver; a tiny table carries none.

        Without a minimum-rows floor the guard fires on a 1-of-1 table, which
        would make the prune unusable on any small or freshly-seeded config
        while catching no real bug — a production config carries 130-180 rows.
        """
        cfg = SignalWatchConfig(
            strategies=["fvg"], timeframes=["1h"], day_filter="tue_thu"
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", "orb", "1d")
        assert prune_undeclared_ratings(conn, "signal_watch", cfg) == 1
        assert self._cells(conn, "signal_watch") == set()

    def test_empty_table_is_not_a_threshold_breach(self) -> None:
        """0 of 0 must not read as 100% deleted and trip the guard."""
        cfg = SignalWatchConfig(
            strategies=["fvg"], timeframes=["1h"], day_filter="tue_thu"
        )
        conn = self._conn()
        assert prune_undeclared_ratings(conn, "signal_watch", cfg) == 0


class TestPruneUnratedRatings:
    """prune_unrated_ratings deletes rows the CURRENT pass produced no rating for.

    ST58: omission from ``compute_recalibrated_ratings`` /
    ``compute_directional_ratings`` has no delete counterpart, so a cell that
    stays *declared* while falling below its trade floor keeps its last rating
    forever. Measured 2026-08-20: 24 of 288 rows frozen, oldest stamped
    2026-04-02, and 24 of 24 still declared — so neither existing pruner can
    reach them. A frozen star is live-reachable: the conflict resolver drops
    the lower-confidence side when both directions fire on one candle.

    **Fixture discipline, inherited from TestPruneUndeclaredRatings.** Every
    expected survivor set below is hand-enumerated as a literal. Do not build an
    expectation by calling the same key-construction the implementation uses —
    that passes no matter how wrong both are.

    ``_seed`` writes a ``combined`` row for every key in ``ratings``
    *unconditionally* (write_confidence_to_db does this before the directional
    loop), so seeding "a long row" produces two rows. Every expectation here
    accounts for that; getting it wrong is the easiest way to write a
    spec-conformant test that asserts the wrong thing.
    """

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def _empty_wr(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["strategy", "timeframe", "avg_r", "win_rate"])

    def _seed(
        self,
        conn: duckdb.DuckDBPyConnection,
        config_name: str,
        ratings: dict[str, dict[str, int]],
        directional: dict[str, dict[str, dict[str, int]]] | None = None,
        day_filter: str = "tue_thu",
    ) -> None:
        """Put rows in the table as a PRIOR pass would have left them."""
        write_confidence_to_db(
            conn,
            config_name,
            ratings,
            self._empty_wr(),
            day_filter=day_filter,
            directional_ratings=directional,
        )

    def _cells(
        self, conn: duckdb.DuckDBPyConnection, config_name: str
    ) -> set[tuple[str, str, str]]:
        rows = conn.execute(
            "SELECT strategy, tf, direction FROM confidence_ratings "
            "WHERE config_name = ?",
            [config_name],
        ).fetchall()
        return {(str(r[0]), str(r[1]), str(r[2])) for r in rows}

    def test_deletes_only_cells_this_pass_did_not_rate(self) -> None:
        """ANTI-VACUITY: the fixture holds both a survivor and a victim.

        Mutation that must fail this: replace the predicate with an
        unconditional ``DELETE ... WHERE config_name = ?``. Both halves of the
        assertion are load-bearing — ``n == 2`` alone stays green under a
        mutation that deletes the wrong two rows.
        """
        conn = self._conn()
        self._seed(
            conn,
            "signal_watch",
            {
                "fvg": {"1h": 3},
                "bos": {"4h": 2},
                "smt_divergence": {"1h": 5},
                "engulfing": {"1d": 5},
            },
        )
        # This pass rated only fvg/1h and bos/4h; the other two fell below
        # min_trades and were omitted — ST58 exactly.
        n = prune_unrated_ratings(
            conn, "signal_watch", {"fvg": {"1h": 3}, "bos": {"4h": 2}}, None
        )
        assert n == 2
        assert self._cells(conn, "signal_watch") == {
            ("fvg", "1h", "combined"),
            ("bos", "4h", "combined"),
        }
        conn.close()

    def test_does_not_touch_other_configs(self) -> None:
        """Two mutations must fail this: dropping the config_name filter from
        the SELECT (n goes to 3) and from the DELETE (the sibling config loses
        its fvg/1h row).

        The second is why the other config must hold **the same cell**. Seeding
        it with a disjoint strategy makes this case vacuous — a DELETE with no
        config_name filter then matches nothing over there by luck of the
        naming, and the test passes while the bug ships. Overlap is also the
        production shape: all three signal_watch configs declare fvg/1h.
        """
        conn = self._conn()
        self._seed(conn, "signal_watch", {"fvg": {"1h": 3}})
        self._seed(conn, "signal_watch_all", {"fvg": {"1h": 4}, "orb": {"1d": 4}})
        n = prune_unrated_ratings(conn, "signal_watch", {}, None)
        assert n == 1
        assert self._cells(conn, "signal_watch") == set()
        assert self._cells(conn, "signal_watch_all") == {
            ("fvg", "1h", "combined"),
            ("orb", "1d", "combined"),
        }
        conn.close()

    def test_key_is_three_part_so_one_direction_can_be_unrated(self) -> None:
        """The mutation here REPRODUCES ST58 itself.

        Build the survivor key as ``(strategy, tf)`` instead of
        ``(strategy, tf, direction)`` and the frozen short row survives with
        n == 0. That two-part key cannot express "rated long, not short", which
        is the state ``signal_watch cvd_divergence/1h`` is actually in today:
        long recomputed fresh, short frozen at ★5 since 2026-05-15.
        """
        conn = self._conn()
        self._seed(
            conn,
            "signal_watch",
            {"cvd_divergence": {"1h": 3}},
            {"cvd_divergence": {"1h": {"long": 3, "short": 5}}},
        )
        # Fixture precondition, asserted so a helper change cannot gut the case.
        assert self._cells(conn, "signal_watch") == {
            ("cvd_divergence", "1h", "combined"),
            ("cvd_divergence", "1h", "long"),
            ("cvd_divergence", "1h", "short"),
        }
        n = prune_unrated_ratings(
            conn,
            "signal_watch",
            {"cvd_divergence": {"1h": 3}},
            {"cvd_divergence": {"1h": {"long": 3}}},  # short omitted this pass
        )
        assert n == 1
        assert self._cells(conn, "signal_watch") == {
            ("cvd_divergence", "1h", "combined"),
            ("cvd_divergence", "1h", "long"),
        }
        conn.close()

    def test_aborts_without_deleting_when_share_exceeds_threshold(self) -> None:
        """ANTI-CATASTROPHE: a mis-scoped pool must not silently wipe a config.

        Measured failure mode: omitting ``adr_suppress_threshold`` from
        ``get_backtest_win_rates`` returns a 0-row pool for two of the three
        configs, ``compute_*_ratings`` then returns ``{}``, and an unguarded
        delete removes every live rating in one pass while the daemon merely
        logs "No confidence ratings found" at its next restart.

        Mutation: remove the share-ceiling check. ``pytest.raises`` fails first;
        if someone then drops the ``raises``, the final count assertion catches
        it at 0. Keep both.
        """
        conn = self._conn()
        self._seed(conn, "signal_watch", {f"ghost_{i}": {"1h": 3} for i in range(30)})
        assert len(self._cells(conn, "signal_watch")) == 30
        with pytest.raises(UnratedPruneThresholdExceeded) as exc:
            prune_unrated_ratings(conn, "signal_watch", {}, None)
        assert exc.value.n_unrated == 30
        assert exc.value.n_total == 30
        assert len(self._cells(conn, "signal_watch")) == 30
        conn.close()

    def test_threshold_can_be_raised_to_allow_a_known_large_prune(self) -> None:
        """Without this, a future edit could satisfy the case above by making
        the pruner never delete anything at all."""
        conn = self._conn()
        self._seed(conn, "signal_watch", {f"ghost_{i}": {"1h": 3} for i in range(30)})
        n = prune_unrated_ratings(conn, "signal_watch", {}, None, max_share=1.0)
        assert n == 30
        assert self._cells(conn, "signal_watch") == set()
        conn.close()

    def test_small_table_does_not_trip_the_share_guard(self) -> None:
        """Mutation: invert the min_rows guard (``<`` for ``>=``) and this
        raises instead of deleting. The ceiling is evidence about a resolver
        and a 4-row table carries none."""
        conn = self._conn()
        self._seed(
            conn,
            "signal_watch",
            {"fvg": {"1h": 3}, "bos": {"4h": 2}, "orb": {"15m": 1}, "doji": {"1d": 1}},
        )
        n = prune_unrated_ratings(conn, "signal_watch", {}, None)
        assert n == 4
        assert self._cells(conn, "signal_watch") == set()
        conn.close()

    def test_empty_table_is_not_a_threshold_breach(self) -> None:
        """0 of 0 must not read as 100% deleted and trip the guard."""
        conn = self._conn()
        assert prune_unrated_ratings(conn, "signal_watch", {}, None) == 0
        conn.close()

    def test_composes_with_stale_and_undeclared_pruners(self) -> None:
        """Drift guard, not a mutation case: the three predicates are
        orthogonal and all three must keep running.

        Assert the TUPLE, not just the survivor set. Running the unrated prune
        first would count the stale and undeclared rows as unrated too — they
        are — which makes the runner's three printed counts lie and inflates
        the share the ceiling is measured against.
        """
        cfg = SignalWatchConfig(
            strategies=["fvg", "bos"], timeframes=["1h", "4h"], day_filter="tue_thu"
        )
        conn = self._conn()
        self._seed(conn, "signal_watch", {"fvg": {"1h": 3}})  # live, re-rated below
        self._seed(conn, "signal_watch", {"bos": {"4h": 2}})  # declared, not re-rated
        self._seed(conn, "signal_watch", {"orb": {"1d": 4}})  # undeclared
        self._seed(
            conn, "signal_watch", {"doji": {"1h": 1}}, day_filter="weekend"
        )  # stale day_filter

        n_stale = prune_stale_ratings(conn, "signal_watch", "tue_thu")
        n_undeclared = prune_undeclared_ratings(conn, "signal_watch", cfg)
        n_unrated = prune_unrated_ratings(
            conn, "signal_watch", {"fvg": {"1h": 3}}, None
        )

        assert (n_stale, n_undeclared, n_unrated) == (1, 1, 1)
        assert self._cells(conn, "signal_watch") == {("fvg", "1h", "combined")}
        conn.close()


# ---------------------------------------------------------------------------
# compute_dsr_ratings — Deflated Sharpe annotation (P0a-2 sub-PR 3)
# ---------------------------------------------------------------------------


def _seed_cell(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    strategy: str,
    tf: str,
    trades: list[tuple[str, float]],
    *,
    symbol: str = "BTCUSDT",
    day_filter: str = "off",
    run_at_ms: int = 1000,
) -> None:
    """Insert one backtest_run + its per-trade rows. ``trades`` is (direction, pnl_r)."""
    n = len(trades)
    wins = sum(1 for _, r in trades if r > 0)
    avg_r = sum(r for _, r in trades) / n if n else 0.0
    conn.execute(
        "INSERT INTO backtest_runs "
        "(run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, days, "
        "sl_pct, tp_r, fee_pct, day_filter, smt_trend_filter, total_signals, "
        "closed_trades, win_count, loss_count, win_rate, avg_r, total_r, "
        "max_drawdown_r, run_at_ms) VALUES "
        "(?, ?, ?, ?, 0, 1, 90, 0.02, 2.0, 0.0, ?, 1, ?, ?, ?, ?, ?, ?, ?, 0.0, ?)",
        [
            run_id,
            symbol,
            tf,
            strategy,
            day_filter,
            n,
            n,
            wins,
            n - wins,
            wins / n if n else 0.0,
            avg_r,
            avg_r * n,
            run_at_ms,
        ],
    )
    for i, (direction, pnl_r) in enumerate(trades):
        conn.execute(
            "INSERT INTO backtest_trades "
            "(trade_id, run_id, symbol, timeframe, strategy, direction, signal_time, "
            "entry_time, entry_price, sl_price, tp_price, outcome, pnl_r) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, 100.0, 99.0, 102.0, ?, ?)",
            [
                f"{run_id}-{i}",
                run_id,
                symbol,
                tf,
                strategy,
                direction,
                i,
                i,
                "win" if pnl_r > 0 else "loss",
                pnl_r,
            ],
        )


# A consistently-positive R stream (sr ~ 0.35 over 30 trades): single-cell DSR is high.
_POS_STREAM = [("long", 1.0)] * 20 + [("long", -1.0)] * 10


class TestComputeDsrRatings:
    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_empty_db_returns_empty_dict(self) -> None:
        conn = self._conn()
        assert compute_dsr_ratings(conn) == {}
        conn.close()

    def test_run_without_trades_returns_empty(self) -> None:
        conn = self._conn()
        # run row but zero backtest_trades rows
        conn.execute(
            "INSERT INTO backtest_runs "
            "(run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, days, "
            "sl_pct, tp_r, fee_pct, day_filter, smt_trend_filter, total_signals, "
            "closed_trades, win_count, loss_count, win_rate, avg_r, total_r, "
            "max_drawdown_r, run_at_ms) VALUES "
            "('r0', 'BTCUSDT', '1h', 'fvg', 0, 1, 90, 0.02, 2.0, 0.0, 'off', 1, "
            "10, 10, 6, 4, 0.6, 0.3, 3.0, 1.0, 1000)"
        )
        assert compute_dsr_ratings(conn) == {}
        conn.close()

    def test_tiny_n_degenerate_cell_does_not_poison_family(self) -> None:
        """A 2-trade near-identical-loss cell has a huge-magnitude per-trade Sharpe.
        It must be excluded from the trial family (n below min_trades) rather than
        inflating the deflation benchmark and collapsing every cell's DSR to ~0."""
        conn = self._conn()
        _seed_cell(conn, "good", "fvg", "1h", _POS_STREAM)  # n=30
        _seed_cell(conn, "deg", "bos", "15m", [("long", -1.0), ("long", -1.001)])
        result = compute_dsr_ratings(conn)
        conn.close()
        # well-sampled cell keeps its high DSR; degenerate cell is not annotated
        assert result["fvg"]["1h"]["combined"] is not None
        assert result["fvg"]["1h"]["combined"] > 0.9
        assert result["bos"]["15m"]["combined"] is None

    def test_consistent_positive_cell_has_high_dsr(self) -> None:
        conn = self._conn()
        _seed_cell(conn, "r1", "fvg", "1h", _POS_STREAM)
        result = compute_dsr_ratings(conn)
        conn.close()
        dsr = result["fvg"]["1h"]["combined"]
        assert dsr is not None
        assert dsr > 0.9
        # all-long stream → long equals combined, short undefined
        assert result["fvg"]["1h"]["long"] == pytest.approx(dsr)
        assert result["fvg"]["1h"]["short"] is None

    def test_dsr_is_deflated_within_a_larger_family(self) -> None:
        """The same cell's DSR drops once it sits inside a dispersed trial family —
        proves the deflation uses the per-pass cell family, not a bare PSR."""
        alone = self._conn()
        _seed_cell(alone, "r1", "fvg", "1h", _POS_STREAM)
        dsr_alone = compute_dsr_ratings(alone)["fvg"]["1h"]["combined"]
        alone.close()

        crowded = self._conn()
        _seed_cell(crowded, "r1", "fvg", "1h", _POS_STREAM)
        # five other 30-trade cells with varied win rates → varied Sharpes →
        # non-zero cross-trial variance (all clear MIN_DSR_TRADES so they count)
        for k, wins in enumerate([10, 12, 14, 16, 18]):
            stream = [("long", 1.0)] * wins + [("long", -1.0)] * (30 - wins)
            _seed_cell(crowded, f"r{k + 2}", f"s{k}", "1h", stream)
        dsr_crowded = compute_dsr_ratings(crowded)["fvg"]["1h"]["combined"]
        crowded.close()

        assert dsr_alone is not None and dsr_crowded is not None
        assert dsr_crowded < dsr_alone

    def test_directional_split_computed_independently(self) -> None:
        conn = self._conn()
        trades = [("long", 1.0)] * 12 + [("long", -1.0)] * 4
        trades += [("short", -1.0)] * 12 + [("short", 1.0)] * 4
        _seed_cell(conn, "r1", "bos", "4h", trades)
        # min_trades=5 so the 16-trade directional slices qualify in this small seed
        result = compute_dsr_ratings(conn, min_trades=5)["bos"]["4h"]
        conn.close()
        assert result["long"] is not None
        assert result["short"] is not None
        # long edge is positive, short edge is negative → long DSR > short DSR
        assert result["long"] > result["short"]

    def test_uses_only_latest_run_per_cell(self) -> None:
        """An older run with a losing stream must not pollute the newer winning run."""
        conn = self._conn()
        _seed_cell(conn, "old", "fvg", "1h", [("long", -1.0)] * 30, run_at_ms=500)
        _seed_cell(conn, "new", "fvg", "1h", _POS_STREAM, run_at_ms=2000)
        dsr = compute_dsr_ratings(conn)["fvg"]["1h"]["combined"]
        conn.close()
        # dedup → newer winning stream wins → high DSR (older losers would tank it)
        assert dsr is not None and dsr > 0.9

    def test_respects_day_filter_scope(self) -> None:
        conn = self._conn()
        _seed_cell(conn, "a", "fvg", "1h", _POS_STREAM, day_filter="off")
        _seed_cell(conn, "b", "bos", "4h", _POS_STREAM, day_filter="tue_thu")
        result = compute_dsr_ratings(conn, day_filter="tue_thu")
        conn.close()
        assert "bos" in result
        assert "fvg" not in result


# A degenerate cell in the `bos/1d/long` shape: 33 trades that all resolved at
# ~ -1.0075R, sd ~ 0.0005. Its Sharpe is ~ -2000, which is not a signal — it is a
# cell where every trade hit the same stop. See ST66 /
# docs/audits/2026-08-24-st63-occurrence-dump-power-pricing.md.
_DEGENERATE_STREAM = [("long", -1.007), ("long", -1.008)] * 16 + [("long", -1.0075)]


class TestDsrDispersionFloor:
    """MIN_DSR_SD gates dispersion beside MIN_DSR_TRADES' count floor.

    Without it a cell whose trades all resolved at the same R clears the count
    floor, earns a Sharpe in the hundreds, and joins the trial family that every
    other cell is deflated against — measured at 1,766 cells, that moves the
    family's sr_variance from 0.1022 to 9.79e26.
    """

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def _healthy_family(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Five dispersed 30-trade cells, so the trial family has real variance."""
        for k, wins in enumerate([10, 12, 14, 16, 18]):
            stream = [("long", 1.0)] * wins + [("long", -1.0)] * (30 - wins)
            _seed_cell(conn, f"r{k + 2}", f"s{k}", "1h", stream)

    # -- the floor itself ---------------------------------------------------

    def test_degenerate_dispersion_has_no_sharpe(self) -> None:
        returns = [r for _, r in _DEGENERATE_STREAM]
        assert statistics.stdev(returns) < MIN_DSR_SD
        assert _sharpe(returns) is None

    def test_zero_dispersion_still_has_no_sharpe(self) -> None:
        """The pre-ST66 behaviour is preserved, not replaced."""
        assert _sharpe([-1.0] * 30) is None

    def test_dispersed_returns_keep_their_sharpe(self) -> None:
        """Specificity control — the floor must not be blanket.

        Without this a `return None` would satisfy every other test here.
        """
        returns = [r for _, r in _POS_STREAM]
        assert statistics.stdev(returns) > MIN_DSR_SD
        sr = _sharpe(returns)
        assert sr is not None
        assert sr == pytest.approx(0.3476, abs=1e-3)

    def test_floor_is_tunable_and_recovers_the_old_behaviour(self) -> None:
        """Proves the parameter reaches, and documents what it changed."""
        returns = [r for _, r in _DEGENERATE_STREAM]
        sr = _sharpe(returns, min_sd=0.0)
        assert sr is not None
        assert sr < -1000  # the un-floored Sharpe this cell used to contribute

    # -- what the floor is FOR: family poisoning ----------------------------

    def test_degenerate_cell_does_not_poison_the_trial_family(self) -> None:
        """A healthy cell's DSR must not move because a degenerate cell exists.

        This is the whole point of the floor. Un-floored, the degenerate cell's
        Sharpe (~ -2000) detonates the family variance every other cell is
        deflated against.
        """
        clean = self._conn()
        _seed_cell(clean, "r1", "fvg", "1h", _POS_STREAM)
        self._healthy_family(clean)
        dsr_clean = compute_dsr_ratings(clean)["fvg"]["1h"]["combined"]
        clean.close()

        poisoned = self._conn()
        _seed_cell(poisoned, "r1", "fvg", "1h", _POS_STREAM)
        self._healthy_family(poisoned)
        _seed_cell(poisoned, "rdeg", "bos", "1d", _DEGENERATE_STREAM)
        dsr_poisoned = compute_dsr_ratings(poisoned)["fvg"]["1h"]["combined"]
        poisoned.close()

        assert dsr_clean is not None and dsr_poisoned is not None
        assert dsr_poisoned == pytest.approx(dsr_clean)

    def test_the_degenerate_cell_is_itself_annotated_none(self) -> None:
        """It is excluded from the family AND unscored — same as the count floor."""
        conn = self._conn()
        _seed_cell(conn, "r1", "fvg", "1h", _POS_STREAM)
        self._healthy_family(conn)
        _seed_cell(conn, "rdeg", "bos", "1d", _DEGENERATE_STREAM)
        result = compute_dsr_ratings(conn)
        conn.close()
        assert result["bos"]["1d"]["combined"] is None
        assert result["bos"]["1d"]["long"] is None

    def test_min_sd_threads_through_compute_dsr_ratings(self) -> None:
        """min_sd=0.0 restores the pre-ST66 result, so the fix is attributable."""
        conn = self._conn()
        _seed_cell(conn, "r1", "fvg", "1h", _POS_STREAM)
        self._healthy_family(conn)
        _seed_cell(conn, "rdeg", "bos", "1d", _DEGENERATE_STREAM)
        result = compute_dsr_ratings(conn, min_sd=0.0)
        conn.close()
        assert result["bos"]["1d"]["combined"] is not None


# ---------------------------------------------------------------------------


def _insert_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    strategy: str,
    tf: str,
    run_at_ms: int,
    sweep_id: str | None,
    avg_r: float,
    closed_trades: int = 100,
) -> None:
    """Insert one backtest_runs row with only the fields these tests read."""
    conn.execute(
        "INSERT INTO backtest_runs VALUES "
        "(?, ?, ?, ?, 0, 1, 90, 0.02, 2.0, 0.0005, 'off', 1, NULL, ?, ?, ?, ?, ?, ?, "
        "?, 1.0, ?, ?, "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        [
            run_id,
            "BTCUSDT",
            tf,
            strategy,
            closed_trades,
            closed_trades,
            closed_trades // 2,
            closed_trades - closed_trades // 2,
            0.5,
            avg_r,
            avg_r * closed_trades,
            run_at_ms,
            sweep_id,
        ],
    )


class TestSweepRowsWinOverRecency:
    """A swept row must outrank a newer live-gate row.

    Writer identity alone is not enough. Both selection sites kept "the latest run
    per (strategy, timeframe, symbol)" by `run_at_ms`, so once the two writers stop
    COLLIDING they simply coexist — and the 15-minute daemon's row is always the
    newest. That converts a destructive overwrite into a silent preference, leaving
    53% of rated cells still sourced from the live gate rather than the deliberate
    sweep. The sweep row is the validated evidence; recency is not the tiebreak.
    """

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_win_rates_prefer_the_sweep_row(self) -> None:
        conn = self._conn()
        _insert_run(conn, "sweep1", "bos", "15m", 1_000, "sweep-abc", avg_r=0.40)
        _insert_run(conn, "live1", "bos", "15m", 9_999, None, avg_r=-0.90)

        df = get_backtest_win_rates(conn, day_filter="off")
        row = df[(df["strategy"] == "bos") & (df["timeframe"] == "15m")].iloc[0]
        assert row["avg_r"] == pytest.approx(0.40), (
            "the newer live-gate row won; sweep provenance was ignored"
        )

    def test_latest_sweep_wins_among_sweeps(self) -> None:
        conn = self._conn()
        _insert_run(conn, "old", "bos", "15m", 1_000, "sweep-old", avg_r=0.10)
        _insert_run(conn, "new", "bos", "15m", 2_000, "sweep-new", avg_r=0.60)

        df = get_backtest_win_rates(conn, day_filter="off")
        row = df[(df["strategy"] == "bos") & (df["timeframe"] == "15m")].iloc[0]
        assert row["avg_r"] == pytest.approx(0.60)

    def test_live_row_still_used_when_no_sweep_exists(self) -> None:
        conn = self._conn()
        _insert_run(conn, "live1", "bos", "15m", 9_999, None, avg_r=-0.90)

        df = get_backtest_win_rates(conn, day_filter="off")
        row = df[(df["strategy"] == "bos") & (df["timeframe"] == "15m")].iloc[0]
        assert row["avg_r"] == pytest.approx(-0.90), (
            "a cell with only live-gate rows must still be rated, not dropped"
        )

    def test_dsr_pools_trades_from_the_sweep_row(self) -> None:
        conn = self._conn()
        _insert_run(conn, "sweep1", "bos", "15m", 1_000, "sweep-abc", avg_r=0.40)
        _insert_run(conn, "live1", "bos", "15m", 9_999, None, avg_r=-0.90)
        # 40 trades under the sweep run, none under the live run. The pnl_r values
        # must VARY: `_sharpe` returns None at zero dispersion, so a constant-R
        # fixture would make this test pass-by-degeneracy and assert nothing.
        conn.executemany(
            "INSERT INTO backtest_trades (trade_id, run_id, symbol, timeframe, "
            "strategy, direction, signal_time, entry_time, entry_price, sl_price, "
            "tp_price, outcome, pnl_r) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [
                    f"t{i}",
                    "sweep1",
                    "BTCUSDT",
                    "15m",
                    "bos",
                    "long",
                    1_000 + i,
                    1_000 + i,
                    100.0,
                    98.0,
                    104.0,
                    "tp" if i % 3 else "sl",
                    0.5 if i % 3 else -1.0,
                ]
                for i in range(40)
            ],
        )
        out = compute_dsr_ratings(conn, day_filter="off")
        assert out.get("bos", {}).get("15m", {}).get("combined") is not None, (
            "DSR read the live row's run_id and found no trades to pool"
        )


# ---------------------------------------------------------------------------
# get_backtest_win_rates — avg_r must be TRADE-WEIGHTED across symbols
# ---------------------------------------------------------------------------


def _insert_weighted_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    symbol: str,
    timeframe: str,
    strategy: str,
    closed_trades: int,
    win_count: int,
    avg_r: float,
    long_closed_trades: int = 0,
    long_win_count: int = 0,
    long_avg_r: float | None = None,
    short_closed_trades: int = 0,
    short_win_count: int = 0,
    short_avg_r: float | None = None,
) -> None:
    """Insert one backtest_runs row by NAME, so column order cannot silently drift."""
    conn.execute(
        "INSERT INTO backtest_runs (run_id, symbol, timeframe, strategy, "
        "data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter, "
        "smt_trend_filter, secondary_symbol, total_signals, closed_trades, "
        "win_count, loss_count, win_rate, avg_r, total_r, max_drawdown_r, "
        "run_at_ms, sweep_id, long_closed_trades, long_win_count, long_avg_r, "
        "short_closed_trades, short_win_count, short_avg_r) VALUES "
        "(?, ?, ?, ?, 0, 1, 90, 0.02, 2.0, 0.0005, 'off', 1, NULL, ?, ?, ?, ?, "
        "?, ?, ?, 0.0, 1000, NULL, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            symbol,
            timeframe,
            strategy,
            closed_trades,
            closed_trades,
            win_count,
            closed_trades - win_count,
            (win_count / closed_trades) if closed_trades else 0.0,
            avg_r,
            avg_r * closed_trades,
            long_closed_trades,
            long_win_count,
            long_avg_r,
            short_closed_trades,
            short_win_count,
            short_avg_r,
        ],
    )


class TestAvgRIsTradeWeighted:
    """``avg_r`` aggregates across symbols weighted by trade count.

    An unweighted ``.mean()`` gives a 3-trade symbol the same say as a 300-trade
    one. The star map has a boundary at exactly 0.0 (``avg_r < 0 -> 1 star``), so
    on real data this flipped whole cells across the sign — ``fvg/1d`` read
    +0.3539 unweighted against -0.3819 trade-weighted. ``win_rate`` in this same
    aggregation was already weighted (sum of wins / sum of trades); only the R
    columns were not.
    """

    def _make_conn(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        init_schema(conn)
        return conn

    def test_combined_avg_r_is_weighted_by_trade_count(self) -> None:
        conn = self._make_conn()
        # 100 trades at -0.5R and 4 trades at +2.0R.
        # unweighted mean = (-0.5 + 2.0) / 2   = +0.75  (positive → 4 stars)
        # weighted mean = (-50 + 8) / 104      = -0.4038 (negative → 1 star)
        _insert_weighted_run(
            conn,
            run_id="w1",
            symbol="BTCUSDT",
            timeframe="1d",
            strategy="fvg",
            closed_trades=100,
            win_count=30,
            avg_r=-0.5,
        )
        _insert_weighted_run(
            conn,
            run_id="w2",
            symbol="ETHUSDT",
            timeframe="1d",
            strategy="fvg",
            closed_trades=4,
            win_count=3,
            avg_r=2.0,
        )
        df = get_backtest_win_rates(conn)
        conn.close()

        row = df[(df["strategy"] == "fvg") & (df["timeframe"] == "1d")].iloc[0]
        assert row["total_trades"] == 104
        assert row["avg_r"] == pytest.approx(-0.4038, abs=1e-4)

    def test_directional_avg_r_is_weighted_by_directional_trade_count(self) -> None:
        """Long/short columns weight by their OWN counts, not the combined one."""
        conn = self._make_conn()
        _insert_weighted_run(
            conn,
            run_id="d1",
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="bos",
            closed_trades=100,
            win_count=40,
            avg_r=0.1,
            long_closed_trades=90,
            long_win_count=36,
            long_avg_r=-0.4,
            short_closed_trades=10,
            short_win_count=4,
            short_avg_r=0.9,
        )
        _insert_weighted_run(
            conn,
            run_id="d2",
            symbol="ETHUSDT",
            timeframe="1h",
            strategy="bos",
            closed_trades=20,
            win_count=8,
            avg_r=0.2,
            long_closed_trades=10,
            long_win_count=4,
            long_avg_r=0.8,
            short_closed_trades=10,
            short_win_count=4,
            short_avg_r=0.9,
        )
        df = get_backtest_win_rates(conn)
        conn.close()

        row = df[(df["strategy"] == "bos") & (df["timeframe"] == "1h")].iloc[0]
        # long: (90*-0.4 + 10*0.8) / 100 = -0.28   (unweighted would be +0.20)
        assert row["long_avg_r"] == pytest.approx(-0.28, abs=1e-4)
        # short: both symbols agree at 0.9, so weighting cannot change it —
        # a control proving the long result above is weighting, not arithmetic drift
        assert row["short_avg_r"] == pytest.approx(0.9, abs=1e-4)

    def test_symbol_with_no_directional_trades_does_not_drag_the_mean(self) -> None:
        """A NULL directional avg_r carries zero weight rather than counting as 0.0."""
        conn = self._make_conn()
        _insert_weighted_run(
            conn,
            run_id="n1",
            symbol="BTCUSDT",
            timeframe="4h",
            strategy="fvg",
            closed_trades=50,
            win_count=25,
            avg_r=0.5,
            long_closed_trades=50,
            long_win_count=25,
            long_avg_r=0.5,
            short_closed_trades=0,
            short_win_count=0,
            short_avg_r=None,
        )
        _insert_weighted_run(
            conn,
            run_id="n2",
            symbol="ETHUSDT",
            timeframe="4h",
            strategy="fvg",
            closed_trades=10,
            win_count=2,
            avg_r=-0.6,
            long_closed_trades=0,
            long_win_count=0,
            long_avg_r=None,
            short_closed_trades=10,
            short_win_count=2,
            short_avg_r=-0.6,
        )
        df = get_backtest_win_rates(conn)
        conn.close()

        row = df[(df["strategy"] == "fvg") & (df["timeframe"] == "4h")].iloc[0]
        # Only BTCUSDT has long trades → the long mean is exactly its own
        assert row["long_avg_r"] == pytest.approx(0.5, abs=1e-4)
        # Only ETHUSDT has short trades → likewise
        assert row["short_avg_r"] == pytest.approx(-0.6, abs=1e-4)
        # combined: (50*0.5 + 10*-0.6) / 60 = 0.3167
        assert row["avg_r"] == pytest.approx(0.3167, abs=1e-4)
