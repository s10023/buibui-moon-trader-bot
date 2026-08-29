"""Tests for `tools/st104_sweep.py` — the ST104 eqh_eql retune runner.

The two that carry weight are `test_baseline_arm_matches_detector_defaults`
(the control cannot silently become a fourth treatment) and
`test_merge_lands_values_in_the_named_column_when_dest_order_differs` (the
mutation case: a positional copy passes every other test here).
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pytest

from analytics.store import (
    init_schema,
    upsert_backtest_run,
    upsert_backtest_trades,
)
from analytics.strategies.eqh_eql import detect_eqh_eql
from tools.st104_sweep import (
    ARMS,
    STRATEGY,
    all_arm_params_json,
    arm_params_json,
    merge_study_rows,
    symbols_for,
    table_columns,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _one(conn: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    row = conn.execute(sql).fetchone()
    assert row is not None, sql
    return row


@dataclass
class _Trade:
    signal_time: int = 1_700_000_000_000
    entry_time: int = 1_700_003_600_000
    entry_price: float = 30_000.0
    direction: str = "long"
    sl_price: float = 29_400.0
    tp_price: float = 31_200.0
    exit_time: int | None = 1_700_007_200_000
    exit_price: float | None = 31_200.0
    outcome: str = "win"
    pnl_r: float | None = 2.0
    low_volume: bool = False
    volume_spike: bool = False


@dataclass
class _Result:
    """The attribute surface `upsert_backtest_run` reads off a sweep result."""

    symbol: str = "BTCUSDT"
    timeframe: str = "15m"
    strategy: str = STRATEGY
    fee_pct: float = 0.0
    trades: list[_Trade] = field(default_factory=lambda: [_Trade()])
    win_count: int = 1
    loss_count: int = 0
    win_rate: float = 1.0
    avg_r: float = 2.0
    total_r: float = 2.0
    max_drawdown_r: float = 0.0
    long_win_count: int = 1
    long_win_rate: float = 1.0
    long_avg_r: float = 2.0
    short_win_count: int = 0
    short_win_rate: float = 0.0
    short_avg_r: float = 0.0
    long_total_r: float = 2.0
    short_total_r: float = 0.0
    recovery_factor: float = 0.0

    @property
    def closed_trades(self) -> list[_Trade]:
        return self.trades

    @property
    def long_closed_trades(self) -> list[_Trade]:
        return [t for t in self.trades if t.direction == "long"]

    @property
    def short_closed_trades(self) -> list[_Trade]:
        return [t for t in self.trades if t.direction == "short"]


_BT_PARAMS: dict[str, Any] = {
    "days": 90,
    "data_start_ms": 1_690_000_000_000,
    "data_end_ms": 1_700_000_000_000,
    "sl_pct": 0.02,
    "tp_r": 2.0,
    "fee_pct": 0.0,
    "day_filter": "off",
    "smt_trend_filter": 1,
    "secondary_symbol": None,
    "sweep_id": None,
}


def _study_db(path: Path, *, arm: str, adr: float = 0.85) -> None:
    """A study DB holding one saved run for `arm`, with a marker column set."""
    with duckdb.connect(str(path)) as conn:
        init_schema(conn)
        run_id = upsert_backtest_run(
            conn,
            _Result(),
            **_BT_PARAMS,
            adr_suppress_threshold=adr,
            detector_params=ARMS[arm],
        )
        upsert_backtest_trades(conn, _Result(), run_id)


class TestArmDefinitions:
    def test_baseline_arm_matches_detector_defaults(self) -> None:
        """The control must BE the default, not merely resemble it.

        If someone retunes `detect_eqh_eql`'s signature defaults, a baseline arm
        pinned to stale literals stops being a control and becomes a fourth
        treatment — with nothing in the output saying so.
        """
        sig = inspect.signature(detect_eqh_eql)
        defaults = {
            name: p.default
            for name, p in sig.parameters.items()
            if p.default is not inspect.Parameter.empty
        }
        assert ARMS["baseline"] == defaults

    def test_every_arm_carries_explicit_params(self) -> None:
        """None/empty would drop the `|dp:` suffix and collide with production.

        A run_id with no detector_params suffix is byte-identical to the one
        production's own sweeps write, so an empty baseline would overwrite live
        rows — and per ST86 that collision is unmeasurable afterwards.
        """
        for arm, params in ARMS.items():
            assert params, arm
            assert set(params) == {"lookback", "tolerance_pct", "swing_n"}, arm

    def test_each_treatment_moves_exactly_one_knob(self) -> None:
        """Three knobs, three trials — pre-registered separately, no joint arm."""
        base = ARMS["baseline"]
        for arm in ("T1_tol", "T2_look", "T3_swing"):
            changed = [k for k, v in ARMS[arm].items() if base[k] != v]
            assert len(changed) == 1, f"{arm} moved {changed}"

    def test_arm_json_is_sorted_and_distinct(self) -> None:
        blobs = all_arm_params_json()
        assert len(set(blobs)) == len(ARMS)
        assert arm_params_json("T2_look") == (
            '{"lookback": 400, "swing_n": 5, "tolerance_pct": 0.003}'
        )


class TestArmJsonMatchesTheStore:
    def test_stored_detector_params_equals_arm_params_json(
        self, tmp_path: Path
    ) -> None:
        """Selection is by this exact string, so the two serialisations must agree.

        A drift here would not raise — it would silently select zero rows and
        report an empty study as a null result.
        """
        db = tmp_path / "study.db"
        _study_db(db, arm="T3_swing")
        with duckdb.connect(str(db), read_only=True) as conn:
            stored = _one(conn, "SELECT detector_params FROM backtest_runs")[0]
        assert stored == arm_params_json("T3_swing")


class TestMerge:
    def test_runs_only_is_the_default_and_leaves_trades_alone(
        self, tmp_path: Path
    ) -> None:
        """The study is ~810k trades against production's ~865k.

        Copying them would nearly double `backtest_trades`, so every ad-hoc
        aggregate that forgets to join through `run_id` would include the study.
        """
        study, prod = tmp_path / "s.db", tmp_path / "p.db"
        _study_db(study, arm="T1_tol")
        with duckdb.connect(str(prod)) as conn:
            init_schema(conn)
            counts = merge_study_rows(conn, study, all_arm_params_json())
        assert counts == {"backtest_runs": 1, "backtest_trades": 0}

    def test_all_trades_copies_the_per_trade_rows(self, tmp_path: Path) -> None:
        study, prod = tmp_path / "s.db", tmp_path / "p.db"
        _study_db(study, arm="T1_tol")
        with duckdb.connect(str(prod)) as conn:
            init_schema(conn)
            counts = merge_study_rows(
                conn, study, all_arm_params_json(), runs_only=False
            )
        assert counts == {"backtest_runs": 1, "backtest_trades": 1}

    def test_is_idempotent(self, tmp_path: Path) -> None:
        """Delete-then-insert, so a re-merge is a no-op rather than a PK error."""
        study, prod = tmp_path / "s.db", tmp_path / "p.db"
        _study_db(study, arm="T1_tol")
        with duckdb.connect(str(prod)) as conn:
            init_schema(conn)
            merge_study_rows(conn, study, all_arm_params_json(), runs_only=False)
            counts = merge_study_rows(
                conn, study, all_arm_params_json(), runs_only=False
            )
        assert counts == {"backtest_runs": 1, "backtest_trades": 1}

    def test_leaves_production_rows_alone(self, tmp_path: Path) -> None:
        """Only the study's own detector_params namespace is touched."""
        study, prod = tmp_path / "s.db", tmp_path / "p.db"
        _study_db(study, arm="T1_tol")
        with duckdb.connect(str(prod)) as conn:
            init_schema(conn)
            # A production row: same strategy, NULL detector_params.
            upsert_backtest_run(conn, _Result(), **_BT_PARAMS)
            merge_study_rows(conn, study, all_arm_params_json())
            live = _one(
                conn,
                "SELECT COUNT(*) FROM backtest_runs WHERE detector_params IS NULL",
            )[0]
        assert live == 1

    def test_lands_values_in_the_named_column_when_dest_order_differs(
        self, tmp_path: Path
    ) -> None:
        """MUTATION CASE — a positional `SELECT *` passes every other test here.

        `backtest_runs` has no single column order: some columns are created
        inline by `init_schema` and others arrive through the ALTER migration, so
        a database predating the CREATE orders them differently from a fresh one.
        Here the destination's order is reversed; a positional copy reads
        `adr_suppress_threshold` out of whichever column happens to sit at that
        index, while a named copy is indifferent to order.
        """
        study, prod = tmp_path / "s.db", tmp_path / "p.db"
        _study_db(study, arm="T2_look", adr=0.85)

        with duckdb.connect(str(prod)) as conn:
            init_schema(conn)
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'backtest_runs' ORDER BY ordinal_position"
            ).fetchall()
            reordered = ", ".join(f"{c} {t}" for c, t in reversed(cols))
            conn.execute("DROP TABLE backtest_runs")
            conn.execute(f"CREATE TABLE backtest_runs ({reordered})")
            assert table_columns(conn, "backtest_runs") != [c for c, _ in cols]

            merge_study_rows(conn, study, all_arm_params_json())
            row = _one(
                conn,
                "SELECT adr_suppress_threshold, strategy, detector_params "
                "FROM backtest_runs",
            )
        assert row[0] == pytest.approx(0.85)
        assert row[1] == STRATEGY
        assert row[2] == arm_params_json("T2_look")

    def test_refuses_when_the_study_db_lacks_a_destination_column(
        self, tmp_path: Path
    ) -> None:
        """A missing column must raise, never fall back to a positional copy."""
        study, prod = tmp_path / "s.db", tmp_path / "p.db"
        _study_db(study, arm="T1_tol")
        with duckdb.connect(str(study)) as conn:
            conn.execute("ALTER TABLE backtest_runs DROP COLUMN recovery_factor")
        with duckdb.connect(str(prod)) as conn:
            init_schema(conn)
            with pytest.raises(RuntimeError, match="recovery_factor"):
                merge_study_rows(conn, study, all_arm_params_json())


