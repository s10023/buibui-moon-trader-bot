"""``analytics.store.load_backtest_trades`` — the one read path into ``backtest_trades`` (#985)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import duckdb
import pytest

from analytics.eras import DEFAULT_ERAS_PATH, DetectorFloor, detector_floors
from analytics.store import SIGNAL_KEY, init_schema, load_backtest_trades

REPO_ROOT = Path(__file__).resolve().parent.parent
FLOOR_MS = 10_000
FLOORS = {"bos": DetectorFloor("bos", FLOOR_MS, "test", "test")}
COLS = ("run_id", "strategy", "entry_time", "pnl_r")


def _run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    strategy: str,
    at: int,
    closed: int,
) -> None:
    conn.execute(
        "INSERT INTO backtest_runs (run_id, symbol, timeframe, strategy, "
        "data_start_ms, data_end_ms, days, sl_pct, tp_r, fee_pct, day_filter, "
        "smt_trend_filter, total_signals, closed_trades, win_count, loss_count, "
        "win_rate, avg_r, total_r, max_drawdown_r, run_at_ms) VALUES "
        "(?, 'BTCUSDT', '1h', ?, 0, 1, 30, 0.02, 2.0, 0.0, 'off', 0, ?, ?, 0, 0, "
        "0.0, 0.0, 0.0, 0.0, ?)",
        [run_id, strategy, closed, closed, at],
    )


def _trade(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    strategy: str,
    entry_time: int,
    pnl_r: float,
    signal_time: int | None = None,
) -> None:
    sig = entry_time - 1 if signal_time is None else signal_time
    conn.execute(
        "INSERT INTO backtest_trades (trade_id, run_id, symbol, timeframe, strategy, "
        "direction, signal_time, entry_time, entry_price, sl_price, tp_price, "
        "outcome, pnl_r) VALUES (?, ?, 'BTCUSDT', '1h', ?, 'long', ?, ?, 100.0, "
        "99.0, 102.0, 'win', ?)",
        [f"{run_id}:{sig}", run_id, strategy, sig, entry_time, pnl_r],
    )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


class TestPooledSelection:
    def test_floored_detector_reads_only_post_floor_runs(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _run(conn, "pre", strategy="bos", at=FLOOR_MS - 1, closed=1)
        _trade(conn, "pre", strategy="bos", entry_time=100, pnl_r=1.0)
        _run(conn, "post", strategy="bos", at=FLOOR_MS, closed=1)
        _trade(conn, "post", strategy="bos", entry_time=200, pnl_r=-1.0)
        got = load_backtest_trades(conn, columns=COLS, floors=FLOORS)
        assert got["run_id"].tolist() == ["post"]

    def test_post_floor_run_carrying_stale_rows_is_refused(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The #949 shape: a run re-saved after the fix kept its pre-fix rows."""
        _run(conn, "dirty", strategy="bos", at=FLOOR_MS + 5, closed=1)
        _trade(conn, "dirty", strategy="bos", entry_time=200, pnl_r=-1.0)
        _trade(conn, "dirty", strategy="bos", entry_time=100, pnl_r=1.0)  # stale
        _run(conn, "clean", strategy="bos", at=FLOOR_MS + 5, closed=1)
        _trade(conn, "clean", strategy="bos", entry_time=300, pnl_r=0.5)
        got = load_backtest_trades(conn, columns=COLS, floors=FLOORS)
        assert got["run_id"].tolist() == ["clean"]

    def test_unfloored_detector_reads_every_run(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """No floor, no clean-run check: an unfloored run's extra rows still count."""
        _run(conn, "old", strategy="fvg", at=1, closed=1)
        _trade(conn, "old", strategy="fvg", entry_time=100, pnl_r=1.0)
        _trade(conn, "old", strategy="fvg", entry_time=150, pnl_r=1.0)
        got = load_backtest_trades(conn, columns=COLS, floors=FLOORS)
        assert sorted(got["entry_time"].tolist()) == [100, 150]

    def test_filters_bind_beside_the_floor(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """Filter and floor parameters share one positional list; keep them aligned."""
        _run(conn, "b", strategy="bos", at=FLOOR_MS, closed=1)
        _trade(conn, "b", strategy="bos", entry_time=100, pnl_r=1.0)
        _run(conn, "f", strategy="fvg", at=1, closed=1)
        _trade(conn, "f", strategy="fvg", entry_time=100, pnl_r=1.0)
        got = load_backtest_trades(
            conn,
            columns=COLS,
            floors=FLOORS,
            strategies=["bos"],
            symbols=["BTCUSDT"],
            timeframes=["1h"],
        )
        assert got["run_id"].tolist() == ["b"]

    def test_default_floors_come_from_the_registry(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        _run(conn, "pre", strategy="liquidity_sweep", at=1, closed=1)
        _trade(conn, "pre", strategy="liquidity_sweep", entry_time=100, pnl_r=1.0)
        assert load_backtest_trades(conn, columns=COLS).empty


class TestDedup:
    def test_newest_run_wins_not_the_largest_run_id(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """The old rule kept the lexicographically last run_id, a hash."""
        _run(conn, "zzz", strategy="fvg", at=1, closed=1)
        _trade(conn, "zzz", strategy="fvg", entry_time=100, pnl_r=1.0)
        _run(conn, "aaa", strategy="fvg", at=2, closed=1)
        _trade(conn, "aaa", strategy="fvg", entry_time=100, pnl_r=-1.0)
        got = load_backtest_trades(conn, columns=COLS, floors={})
        assert got["run_id"].tolist() == ["aaa"]

    def test_signal_key_dedups_on_the_signal_bar(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        for run_id, at, entry in (("a", 1, 100), ("b", 2, 101)):
            _run(conn, run_id, strategy="fvg", at=at, closed=1)
            _trade(
                conn,
                run_id,
                strategy="fvg",
                entry_time=entry,
                pnl_r=0.0,
                signal_time=50,
            )
        assert len(load_backtest_trades(conn, columns=COLS, floors={})) == 2
        got = load_backtest_trades(conn, columns=COLS, floors={}, dedup_on=SIGNAL_KEY)
        assert got["run_id"].tolist() == ["b"]


class TestChosenRuns:
    def test_run_ids_are_read_unfloored_and_whole(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        """A rated reader's selection is select_rated_run_ids', not the floor's."""
        _run(conn, "pre", strategy="bos", at=1, closed=2)
        _trade(conn, "pre", strategy="bos", entry_time=100, pnl_r=1.0)
        _trade(conn, "pre", strategy="bos", entry_time=200, pnl_r=1.0)
        got = load_backtest_trades(conn, columns=COLS, run_ids=["pre"], dedup_on=None)
        assert len(got) == 2

    def test_empty_run_ids_read_nothing(self, conn: duckdb.DuckDBPyConnection) -> None:
        _run(conn, "r", strategy="fvg", at=1, closed=1)
        _trade(conn, "r", strategy="fvg", entry_time=100, pnl_r=1.0)
        assert load_backtest_trades(conn, columns=COLS, run_ids=[]).empty

    def test_run_columns_join(self, conn: duckdb.DuckDBPyConnection) -> None:
        _run(conn, "r", strategy="fvg", at=7, closed=1)
        _trade(conn, "r", strategy="fvg", entry_time=100, pnl_r=1.0)
        got = load_backtest_trades(
            conn, columns=("pnl_r",), run_ids=["r"], run_columns=("run_at_ms",)
        )
        assert list(got.columns) == ["pnl_r", "run_at_ms"]
        assert got["run_at_ms"].tolist() == [7]

    def test_run_ids_and_floors_together_is_an_error(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(ValueError, match="not both"):
            load_backtest_trades(conn, columns=COLS, run_ids=["r"], floors={})

    def test_unknown_column_is_refused(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Column names are interpolated into SQL, so only known names pass."""
        with pytest.raises(ValueError, match="unknown"):
            load_backtest_trades(conn, columns=("pnl_r, 1 AS x",), floors={})


class TestRegistry:
    def test_committed_floors(self) -> None:
        floors = detector_floors()
        assert set(floors) == {"bos", "liquidity_sweep"}
        # 2026-08-18T13:21Z, when the #652 fix entered the working tree.
        assert {f.since_ms for f in floors.values()} == {1_787_059_260_000}

    def test_missing_table_raises(self, tmp_path: Path) -> None:
        text = DEFAULT_ERAS_PATH.read_text(encoding="utf-8")
        head, _, rest = text.partition("[[detector_floor]]")
        path = tmp_path / "eras.toml"
        path.write_text(head + rest[rest.index("[[boundary]]") :], encoding="utf-8")
        with pytest.raises(ValueError, match="no \\[\\[detector_floor\\]\\]"):
            detector_floors(path)

    def test_offsetless_since_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "eras.toml"
        path.write_text(
            '[[detector_floor]]\nid = "x"\nstrategies = ["bos"]\n'
            'since = "2026-08-18T13:21:00"\nwhy = "x"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="UTC offset"):
            detector_floors(path)


#: Files allowed to name ``backtest_trades`` in a FROM/JOIN, with how many times.
#: Each writes, migrates or deletes; none reads trades for analysis. A count, not a
#: file list, so a new read added to one of these files still fails.
_NON_READERS: dict[str, int] = {
    "analytics/store/backtest_trades.py": 2,  # the loader itself
    "analytics/store/backtest_runs.py": 1,  # the writer's delete-then-insert
    "analytics/store/schema.py": 1,  # split-column backfill on init
    "migrations/001_day_filter_text.py": 1,  # row count before a run_id cascade
    "scripts/db_prune_backtests.py": 3,  # docstring, count, cascade delete
    "tools/st104_sweep.py": 3,  # merge: delete, copy in, verify the copy
}
_READ = re.compile(r"\b(?:from|join)\s+(?:\w+\.)?backtest_trades\b", re.IGNORECASE)


def test_no_read_path_bypasses_the_loader() -> None:
    """Every ``backtest_trades`` reader outside tests goes through the loader."""
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    found: dict[str, int] = {}
    for rel in tracked:
        if rel.startswith("tests/"):
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        n = len(_READ.findall(text))
        if n:
            found[rel] = n
    assert found == _NON_READERS
