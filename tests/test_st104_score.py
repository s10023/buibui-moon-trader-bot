"""Tests for `tools/st104_score.py` — the ST104 scoring pass.

The two that carry weight are `test_degenerate_dispersion_is_not_a_signal` (ST66:
a cell whose trades all resolved at the same R would otherwise earn a Sharpe in
the hundreds off a ~0 denominator) and
`test_ambiguous_counts_only_bars_spanning_both_levels` (ST56/ST57: the tie-break
exposure count is the thing that says which way the bias runs).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from analytics.store import init_schema
from tools.st104_score import (
    POWER_BAR,
    UNREACHABLE_N,
    Cell,
    _sharpe,
    _verdict,
    bar_for,
    daily_matrix,
    load_trades,
    score,
)
from tools.st104_sweep import ARMS, STRATEGY, arm_params_json

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY_MS = 86_400_000


_TRADE_COLS = (
    "trade_id",
    "run_id",
    "symbol",
    "timeframe",
    "strategy",
    "direction",
    "signal_time",
    "entry_time",
    "entry_price",
    "sl_price",
    "tp_price",
    "exit_time",
    "exit_price",
    "outcome",
    "pnl_r",
)


def _insert_trades(conn: duckdb.DuckDBPyConnection, rows: list[list[Any]]) -> None:
    """Load `rows` into `backtest_trades` as ONE columnar insert.

    ST125 — the row-at-a-time form this replaces ran 3.35s at n=800, which reads as
    a safe 9x margin under pytest's global `timeout = 30` and is not one: it timed
    out on main the first time two merges landed 41 seconds apart and shared a
    free-tier runner, and a timed-out test is indistinguishable from real drift (the
    same cap AGENTS.md documents for the golden backtests). DuckDB is columnar, so
    `executemany` is still a round trip per row and only bought 3x; a registered
    frame buys 46x.

    ⚠ The column list is named on BOTH sides on purpose. `INSERT ... SELECT *` maps
    by POSITION, and `backtest_trades` has no single column order — `init_schema`
    creates three columns inline that an ALTER migration appends on an older DB — so
    the star form reads a value out of a column five places away on one shape and
    not the other (AGENTS.md, measured 2026-08-25).
    """
    frame = pd.DataFrame(rows, columns=list(_TRADE_COLS))
    cols = ", ".join(_TRADE_COLS)
    conn.register("_seed_trades", frame)
    try:
        conn.execute(
            f"INSERT INTO backtest_trades ({cols}) SELECT {cols} FROM _seed_trades"
        )
    finally:
        conn.unregister("_seed_trades")


def _seed(
    conn: duckdb.DuckDBPyConnection,
    arm: str,
    *,
    n: int,
    tf: str = "15m",
    direction: str = "short",
    mean: float = 0.0,
    spread: float = 1.0,
    days: int = 400,
    fee_pct: float = 0.0,
) -> None:
    """One run row plus `n` synthetic trades for `arm`, spread over `days`."""
    run_id = f"run-{arm}-{tf}-{direction}"
    conn.execute(
        "INSERT INTO backtest_runs (run_id, symbol, timeframe, strategy, "
        "data_start_ms, data_end_ms, days, "
        "sl_pct, tp_r, fee_pct, day_filter, smt_trend_filter, total_signals, "
        "closed_trades, win_count, loss_count, win_rate, avg_r, total_r, "
        "max_drawdown_r, run_at_ms, detector_params) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            run_id,
            "BTCUSDT",
            tf,
            STRATEGY,
            1_700_000_000_000,
            1_700_000_000_000 + days * DAY_MS,
            90,
            0.02,
            2.0,
            fee_pct,
            "off",
            1,
            n,
            n,
            0,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            1_700_000_000_000,
            arm_params_json(arm),
        ],
    )
    rng = np.random.default_rng(7)
    rows: list[list[Any]] = []
    for i in range(n):
        entry = 1_700_000_000_000 + (i % days) * DAY_MS + i * 1_000
        pnl = mean + (spread * float(rng.standard_normal()) if spread else 0.0)
        rows.append(
            [
                f"{run_id}-{i}",
                run_id,
                "BTCUSDT",
                tf,
                STRATEGY,
                direction,
                entry,
                entry,
                100.0,
                98.0,
                104.0,
                entry + 3_600_000,
                101.0,
                "win" if pnl > 0 else "loss",
                pnl,
            ]
        )
    _insert_trades(conn, rows)


class TestBar:
    def test_clamps_rather_than_extrapolating(self) -> None:
        ns = sorted(POWER_BAR)
        assert bar_for(1.0) == POWER_BAR[ns[0]]
        assert bar_for(10_000_000.0) == POWER_BAR[ns[-1]]

    def test_falls_monotonically_with_effective_n(self) -> None:
        values = [bar_for(n) for n in (250, 400, 800, 1500, 3000, 4000)]
        assert values == sorted(values, reverse=True)

    def test_interpolates_between_pinned_rows(self) -> None:
        assert POWER_BAR[2000] < bar_for(1400) < POWER_BAR[1000]


class TestSharpe:
    def test_degenerate_dispersion_is_not_a_signal(self) -> None:
        """ST66 — every trade at the same R clears a count floor, not a bar.

        Without the floor this returns a Sharpe of several hundred, which took a
        real DSR family's variance from 0.0181 to 3937 and zeroed all 18
        scoreable long-scope DSRs.
        """
        assert math_isnan(_sharpe(np.full(500, -1.0)))
        assert math_isnan(_sharpe(np.full(500, -1.0) + 1e-9))

    def test_scores_a_dispersed_cell(self) -> None:
        rng = np.random.default_rng(1)
        r = rng.standard_normal(50_000) * 1.4 + 0.2
        assert _sharpe(r) == pytest.approx(0.2 / 1.4, abs=0.02)

    def test_needs_two_observations(self) -> None:
        assert math_isnan(_sharpe(np.asarray([1.0])))


def math_isnan(x: float) -> bool:
    return x != x


class TestDailyMatrix:
    def test_none_below_the_pbo_block_floor(self) -> None:
        arms = {
            "a": (np.ones(10), list(range(10))),
            "b": (np.ones(10), list(range(10))),
        }
        assert daily_matrix(arms) is None

    def test_restricts_to_days_every_trial_shares(self) -> None:
        """A day only one arm trades cannot be a row — the columns must align."""
        arms = {
            "a": (np.ones(60), list(range(60))),
            "b": (np.ones(60), list(range(20, 80))),
        }
        matrix = daily_matrix(arms)
        assert matrix is not None
        assert matrix.shape == (40, 2)

    def test_none_with_a_single_trial(self) -> None:
        assert daily_matrix({"a": (np.ones(60), list(range(60)))}) is None


class TestAmbiguousCount:
    def test_counts_only_bars_spanning_both_levels(self, tmp_path: Path) -> None:
        """ST56/ST57 — the exit bar must reach the stop AND the target.

        A bar touching one level is resolved unambiguously; only a bar spanning
        both is decided by the adverse-first rule, so only those belong in the
        exposure count that names the bias direction.
        """
        db = tmp_path / "s.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
            _seed(conn, "baseline", n=3)
            exits = [
                r[0]
                for r in conn.execute(
                    "SELECT exit_time FROM backtest_trades ORDER BY trade_id"
                ).fetchall()
            ]
            # sl 98 / tp 104: spanning, low-only, high-only.
            for exit_time, (low, high) in zip(
                exits, [(97.0, 105.0), (97.0, 103.0), (99.0, 105.0)], strict=True
            ):
                conn.execute(
                    "INSERT INTO ohlcv_all (symbol, timeframe, open_time, open, "
                    "high, low, close, volume, venue) VALUES "
                    "('BTCUSDT','15m',?,100.0,?,?,100.0,1.0,'binance')",
                    [exit_time, high, low],
                )
            got = load_trades(conn, "baseline")
        _, _, ambiguous, _ = got[("15m", "short")]
        assert ambiguous == 1


class TestVerdictTaxonomy:
    """CONFIRMED-BAD outranks NO-EDGE, and neither is a failure-to-clear."""

    def _cell(self, lo: float, hi: float) -> Cell:
        return Cell(
            arm="T1_tol",
            timeframe="15m",
            direction="short",
            n=1000,
            n_clusters=500,
            design_effect=2.0,
            n_eff=500.0,
            avg_r=(lo + hi) / 2,
            avg_r_gross=0.0,
            avg_drag=0.0,
            sd=1.4,
            sharpe=-0.1,
            boot_lo=lo,
            boot_hi=hi,
            bar=0.2,
            ambiguous=0,
            ambiguous_pct=0.0,
            dsr=0.0,
            pbo=1.0,
        )

    def test_ci_wholly_below_zero_is_confirmed_bad(self) -> None:
        assert _verdict(self._cell(-0.31, -0.18), 0.2) == "CONFIRMED-BAD"

    def test_confirmed_bad_outranks_a_powered_null(self) -> None:
        """[-0.15, -0.02] is inside the bar AND wholly negative.

        Reporting that as NO-EDGE would say "no effect was found" about a cell
        that confidently loses money — the stronger statement must win.
        """
        assert _verdict(self._cell(-0.15, -0.02), 0.2) == "CONFIRMED-BAD"

    def test_ci_inside_the_bar_and_spanning_zero_is_no_edge(self) -> None:
        assert _verdict(self._cell(-0.10, 0.10), 0.2) == "NO-EDGE"

    def test_a_wide_ci_is_insufficient_not_a_null(self) -> None:
        """A CI several times the bar has established nothing either way."""
        assert _verdict(self._cell(-0.9, 0.8), 0.2) == "INSUFFICIENT"

    def test_a_missing_gate_leg_still_names_a_verdict(self) -> None:
        """No DSR/PBO cannot mean no verdict — the spec's metric is a NAME."""
        cell = self._cell(-0.9, 0.8)
        cell.dsr, cell.pbo = None, None
        assert _verdict(cell, 0.2) == "INSUFFICIENT"
        cell = self._cell(-0.31, -0.18)
        cell.dsr, cell.pbo = None, None
        assert _verdict(cell, 0.2) == "CONFIRMED-BAD"


