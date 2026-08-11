"""Binance SPOT daily bars — the second venue for the D1 CVD sleeve.

Deliberately a separate table from ``ohlcv`` (perp) and from H14's
``venue_spot_daily`` (closes only). ``_upsert`` emits ``INSERT OR REPLACE INTO
{table} SELECT {columns}`` with no INSERT column list, so widening an existing
table changes the arity contract of every upsert already writing to it.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store._common import _upsert

_COLUMNS = "symbol, open_time, open, high, low, close, volume, taker_buy_volume"


def upsert_spot_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace daily spot bars.

    df must have columns: symbol, open_time, open, high, low, close, volume,
    taker_buy_volume. Conflicts on (symbol, open_time) are replaced.
    """
    _upsert(conn, df, "spot_ohlcv", _COLUMNS)


def get_spot_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Daily spot bars for `symbol` between start and end (Unix ms, inclusive)."""
    return conn.execute(
        f"SELECT {_COLUMNS} FROM spot_ohlcv "  # noqa: S608
        "WHERE symbol = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time",
        [symbol, start, end],
    ).df()
