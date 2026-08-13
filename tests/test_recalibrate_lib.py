"""Tests for analytics/recalibrate_lib.py."""

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
    PruneThresholdExceeded,
    compute_directional_ratings,
    compute_dsr_ratings,
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
    prune_stale_ratings,
    prune_undeclared_ratings,
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
