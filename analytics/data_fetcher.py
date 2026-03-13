"""Pure data-fetching logic for analytics — Binance Futures API to DataFrames.

All functions accept a Binance client as a parameter.
No module-level side effects.
"""

from typing import Literal

import pandas as pd
from binance.client import Client

OIPeriod = Literal["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]

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
]
FUNDING_COLUMNS: list[str] = ["symbol", "funding_time", "funding_rate"]
OI_COLUMNS: list[str] = ["symbol", "timestamp", "oi_usd"]


def fetch_klines(
    client: Client,
    symbol: str,
    interval: str,
    start_time: int,
    limit: int = KLINES_MAX_LIMIT,
) -> pd.DataFrame:
    """Fetch up to `limit` klines starting from start_time (Unix ms).

    Returns a DataFrame with columns matching OHLCV_COLUMNS.
    Returns an empty DataFrame (with correct columns) if the API returns no data.
    Raises on API errors — callers decide whether to retry or skip.
    """
    raw = client.futures_klines(
        symbol=symbol, interval=interval, startTime=start_time, limit=limit
    )
    if not raw:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    rows = [
        {
            "symbol": symbol,
            "timeframe": interval,
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]
    return pd.DataFrame(rows, columns=OHLCV_COLUMNS)


def fetch_funding_rates(
    client: Client,
    symbol: str,
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch the most recent `limit` funding rate records for symbol.

    Returns a DataFrame with columns matching FUNDING_COLUMNS.
    Returns an empty DataFrame (with correct columns) if no data.
    """
    raw = client.futures_funding_rate(symbol=symbol, limit=limit)
    if not raw:
        return pd.DataFrame(columns=FUNDING_COLUMNS)
    rows = [
        {
            "symbol": r["symbol"],
            "funding_time": int(r["fundingTime"]),
            "funding_rate": float(r["fundingRate"]),
        }
        for r in raw
    ]
    return pd.DataFrame(rows, columns=FUNDING_COLUMNS)


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
    if not raw:
        return pd.DataFrame(columns=OI_COLUMNS)
    rows = [
        {
            "symbol": r["symbol"],
            "timestamp": int(r["timestamp"]),
            "oi_usd": float(r["sumOpenInterestValue"]),
        }
        for r in raw
    ]
    return pd.DataFrame(rows, columns=OI_COLUMNS)
