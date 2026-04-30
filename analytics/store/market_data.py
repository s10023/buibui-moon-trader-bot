"""OHLCV / funding rates / open interest table accessors."""

import duckdb
import pandas as pd

from analytics.store._common import _upsert


def upsert_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace OHLCV rows.

    df must have columns: symbol, timeframe, open_time, open, high, low, close, volume,
    taker_buy_volume.
    Conflicts on (symbol, timeframe, open_time) are replaced.
    """
    _upsert(
        conn,
        df,
        "ohlcv",
        "symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume",
    )


def upsert_funding_rates(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace funding rate rows.

    df must have columns: symbol, funding_time, funding_rate.
    Conflicts on (symbol, funding_time) are replaced.
    """
    _upsert(conn, df, "funding_rates", "symbol, funding_time, funding_rate")


def upsert_open_interest(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace open interest rows.

    df must have columns: symbol, timestamp, oi_usd.
    Conflicts on (symbol, timestamp) are replaced.
    """
    _upsert(conn, df, "open_interest", "symbol, timestamp, oi_usd")


def get_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return OHLCV rows for (symbol, timeframe) between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, timeframe, open_time, open, high, low, close, volume, taker_buy_volume "
        "FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time",
        [symbol, timeframe, start, end],
    ).df()


def get_funding_rates(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return funding rate rows for symbol between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, funding_time, funding_rate "
        "FROM funding_rates "
        "WHERE symbol = ? AND funding_time >= ? AND funding_time <= ? "
        "ORDER BY funding_time",
        [symbol, start, end],
    ).df()


def get_open_interest(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start: int,
    end: int,
) -> pd.DataFrame:
    """Return open interest rows for symbol between start and end (Unix ms, inclusive)."""
    return conn.execute(
        "SELECT symbol, timestamp, oi_usd "
        "FROM open_interest "
        "WHERE symbol = ? AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp",
        [symbol, start, end],
    ).df()


def get_latest_open_time(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int | None:
    """Return the maximum open_time stored for (symbol, timeframe), or None if no rows."""
    # ORDER BY ... LIMIT 1 instead of MAX() to avoid a DuckDB statistics
    # optimizer bug (InternalException on aggregate after multiple inserts).
    result = conn.execute(
        "SELECT open_time FROM ohlcv WHERE symbol = ? AND timeframe = ?"
        " ORDER BY open_time DESC LIMIT 1",
        [symbol, timeframe],
    ).fetchone()
    if result is None:
        return None
    return int(result[0])
