"""Export a slim live-signal DuckDB for the GitHub Actions signal-watch job.

Copies ONLY the live-table DATA the daemon reads (ohlcv + calibration) from the
local Binance analytics.db into a fresh live_signal.duckdb, leaving the bulky
backtest_trades / backtest_runs / backtest_cache empty. The full schema is created
via init_schema() so every table keeps its PRIMARY KEY — a plain CTAS copy drops
the PK, which breaks the daemon's `INSERT OR REPLACE INTO ohlcv` on the first sync.
The source is opened READ-ONLY and never mutated. Run after `make db-update` /
recalibrate, then commit the output (plain git, not LFS — bandwidth is metered).

Usage: PYTHONPATH=. poetry run python tools/export_live_db.py [SRC] [OUT]
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

from analytics.store.schema import init_schema

LIVE_TABLES: list[str] = [
    "ohlcv",
    "confidence_ratings",
    "backtest_combos",
    "backtest_cross_tf_combos",
]

DEFAULT_SRC = Path("analytics.db")
DEFAULT_OUT = Path("live_signal.duckdb")


def _scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list[object]) -> int:
    """Return the first column of the first row as an int (0 if no row)."""
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        _scalar(
            con,
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name=?",
            [table],
        )
    )


def export_live_db(src: Path = DEFAULT_SRC, out: Path = DEFAULT_OUT) -> None:
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    out.unlink(missing_ok=True)  # fresh file; never appends to a stale one

    # Source opened READ-ONLY — guarantees we never overwrite local Binance data.
    src_con = duckdb.connect(str(src), read_only=True)
    out_con = duckdb.connect(str(out))
    try:
        # Create the full production schema first so every table carries its
        # PRIMARY KEY. CTAS (CREATE TABLE AS SELECT) would drop the PK, breaking
        # the daemon's INSERT OR REPLACE INTO ohlcv (DuckDB needs a UNIQUE/PK for
        # ON CONFLICT). Non-live tables are created empty.
        init_schema(out_con)
        for table in LIVE_TABLES:
            if not _table_exists(src_con, table):
                continue
            df = src_con.execute(f'SELECT * FROM "{table}"').fetchdf()  # noqa: F841
            out_con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM df')
        rows = {
            t: _scalar(out_con, f'SELECT COUNT(*) FROM "{t}"', [])
            for t in LIVE_TABLES
            if _table_exists(out_con, t)
        }
    finally:
        src_con.close()
        out_con.close()
    # Stat after close so DuckDB has flushed the file to its final size.
    size_mb = out.stat().st_size / 1e6
    print(f"Exported {out} ({size_mb:.1f} MB): {rows}")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    export_live_db(src, out)
