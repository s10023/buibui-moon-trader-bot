"""Tests for tools/export_live_db.py — read-only slim live-DB export."""

from pathlib import Path

import duckdb

from tools.export_live_db import LIVE_TABLES, export_live_db


def _make_source(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE ohlcv (symbol TEXT, open_time BIGINT)")
    con.execute("INSERT INTO ohlcv VALUES ('BTCUSDT', 1)")
    con.execute("CREATE TABLE confidence_ratings (strategy TEXT)")
    con.execute("INSERT INTO confidence_ratings VALUES ('bos')")
    con.execute("CREATE TABLE backtest_combos (combo_id TEXT)")
    con.execute("CREATE TABLE backtest_cross_tf_combos (combo_id TEXT)")
    con.execute("CREATE TABLE backtest_trades (id BIGINT)")  # must NOT be copied
    con.execute("INSERT INTO backtest_trades VALUES (1)")
    con.close()


def test_export_copies_only_live_tables(tmp_path: Path) -> None:
    src = tmp_path / "analytics.db"
    out = tmp_path / "live_signal.duckdb"
    _make_source(src)

    export_live_db(src, out)

    con = duckdb.connect(str(out), read_only=True)
    tables = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }
    con.close()
    assert tables == set(LIVE_TABLES)
    assert "backtest_trades" not in tables


def test_export_does_not_mutate_source(tmp_path: Path) -> None:
    src = tmp_path / "analytics.db"
    out = tmp_path / "live_signal.duckdb"
    _make_source(src)
    before = src.stat().st_mtime_ns

    export_live_db(src, out)

    # source untouched (read-only access); mtime unchanged
    assert src.stat().st_mtime_ns == before
    con = duckdb.connect(str(src), read_only=True)
    row = con.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()
    con.close()
    assert row is not None and row[0] == 1
