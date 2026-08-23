"""Export a slim live-signal DuckDB for the GitHub Actions signal-watch job.

Copies ONLY the live-table DATA the daemon reads (ohlcv_all + calibration) from the
local Binance analytics.db into a fresh live_signal.duckdb, leaving the bulky
backtest_trades / backtest_runs / backtest_cache empty. The full schema is created
via init_schema() so every table keeps its PRIMARY KEY — a plain CTAS copy drops
the PK, which breaks the daemon's `INSERT OR REPLACE INTO ohlcv_all` on the first
sync.
The ohlcv_all copy is scoped to coins.json symbols within a 400-day rolling window
so universe/deep-history rows never reach the committed file.
The output's `ohlcv` view is stamped to prefer OKX over Binance
(`read_venue_order`, default "okx,binance") — the slim DB is seeded into an
ephemeral CI database that a DATA_SOURCE=okx run then extends, and this order
reproduces that mixed read exactly.
The source is opened READ-ONLY and never mutated. Run after `make db-update` /
recalibrate, then commit the output (plain git, not LFS — bandwidth is metered).

Usage: PYTHONPATH=. poetry run python tools/export_live_db.py [SRC] [OUT]
       [--read-venue-order ORDER]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

from analytics.store.schema import init_schema
from analytics.store.venue import parse_venue_order, set_read_venue_order
from utils.binance_client import load_coins_config

LIVE_TABLES: list[str] = [
    "ohlcv_all",
    "confidence_ratings",
    "backtest_combos",
    "backtest_cross_tf_combos",
]

DEFAULT_SRC = Path("analytics.db")
DEFAULT_OUT = Path("live_signal.duckdb")

# The CI signal-watch job seeds an ephemeral DB from the committed export, then runs
# DATA_SOURCE=okx syncs against it and reads a mixed series back — Binance history
# underneath, OKX's fresh tail on top. Stamping this order on the output reproduces
# that read exactly.
DEFAULT_READ_VENUE_ORDER = "okx,binance"

# The committed slim DB is checked out hourly by GH Actions — keep it scoped to
# what the daemon actually reads: live (coins.json) symbols, rolling window.
_OHLCV_FLOOR_DAYS: int = 400


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


def export_live_db(
    src: Path = DEFAULT_SRC,
    out: Path = DEFAULT_OUT,
    *,
    ohlcv_symbols: list[str] | None = None,
    ohlcv_floor_days: int = _OHLCV_FLOOR_DAYS,
    now_ms: int | None = None,
    read_venue_order: str = DEFAULT_READ_VENUE_ORDER,
) -> None:
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    out.unlink(missing_ok=True)  # fresh file; never appends to a stale one

    if ohlcv_symbols is None:
        ohlcv_symbols = sorted(load_coins_config().keys())
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    floor_ms = now - ohlcv_floor_days * 86_400_000

    # Source opened READ-ONLY — guarantees we never overwrite local Binance data.
    src_con = duckdb.connect(str(src), read_only=True)
    out_con = duckdb.connect(str(out))
    try:
        # Create the full production schema first so every table carries its
        # PRIMARY KEY. CTAS (CREATE TABLE AS SELECT) would drop the PK, breaking
        # the daemon's INSERT OR REPLACE INTO ohlcv_all (DuckDB needs a UNIQUE/PK
        # for ON CONFLICT). Non-live tables are created empty.
        init_schema(out_con)
        for table in LIVE_TABLES:
            if not _table_exists(src_con, table):
                continue
            if table == "ohlcv_all":
                placeholders = ", ".join("?" for _ in ohlcv_symbols)
                df = src_con.execute(
                    f"SELECT * FROM ohlcv_all WHERE symbol IN ({placeholders}) "
                    "AND open_time >= ?",
                    [*ohlcv_symbols, floor_ms],
                ).fetchdf()  # noqa: F841
            else:
                df = src_con.execute(f'SELECT * FROM "{table}"').fetchdf()  # noqa: F841
            out_con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM df')
        # The slim DB is seeded into an ephemeral CI database that a DATA_SOURCE=okx
        # run then extends. Preferring OKX and falling back to the Binance history
        # underneath reproduces the pre-ST60(b) mixed read exactly -- without letting
        # either venue overwrite the other.
        set_read_venue_order(out_con, parse_venue_order(read_venue_order))
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", nargs="?", type=Path, default=DEFAULT_SRC)
    parser.add_argument("out", nargs="?", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--read-venue-order",
        default=DEFAULT_READ_VENUE_ORDER,
        help=(
            "Comma-separated venue preference order stamped on the output's "
            f"ohlcv view (default: {DEFAULT_READ_VENUE_ORDER!r})"
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    export_live_db(args.src, args.out, read_venue_order=args.read_venue_order)
