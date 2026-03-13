"""Analytics runner — thin wrapper that creates dependencies and delegates to data_sync."""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from analytics.data_store import init_schema
from analytics.data_sync import backfill, sync
from utils.binance_client import create_client, load_coins_config

_DEFAULT_DB_PATH: Path = Path("analytics.db")


def _resolve_symbols(symbols: list[str] | None) -> list[str]:
    if symbols:
        return symbols
    try:
        return list(load_coins_config().keys())
    except Exception as e:
        logging.error("Failed to load coins config: %s", e)
        sys.exit(1)


@contextmanager
def _open_session(
    db_path: Path,
) -> Generator[tuple[Any, duckdb.DuckDBPyConnection], None, None]:
    try:
        client: Any = create_client()
    except Exception as e:
        logging.error("Failed to create Binance client: %s", e)
        sys.exit(1)
    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path))
    try:
        init_schema(conn)
        yield client, conn
    finally:
        conn.close()


def run_backfill(
    symbols: list[str] | None,
    timeframes: list[str],
    since_ms: int,
    db_path: Path = _DEFAULT_DB_PATH,
) -> None:
    """Create client, open DB, run backfill for all symbol/timeframe pairs."""
    resolved = _resolve_symbols(symbols)
    with _open_session(db_path) as (client, conn):
        for symbol in resolved:
            for timeframe in timeframes:
                logging.info("Backfilling %s %s ...", symbol, timeframe)
                total = backfill(conn, client, symbol, timeframe, since_ms)
                logging.info(
                    "Backfill complete: %s %s — %d rows", symbol, timeframe, total
                )


def run_sync(
    symbols: list[str] | None,
    timeframes: list[str],
    db_path: Path = _DEFAULT_DB_PATH,
) -> None:
    """Create client, open DB, run incremental sync for all symbol/timeframe pairs."""
    resolved = _resolve_symbols(symbols)
    with _open_session(db_path) as (client, conn):
        for symbol in resolved:
            for timeframe in timeframes:
                logging.info("Syncing %s %s ...", symbol, timeframe)
                try:
                    total = sync(conn, client, symbol, timeframe)
                    logging.info(
                        "Sync complete: %s %s — %d new rows", symbol, timeframe, total
                    )
                except ValueError as e:
                    logging.warning("%s — skipping (run backfill first)", e)
