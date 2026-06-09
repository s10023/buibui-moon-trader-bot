"""Orchestration logic for backfill and incremental sync.

Accepts all dependencies as parameters — no module-level side effects.
"""

import logging
import time
from collections.abc import Callable

import duckdb
from binance.client import Client

from analytics.data_fetcher import (
    KLINES_MAX_LIMIT,
    KlineClient,
    OIPeriod,
    fetch_funding_rates,
    fetch_klines,
    fetch_open_interest,
)
from analytics.data_store import (
    get_latest_open_time,
    upsert_funding_rates,
    upsert_ohlcv,
    upsert_open_interest,
)

# Binance funding rates are emitted every 8 hours.
_FUNDING_RATE_INTERVAL_HOURS: int = 8

# Default OI period to sync — 1h granularity gives ~24 rows/day.
_DEFAULT_OI_PERIOD: OIPeriod = "1h"
# Binance openInterestHist endpoint caps at 500 records per request.
_OI_MAX_LIMIT: int = 500

_DEFAULT_SLEEP_SECONDS: float = 0.1

# Binance funding-rate-history endpoint caps at 1000 records per request.
_FUNDING_BACKFILL_LIMIT: int = 1000


def backfill(
    conn: duckdb.DuckDBPyConnection,
    client: KlineClient,
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


def backfill_funding_rates(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbol: str,
    since_ms: int,
    until_ms: int | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    """Fetch full funding-rate history from since_ms forward and store it.

    Paginates in 1000-record batches (the endpoint cap), advancing past the last
    fundingTime each page. Returns total rows upserted. Stops when a short page
    (fewer rows than the limit) or an empty page is returned.

    Binance-only: the OKX adapter does not serve funding, and this is reached only
    on the Binance backfill path.
    """
    _sleep = sleep_fn if sleep_fn is not None else time.sleep
    total = 0
    current_start = since_ms
    while True:
        df = fetch_funding_rates(
            client,
            symbol,
            limit=_FUNDING_BACKFILL_LIMIT,
            start_time=current_start,
            end_time=until_ms,
        )
        if df.empty:
            break
        upsert_funding_rates(conn, df)
        total += len(df)
        logging.info(
            "backfill_funding_rates %s: stored %d rows (total %d)",
            symbol,
            len(df),
            total,
        )
        if len(df) < _FUNDING_BACKFILL_LIMIT:
            break
        current_start = int(df["funding_time"].iloc[-1]) + 1
        _sleep(_DEFAULT_SLEEP_SECONDS)
    return total


def sync_open_interest(
    conn: duckdb.DuckDBPyConnection,
    client: Client,
    symbol: str,
    days: int = 90,
) -> int:
    """Fetch recent open interest history and store it.

    Uses 1h granularity — each day produces ~24 rows.
    Returns the number of rows upserted (0 if the API returned no data).
    """
    limit = min(days * 24, _OI_MAX_LIMIT)
    df = fetch_open_interest(client, symbol, period=_DEFAULT_OI_PERIOD, limit=limit)
    if df.empty:
        return 0
    upsert_open_interest(conn, df)
    logging.info("sync_open_interest %s: stored %d rows", symbol, len(df))
    return len(df)


def sync(
    conn: duckdb.DuckDBPyConnection,
    client: KlineClient,
    symbol: str,
    timeframe: str,
    sleep_fn: Callable[[float], None] | None = None,
) -> int:
    """Fetch candles from the latest stored open_time onwards (inclusive).

    Starting from `latest` (not `latest + 1`) ensures the last stored candle
    is always re-fetched and overwritten with its final OHLCV values.  Without
    this, a candle stored mid-formation (e.g. a few seconds after it opened)
    keeps its stale close forever — later syncs skip it because they start
    from latest+1.

    Raises ValueError if no data exists for (symbol, timeframe) — run backfill first.
    Returns total rows upserted.
    """
    latest = get_latest_open_time(conn, symbol, timeframe)
    if latest is None:
        raise ValueError(f"No data found for {symbol}/{timeframe}. Run backfill first.")
    return backfill(conn, client, symbol, timeframe, latest, sleep_fn=sleep_fn)
