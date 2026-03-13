"""Pure data store logic for analytics — DuckDB-backed OHLCV/funding/OI storage.

All functions accept an open DuckDB connection as a parameter.
No module-level side effects.
"""

from pathlib import Path

import duckdb
import pandas as pd

DEFAULT_DB_PATH: Path = Path("analytics.db")


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they do not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol    TEXT   NOT NULL,
            timeframe TEXT   NOT NULL,
            open_time BIGINT NOT NULL,
            open      DOUBLE NOT NULL,
            high      DOUBLE NOT NULL,
            low       DOUBLE NOT NULL,
            close     DOUBLE NOT NULL,
            volume    DOUBLE NOT NULL,
            PRIMARY KEY (symbol, timeframe, open_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS funding_rates (
            symbol       TEXT   NOT NULL,
            funding_time BIGINT NOT NULL,
            funding_rate DOUBLE NOT NULL,
            PRIMARY KEY (symbol, funding_time)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_interest (
            symbol    TEXT   NOT NULL,
            timestamp BIGINT NOT NULL,
            oi_usd    DOUBLE NOT NULL,
            PRIMARY KEY (symbol, timestamp)
        )
    """)


def _upsert(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    table: str,
    columns: str,
) -> None:
    if df.empty:
        return
    col_list = [c.strip() for c in columns.split(",")]
    placeholders = ", ".join(["?" for _ in col_list])
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})",
        df[col_list].values.tolist(),
    )


def upsert_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or replace OHLCV rows.

    df must have columns: symbol, timeframe, open_time, open, high, low, close, volume.
    Conflicts on (symbol, timeframe, open_time) are replaced.
    """
    _upsert(
        conn,
        df,
        "ohlcv",
        "symbol, timeframe, open_time, open, high, low, close, volume",
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
        "SELECT symbol, timeframe, open_time, open, high, low, close, volume "
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


def get_latest_open_time(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
) -> int | None:
    """Return the maximum open_time stored for (symbol, timeframe), or None if no rows."""
    result = conn.execute(
        "SELECT MAX(open_time) FROM ohlcv WHERE symbol = ? AND timeframe = ?",
        [symbol, timeframe],
    ).fetchone()
    if result is None or result[0] is None:
        return None
    return int(result[0])
