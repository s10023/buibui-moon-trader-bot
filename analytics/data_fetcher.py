"""Pure data-fetching logic for analytics — Binance Futures API to DataFrames.

All functions accept a Binance client as a parameter.
No module-level side effects.
"""

from collections.abc import Callable
from typing import Any, Literal

import pandas as pd
from binance.client import Client

from utils.okx_client import OKXClient

OIPeriod = Literal["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]

# Market-data clients fetch_klines can drive. Binance and OKX have incompatible
# futures_klines keyword names (startTime vs start_time), so a structural Protocol
# can't match both — a union narrowed by isinstance is the honest type.
type KlineClient = Client | OKXClient

KLINES_MAX_LIMIT: int = 1000

OHLCV_COLUMNS: list[str] = [
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "taker_buy_volume",
]
FUNDING_COLUMNS: list[str] = ["symbol", "funding_time", "funding_rate"]
OI_COLUMNS: list[str] = ["symbol", "timestamp", "oi_usd"]


def _fetch_to_df(
    raw: list[Any],
    mapper: Callable[[Any], dict[str, Any]],
    columns: list[str],
) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([mapper(r) for r in raw], columns=columns)


def fetch_klines(
    client: KlineClient,
    symbol: str,
    interval: str,
    start_time: int,
    limit: int = KLINES_MAX_LIMIT,
) -> pd.DataFrame:
    """Fetch up to `limit` klines starting from start_time (Unix ms).

    Returns a DataFrame with columns matching OHLCV_COLUMNS.
    Returns an empty DataFrame (with correct columns) if the API returns no data.
    Raises on API errors — callers decide whether to retry or skip.

    When `client` is an OKXClient, its futures_klines already returns a Binance-shaped
    OHLCV DataFrame, so pass it through directly; otherwise map the Binance raw rows.
    """
    if isinstance(client, OKXClient):
        return client.futures_klines(symbol, interval, start_time, limit)
    raw = client.futures_klines(
        symbol=symbol, interval=interval, startTime=start_time, limit=limit
    )
    return _fetch_to_df(
        raw,
        lambda k: {
            "symbol": symbol,
            "timeframe": interval,
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "taker_buy_volume": float(k[9]),
        },
        OHLCV_COLUMNS,
    )


def fetch_funding_rates(
    client: Client,
    symbol: str,
    limit: int = 100,
    start_time: int | None = None,
    end_time: int | None = None,
) -> pd.DataFrame:
    """Fetch funding rate records for symbol.

    Without `start_time`, returns the most recent `limit` records (back-compat).
    With `start_time` (Unix ms), Binance returns records from that time forward in
    ascending fundingTime order — used by the paginated history backfill. `end_time`
    optionally bounds the upper edge. Binance caps `limit` at 1000.

    Returns a DataFrame with columns matching FUNDING_COLUMNS.
    Returns an empty DataFrame (with correct columns) if no data.
    """
    kwargs: dict[str, Any] = {"symbol": symbol, "limit": limit}
    if start_time is not None:
        kwargs["startTime"] = start_time
    if end_time is not None:
        kwargs["endTime"] = end_time
    raw = client.futures_funding_rate(**kwargs)
    return _fetch_to_df(
        raw,
        lambda r: {
            "symbol": r["symbol"],
            "funding_time": int(r["fundingTime"]),
            "funding_rate": float(r["fundingRate"]),
        },
        FUNDING_COLUMNS,
    )


def fetch_open_interest(
    client: Client,
    symbol: str,
    period: OIPeriod,
    limit: int = 200,
) -> pd.DataFrame:
    """Fetch the most recent `limit` open interest history records for symbol.

    `period` matches Binance OI history intervals: "5m", "15m", "30m", "1h", "2h",
    "4h", "6h", "12h", "1d".
    Returns a DataFrame with columns matching OI_COLUMNS.
    Returns an empty DataFrame (with correct columns) if no data.
    """
    raw = client.futures_open_interest_hist(symbol=symbol, period=period, limit=limit)
    return _fetch_to_df(
        raw,
        lambda r: {
            "symbol": r["symbol"],
            "timestamp": int(r["timestamp"]),
            "oi_usd": float(r["sumOpenInterestValue"]),
        },
        OI_COLUMNS,
    )
