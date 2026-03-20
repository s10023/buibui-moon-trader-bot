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
            symbol           TEXT   NOT NULL,
            timeframe        TEXT   NOT NULL,
            open_time        BIGINT NOT NULL,
            open             DOUBLE NOT NULL,
            high             DOUBLE NOT NULL,
            low              DOUBLE NOT NULL,
            close            DOUBLE NOT NULL,
            volume           DOUBLE NOT NULL,
            taker_buy_volume DOUBLE,
            PRIMARY KEY (symbol, timeframe, open_time)
        )
    """)
    # Migration guard: add column to existing DBs that were created before this field.
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'ohlcv'"
        ).fetchall()
    }
    if "taker_buy_volume" not in existing:
        conn.execute("ALTER TABLE ohlcv ADD COLUMN taker_buy_volume DOUBLE")
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            symbol        VARCHAR NOT NULL,
            timeframe     VARCHAR NOT NULL,
            strategy      VARCHAR NOT NULL,
            open_time     BIGINT  NOT NULL,
            direction     VARCHAR NOT NULL,
            entry_price   DOUBLE,
            sl_price      DOUBLE,
            reason        VARCHAR,
            confidence    INTEGER,
            fired_at      BIGINT  NOT NULL,
            PRIMARY KEY (symbol, timeframe, strategy, open_time, direction)
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
    # register/unregister in try/finally: DuckDB increments refcount on register and
    # decrements on unregister, giving safe bulk-scan performance without the stale
    # C-pointer heap corruption that the implicit replacement scan (FROM df) causes.
    conn.register("_upsert_df", df)
    try:
        conn.execute(f"INSERT OR REPLACE INTO {table} SELECT {columns} FROM _upsert_df")
    finally:
        conn.unregister("_upsert_df")


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


def upsert_signals(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Insert or ignore signal rows (conflicts on PK are silently skipped).

    df must have columns: symbol, timeframe, strategy, open_time, direction,
    entry_price, sl_price, reason, confidence, fired_at.
    Conflicts on (symbol, timeframe, strategy, open_time, direction) are ignored
    so that re-runs of the same scan cycle do not overwrite previously persisted
    signals with potentially different metadata.
    """
    if df.empty:
        return
    # Explicit register/unregister in try/finally — see _upsert docstring for why.
    conn.register("_signals_upsert_df", df)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO signals "
            "SELECT symbol, timeframe, strategy, open_time, direction, "
            "entry_price, sl_price, reason, confidence, fired_at "
            "FROM _signals_upsert_df"
        )
    finally:
        conn.unregister("_signals_upsert_df")


def get_signals_history(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> pd.DataFrame:
    """Return persisted signal rows for (symbol, timeframe) in [start_ms, end_ms].

    Results are ordered by open_time descending (most recent first).
    """
    return conn.execute(
        "SELECT symbol, timeframe, strategy, open_time, direction, "
        "entry_price, sl_price, reason, confidence, fired_at "
        "FROM signals "
        "WHERE symbol = ? AND timeframe = ? AND open_time >= ? AND open_time <= ? "
        "ORDER BY open_time DESC",
        [symbol, timeframe, start_ms, end_ms],
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
