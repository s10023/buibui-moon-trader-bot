"""Orchestration logic for backfill and incremental sync.

Accepts all dependencies as parameters — no module-level side effects.
"""

import logging
import time
from collections.abc import Callable

import duckdb
from binance.client import Client

from analytics.data_fetcher import KLINES_MAX_LIMIT, fetch_funding_rates, fetch_klines
from analytics.data_store import (
    get_latest_open_time,
    upsert_funding_rates,
    upsert_ohlcv,
)

# Binance funding rates are emitted every 8 hours.
_FUNDING_RATE_INTERVAL_HOURS: int = 8

_DEFAULT_SLEEP_SECONDS: float = 0.1


def backfill(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbol: str,
    timeframe: str,
    start_ms: int,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    """Fetch full OHLCV history from start_ms to now and store it.

    Paginates in 1000-candle batches. Returns total rows upserted.
    Stops when the API returns fewer rows than the limit (end of history reached).
    """
    _sleep = sleep_fn if sleep_fn is not None else time.sleep
    total = 0
    current_start = start_ms
    while True:
        df = fetch_klines(
            client, symbol, timeframe, current_start, limit=KLINES_MAX_LIMIT
        )
        if df.empty:
            break
        upsert_ohlcv(conn, df)
        total += len(df)
        logging.info(
            "backfill %s %s: stored %d rows (total %d)",
            symbol,
            timeframe,
            len(df),
            total,
        )
        if len(df) < KLINES_MAX_LIMIT:
            break
        current_start = int(df["open_time"].iloc[-1]) + 1
        _sleep(_DEFAULT_SLEEP_SECONDS)
    return total


def sync_funding_rates(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbol: str,
    days: int = 90,
) -> int:
    """Fetch recent funding rates and store them.

    Converts `days` to a record limit (funding rates are emitted every 8 hours).
    Returns the number of rows upserted (0 if the API returned no data).
    """
    limit = days * 24 // _FUNDING_RATE_INTERVAL_HOURS
    df = fetch_funding_rates(client, symbol, limit=limit)
    if df.empty:
        return 0
    upsert_funding_rates(conn, df)
    logging.info("sync_funding_rates %s: stored %d rows", symbol, len(df))
    return len(df)


def sync(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbol: str,
    timeframe: str,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    """Fetch only candles newer than the latest stored open_time.

    Raises ValueError if no data exists for (symbol, timeframe) — run backfill first.
    Returns total rows upserted.
    """
    latest = get_latest_open_time(conn, symbol, timeframe)
    if latest is None:
        raise ValueError(f"No data found for {symbol}/{timeframe}. Run backfill first.")
    return backfill(conn, client, symbol, timeframe, latest + 1, sleep_fn=sleep_fn)