class TestSymbolsFor:
    def test_reads_symbols_from_the_data_not_the_universe(self, tmp_path: Path) -> None:
        """15m exists for the majors only; a universe-shaped list backtests nothing."""
        db = tmp_path / "s.db"
        with duckdb.connect(str(db)) as conn:
            init_schema(conn)
            conn.execute(
                "INSERT INTO ohlcv_all (symbol, timeframe, open_time, open, high, "
                "low, close, volume, venue) VALUES "
                "('BTCUSDT','15m',1,1,1,1,1,1,'binance'), "
                "('ETHUSDT','15m',1,1,1,1,1,1,'binance'), "
                "('ADAUSDT','1h',1,1,1,1,1,1,'binance')"
            )
            assert symbols_for(conn, "15m") == ["BTCUSDT", "ETHUSDT"]
            assert symbols_for(conn, "1h") == ["ADAUSDT"]


def test_bare_invocation_works(tmp_path: Path) -> None:
    """`python3 tools/st104_sweep.py` with no PYTHONPATH — how CI would call it."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "st104_sweep.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "ST104" in proc.stdout


class TestArmCausality:
    """Spec P3 — the causality gate, run per arm rather than at stock params.

    `tests/test_lookahead.py` feeds each `DETECTOR_REGISTRY` entry its DEFAULT
    keywords, so a green suite there says nothing about an arm that moves
    `swing_n` — and `swing_n` sets the width of a CENTRED window, which is
    exactly the shape that made `bos` non-causal on 100% of its signals. The
    harness is reused rather than reimplemented so both read the same definition
    of a violation.

    A skip here means UNTESTED, not clean: `_first_lookahead_violation` returns a
    tested count precisely so those two cannot be confused, and T2's 400-bar
    lookback needs more history than the 1d fixture holds.
    """

    @pytest.mark.parametrize("tf", ["4h", "1d"])
    @pytest.mark.parametrize("arm", sorted(ARMS))
    def test_arm_has_no_lookahead(self, arm: str, tf: str) -> None:
        from functools import partial

        from tests.test_lookahead import _first_lookahead_violation, _load_fixture

        params = ARMS[arm]
        detector = partial(
            detect_eqh_eql,
            lookback=int(params["lookback"]),
            tolerance_pct=float(params["tolerance_pct"]),
            swing_n=int(params["swing_n"]),
        )
        df = _load_fixture(tf)
        if detector(df).empty:
            pytest.skip(f"{arm} emits no signals on the {tf} fixture")
        violation, tested = _first_lookahead_violation(detector, df)
        if tested == 0:
            pytest.skip(f"{arm} on {tf}: no signal has enough history — UNTESTED")
        assert violation is None, (
            f"{arm} on {tf}: signals at open_time={violation} differ between the "
            f"full-series and truncated runs — this arm reads future bars."
        )
