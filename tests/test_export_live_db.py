"""Tests for tools/export_live_db.py — read-only slim live-DB export."""

from pathlib import Path

import duckdb

from analytics.store.schema import init_schema
from tools.export_live_db import LIVE_TABLES, export_live_db


def _make_source(path: Path) -> None:
    """Build a source DB with the real schema + a few rows in the live tables."""
    con = duckdb.connect(str(path))
    init_schema(con)  # real production schema (PKs + all tables)
    con.execute(
        "INSERT INTO ohlcv VALUES ('BTCUSDT', '1h', 1, 10, 11, 9, 10.5, 100, 50)"
    )
    con.execute(
        "INSERT INTO confidence_ratings "
        "(config_name, strategy, tf, direction, stars, avg_r, win_rate, "
        "updated_at_ms, day_filter) VALUES "
        "('signal_watch', 'bos', '1h', 'long', 3, 0.2, 0.5, 1, 'tue_thu')"
    )
    # backtest_trades carries bulk rows that must NOT be copied into the slim DB.
    con.execute(
        "INSERT INTO backtest_trades "
        "(trade_id, run_id, symbol, timeframe, strategy, direction, signal_time, "
        "entry_time, entry_price, sl_price, tp_price, outcome) VALUES "
        "('t1', 'r1', 'BTCUSDT', '1h', 'bos', 'long', 1, 1, 10, 9, 12, 'win')"
    )
    con.close()


def test_export_copies_live_table_data(tmp_path: Path) -> None:
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
    # Every live table is present with its data copied over.
    assert set(LIVE_TABLES) <= tables
    assert con.execute("SELECT COUNT(*) FROM ohlcv").fetchone() == (1,)
    assert con.execute("SELECT COUNT(*) FROM confidence_ratings").fetchone() == (1,)
    # Bulky backtest_trades schema may exist (from init_schema) but holds no data.
    bt = con.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()
    con.close()
    assert bt == (0,)


def test_exported_ohlcv_supports_insert_or_replace(tmp_path: Path) -> None:
    """Regression: the daemon's incremental sync does INSERT OR REPLACE INTO ohlcv,
    which DuckDB only allows when the table keeps its PRIMARY KEY. A CTAS export
    would drop the PK and raise BinderException on the first live sync."""
    src = tmp_path / "analytics.db"
    out = tmp_path / "live_signal.duckdb"
    _make_source(src)

    export_live_db(src, out)

    con = duckdb.connect(str(out))
    # Same primary key as the seeded row → must REPLACE, not raise.
    con.execute(
        "INSERT OR REPLACE INTO ohlcv VALUES "
        "('BTCUSDT', '1h', 1, 99, 99, 99, 99, 999, 500)"
    )
    row = con.execute(
        "SELECT close FROM ohlcv WHERE symbol='BTCUSDT' AND timeframe='1h' AND open_time=1"
    ).fetchone()
    con.close()
    assert row == (99,)


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