class TestCostIsChargedExactlyOnce:
    """`Trade.pnl_r` is already net when the run carried a fee; charging the
    drag again would double-count it, and the drag here is a large multiple of
    every effect under test rather than a rounding term."""

    def _avg(self, tmp_path: Path, fee_pct: float) -> float:
        db = tmp_path / f"s{fee_pct}.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
            _seed(conn, "baseline", n=100, mean=0.0, spread=0.0, fee_pct=fee_pct)
            arr, _, _, _ = load_trades(conn, "baseline")[("15m", "short")]
        return float(arr.mean())

    def test_zero_fee_run_has_the_drag_subtracted(self, tmp_path: Path) -> None:
        # entry 100, sl 98 -> risk 2% -> drag = 0.0014 * 100 / 2 = 0.07R
        assert self._avg(tmp_path, 0.0) == pytest.approx(-0.07, abs=1e-9)

    def test_costed_run_is_left_alone(self, tmp_path: Path) -> None:
        assert self._avg(tmp_path, 0.0005) == pytest.approx(0.0, abs=1e-9)


class TestScore:
    def test_treatment_below_the_screen_floor_is_unreachable(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "s.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
            _seed(conn, "baseline", n=600)
            _seed(conn, "T1_tol", n=UNREACHABLE_N - 1)
            _seed(conn, "T2_look", n=600)
            _seed(conn, "T3_swing", n=600)
            cells = score(conn)
        by_arm = {c.arm: c for c in cells}
        assert by_arm["T1_tol"].verdict == "UNREACHABLE"
        assert by_arm["T2_look"].verdict != "UNREACHABLE"

    def test_every_scored_cell_gets_a_named_verdict(self, tmp_path: Path) -> None:
        """The spec's success metric: a named verdict per arm, not a pass."""
        db = tmp_path / "s.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
            for arm in ARMS:
                _seed(conn, arm, n=800, mean=-0.02)
            cells = score(conn)
        assert len(cells) == 4
        allowed = {"PASS", "CONFIRMED-BAD", "NO-EDGE", "INSUFFICIENT", "UNREACHABLE"}
        for c in cells:
            assert c.verdict in allowed, c
            assert c.n_clusters > 1
            assert c.design_effect >= 1.0

    def test_a_flat_cell_reads_insufficient_not_a_pass(self, tmp_path: Path) -> None:
        """The ST66 floor must reach the verdict, not just the Sharpe helper."""
        db = tmp_path / "s.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
            _seed(conn, "baseline", n=600, mean=-1.0, spread=0.0)
            cells = score(conn)
        assert cells[0].verdict == "INSUFFICIENT"
        assert "dispersion floor" in cells[0].notes


def test_bare_invocation_works(tmp_path: Path) -> None:
    """`python3 tools/st104_score.py` with no PYTHONPATH — how CI would call it."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "st104_score.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "ST104" in proc.stdout
