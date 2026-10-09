"""Runner-level wiring tests for analytics/recalibrate_runner.py.

The unit tests in ``test_recalibrate_lib.py`` call each pruner directly, so
none of them proves the runner passes the right dicts, in the right scope, in
the right order. That wiring is where ST58's fix is easiest to get wrong: the
delete must run once per config over the union of all three directions, never
inside the per-direction upsert, and only after the pass has written its rows.

These drive the real ``run()`` against a temp DB and a temp config TOML, so
they exercise compute -> write -> prune as one chain.
"""

import argparse
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from analytics import recalibrate_runner
from analytics.eras import EraBoundary
from analytics.store import init_schema


@pytest.fixture(autouse=True)
def _no_detector_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests seed ``bos`` at toy ``run_at_ms`` values, below its real floor.

    They pin the runner, not the floor, so the floor is switched off here.
    ``TestDetectorFloorSelection`` in ``test_recalibrate_lib.py`` covers it (#993).
    """
    monkeypatch.setattr("analytics.recalibrate_lib.detector_floors", dict)


def _insert_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    strategy: str,
    tf: str,
    avg_r: float,
    closed_trades: int,
    run_at_ms: int = 1000,
) -> None:
    """One backtest_runs row carrying only the fields recalibrate reads.

    ``day_filter`` must match the config's or ``_build_run_filter`` excludes the
    row; ``adr_suppress_threshold`` is left NULL, which is the scope a config
    with no ADR gate asks for.
    """
    conn.execute(
        "INSERT INTO backtest_runs "
        "(run_id, symbol, timeframe, strategy, data_start_ms, data_end_ms, days, "
        "sl_pct, tp_r, fee_pct, day_filter, smt_trend_filter, total_signals, "
        "closed_trades, win_count, loss_count, win_rate, avg_r, total_r, "
        "max_drawdown_r, run_at_ms) "
        "VALUES (?, 'BTCUSDT', ?, ?, 0, 1, 90, 0.02, 2.0, 0.0005, 'tue_thu', 1, "
        "?, ?, ?, ?, 0.5, ?, ?, 1.0, ?)",
        [
            run_id,
            tf,
            strategy,
            closed_trades,
            closed_trades,
            closed_trades // 2,
            closed_trades - closed_trades // 2,
            avg_r,
            avg_r * closed_trades,
            run_at_ms,
        ],
    )


def _config(tmp_path: Path, strategies: list[str]) -> Path:
    path = tmp_path / "signal_watch.toml"
    path.write_text(
        "day_filter = 'tue_thu'\n"
        f"strategies = {strategies!r}\n"
        "timeframes = ['1h', '4h']\n",
        encoding="utf-8",
    )
    return path


def _cells(db: Path) -> set[tuple[str, str, str]]:
    conn = duckdb.connect(str(db), read_only=True)
    try:
        rows = conn.execute(
            "SELECT strategy, tf, direction FROM confidence_ratings "
            "WHERE config_name = 'signal_watch'"
        ).fetchall()
    finally:
        conn.close()
    return {(str(r[0]), str(r[1]), str(r[2])) for r in rows}


def _no_boundaries() -> list[EraBoundary]:
    return []


def _run(
    db: Path,
    cfg: Path,
    loader: Callable[[], list[EraBoundary]] = _no_boundaries,
) -> None:
    recalibrate_runner.run(
        argparse.Namespace(apply=True, min_trades=10, config=str(cfg)),
        db_path=db,
        boundary_loader=loader,
    )


def _boundary(ts_ms: int) -> EraBoundary:
    return EraBoundary(
        ts_ms=ts_ms,
        label="feat: a rule change",
        scope="backtest",
        source="git",
        ref="abc1234",
    )


def test_runner_prunes_a_cell_that_fell_below_the_trade_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ST58 shape end to end: a still-DECLARED cell that stops being rated
    must lose its row, while the cell still rated keeps its own.

    Mutation that must fail this: delete the ``prune_unrated_ratings`` call from
    the runner. ``bos/4h`` then survives carrying its stale stars — which is the
    production bug, measured at 24 of 288 rows on 2026-08-20.
    """
    db = tmp_path / "analytics.db"
    cfg = _config(tmp_path, ["fvg", "bos"])

    conn = duckdb.connect(str(db))
    init_schema(conn)
    _insert_run(conn, "r1", "fvg", "1h", 0.4, 40)
    _insert_run(conn, "r2", "bos", "4h", 0.3, 40)
    conn.close()

    _run(db, cfg)
    assert _cells(db) == {("fvg", "1h", "combined"), ("bos", "4h", "combined")}

    # bos/4h drops below min_trades — the omission-without-delete ST58 is about.
    conn = duckdb.connect(str(db))
    conn.execute("DELETE FROM backtest_runs WHERE run_id = 'r2'")
    _insert_run(conn, "r3", "bos", "4h", 0.3, 4)
    conn.close()
    capsys.readouterr()

    _run(db, cfg)
    assert _cells(db) == {("fvg", "1h", "combined")}
    assert "no longer rated" in capsys.readouterr().out


def test_runner_refuses_the_prune_without_deleting_when_the_pool_collapses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A collapsed pool must not wipe the config.

    ``run()`` returns early when ``new_ratings`` is wholly empty, so the case
    that actually reaches the pruner is a pool that still rates *something*
    while losing most of it. Here 29 of 30 declared cells fall below the floor;
    the share ceiling must refuse, print, and delete nothing.
    """
    ghosts = [f"ghost_{i}" for i in range(30)]
    db = tmp_path / "analytics.db"
    cfg = _config(tmp_path, ghosts)

    conn = duckdb.connect(str(db))
    init_schema(conn)
    for i, name in enumerate(ghosts):
        _insert_run(conn, f"r{i}", name, "1h", 0.4, 40)
    conn.close()

    _run(db, cfg)
    assert len(_cells(db)) == 30

    conn = duckdb.connect(str(db))
    conn.execute("DELETE FROM backtest_runs WHERE run_id <> 'r0'")
    conn.close()
    capsys.readouterr()

    _run(db, cfg)
    assert len(_cells(db)) == 30
    assert "SKIPPED unrated-rating prune" in capsys.readouterr().out


def test_runner_prints_the_era_check_for_the_rated_pool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two rated runs saved either side of a boundary straddle it, and the report
    says so beside the stars rather than leaving the reader to assume one era.

    Mutation that must fail this: drop the era-check print from ``run()``.
    """
    db = tmp_path / "analytics.db"
    cfg = _config(tmp_path, ["fvg", "bos"])
    conn = duckdb.connect(str(db))
    init_schema(conn)
    _insert_run(conn, "r1", "fvg", "1h", 0.4, 40, run_at_ms=1_000)
    _insert_run(conn, "r2", "bos", "4h", 0.3, 40, run_at_ms=3_000)
    conn.close()

    _run(db, cfg, loader=lambda: [_boundary(2_000)])
    out = capsys.readouterr().out
    assert "Era check" in out
    assert "STRADDLES 1 boundaries" in out


def test_runner_era_check_reads_the_rated_pool_not_every_saved_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A superseded run is not in the pool the stars came from, so a boundary
    that only it sits behind must not be reported as straddled.

    Mutation that must fail this: key the check on every ``backtest_runs`` row
    instead of :func:`sample_run_times`.
    """
    db = tmp_path / "analytics.db"
    cfg = _config(tmp_path, ["fvg"])
    conn = duckdb.connect(str(db))
    init_schema(conn)
    _insert_run(conn, "old", "fvg", "1h", 0.4, 40, run_at_ms=500)
    _insert_run(conn, "new", "fvg", "1h", 0.4, 40, run_at_ms=1_000)
    conn.close()

    _run(db, cfg, loader=lambda: [_boundary(700)])
    out = capsys.readouterr().out
    assert "era check: CLEAN" in out
    assert "STRADDLES" not in out


def test_runner_era_check_failure_prints_not_run_and_still_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A git failure must neither read as CLEAN nor block the ratings write.

    Mutations that must fail this: let the loader's exception propagate (the
    apply never happens), or return ``[]`` on failure (prints CLEAN).
    """

    def broken() -> list[EraBoundary]:
        raise RuntimeError("git exited 128: not a git repository")

    db = tmp_path / "analytics.db"
    cfg = _config(tmp_path, ["fvg"])
    conn = duckdb.connect(str(db))
    init_schema(conn)
    _insert_run(conn, "r1", "fvg", "1h", 0.4, 40)
    conn.close()

    _run(db, cfg, loader=broken)
    out = capsys.readouterr().out
    assert "era check: NOT RUN" in out
    assert "CLEAN" not in out
    assert _cells(db) == {("fvg", "1h", "combined")}
