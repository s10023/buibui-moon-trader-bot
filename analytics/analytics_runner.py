"""Analytics runner — thin wrapper that creates dependencies and delegates to data_sync."""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.data_sync import (
    backfill,
    backfill_funding_rates,
    refresh_symbol_lifecycle,
    sync,
    sync_funding_rates,
    sync_open_interest,
)
from analytics.db_retry import connect_with_retry
from utils.binance_client import create_client, load_coins_config


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
) -> Generator[tuple[Any, duckdb.DuckDBPyConnection]]:
    try:
        client: Any = create_client()
    except Exception as e:
        logging.error("Failed to create Binance client: %s", e)
        sys.exit(1)
    # Waits out a concurrent writer rather than dying on it. These runners are
    # unattended systemd timers that catch up simultaneously after a resume from
    # suspend, which is how the 2026-08-11 xsmom sync lost a day to a
    # `Conflicting lock` nine seconds after signal-watch's own catch-up fired.
    conn: duckdb.DuckDBPyConnection = connect_with_retry(db_path)
    try:
        init_schema(conn)
        yield client, conn
    finally:
        conn.close()


def _sync_ancillary(
    conn: duckdb.DuckDBPyConnection,
    client: Any,
    symbol: str,
    funding_since_ms: int | None = None,
) -> None:
    if funding_since_ms is not None:
        logging.info("Backfilling funding rates for %s ...", symbol)
        total_fr = backfill_funding_rates(conn, client, symbol, funding_since_ms)
    else:
        logging.info("Syncing funding rates for %s ...", symbol)
        total_fr = sync_funding_rates(conn, client, symbol)
    logging.info("Funding rates complete: %s — %d rows", symbol, total_fr)
    logging.info("Syncing open interest for %s ...", symbol)
    total_oi = sync_open_interest(conn, client, symbol)
    logging.info("Open interest complete: %s — %d rows", symbol, total_oi)


def _refresh_lifecycle_safe(
    conn: duckdb.DuckDBPyConnection, client: Any, symbols: list[str]
) -> None:
    """Refresh symbol_lifecycle; non-fatal on error (must never block ingest)."""
    try:
        refresh_symbol_lifecycle(conn, client, symbols)
    except Exception as e:
        logging.warning("symbol_lifecycle refresh failed (continuing): %s", e)


def run_backfill(
    symbols: list[str] | None,
    timeframes: list[str],
    since_ms: int,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    resolved = _resolve_symbols(symbols)
    failures: list[str] = []
    with _open_session(db_path) as (client, conn):
        _refresh_lifecycle_safe(conn, client, resolved)
        for symbol in resolved:
            try:
                for timeframe in timeframes:
                    logging.info("Backfilling %s %s ...", symbol, timeframe)
                    # client is always create_client() (real Binance), regardless
                    # of DATA_SOURCE -- say so explicitly rather than letting
                    # backfill() fall back to resolve_venue(), which would mistag
                    # these rows if a stray DATA_SOURCE=okx leaked into this shell.
                    total = backfill(
                        conn, client, symbol, timeframe, since_ms, venue="binance"
                    )
                    logging.info(
                        "Backfill complete: %s %s — %d rows", symbol, timeframe, total
                    )
                _sync_ancillary(conn, client, symbol, funding_since_ms=since_ms)
            except Exception:
                logging.exception("backfill failed for %s — continuing", symbol)
                failures.append(symbol)
    if failures:
        logging.error("backfill finished with failures: %s", ", ".join(failures))
        sys.exit(1)


def run_sync(
    symbols: list[str] | None,
    timeframes: list[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    resolved = _resolve_symbols(symbols)
    failures: list[str] = []
    with _open_session(db_path) as (client, conn):
        _refresh_lifecycle_safe(conn, client, resolved)
        for symbol in resolved:
            try:
                for timeframe in timeframes:
                    logging.info("Syncing %s %s ...", symbol, timeframe)
                    try:
                        # Same reasoning as the backfill() call above: this
                        # runner's client is unconditionally Binance.
                        total = sync(conn, client, symbol, timeframe, venue="binance")
                        logging.info(
                            "Sync complete: %s %s — %d new rows",
                            symbol,
                            timeframe,
                            total,
                        )
                    except ValueError as e:
                        logging.warning("%s — skipping (run backfill first)", e)
                _sync_ancillary(conn, client, symbol)
            except Exception:
                logging.exception("sync failed for %s — continuing", symbol)
                failures.append(symbol)
    if failures:
        logging.error("sync finished with failures: %s", ", ".join(failures))
        sys.exit(1)
