"""Export a slim live-signal DuckDB for the GitHub Actions signal-watch job.

Copies ONLY the tables the live daemon reads (ohlcv + calibration) from the local
Binance analytics.db into a fresh live_signal.duckdb, excluding the bulky
backtest_trades / backtest_runs / backtest_cache. The source is opened READ-ONLY
and never mutated. Run after `make db-update` / recalibrate, then commit the output
via Git LFS.

Usage: PYTHONPATH=. poetry run python tools/export_live_db.py [SRC] [OUT]
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

LIVE_TABLES: list[str] = [
    "ohlcv",
    "confidence_ratings",
    "backtest_combos",
    "backtest_cross_tf_combos",
]

DEFAULT_SRC = Path("analytics.db")
DEFAULT_OUT = Path("live_signal.duckdb")


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchone()[0]
    )


def export_live_db(src: Path = DEFAULT_SRC, out: Path = DEFAULT_OUT) -> None:
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    out.unlink(missing_ok=True)  # fresh file; never appends to a stale one

    # Source opened READ-ONLY — guarantees we never overwrite local Binance data.
    src_con = duckdb.connect(str(src), read_only=True)
    out_con = duckdb.connect(str(out))
    try:
        for table in LIVE_TABLES:
            if not _table_exists(src_con, table):
                continue
            df = src_con.execute(f'SELECT * FROM "{table}"').fetchdf()  # noqa: F841
            out_con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM df')
        rows = {
            t: out_con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for t in LIVE_TABLES
            if _table_exists(out_con, t)
        }
        size_mb = out.stat().st_size / 1e6
        print(f"Exported {out} ({size_mb:.1f} MB): {rows}")
    finally:
        src_con.close()
        out_con.close()


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    export_live_db(src, out)
