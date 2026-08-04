"""Free, keyless daily-close fetchers for the H14 premium state tag.

``get`` is injected so the suite is network-free. Both venues are public REST;
no API key is used or required.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pandas as pd

DAY_MS = 86_400_000
_COINBASE_MAX = 300
_BINANCE_LIMIT = 1000
Getter = Callable[[str], Any]


def http_get_json(url: str) -> Any:
    """Default real-network getter. Never used in tests."""
    req = urllib.request.Request(url, headers={"User-Agent": "buibui-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.load(resp)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _frame(rows: list[tuple[int, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["open_time", "close"])
    if df.empty:
        return df.astype({"open_time": "int64", "close": "float64"})
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    return df.astype({"open_time": "int64", "close": "float64"})


def fetch_coinbase_daily(
    product: str, start_ms: int, end_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily closes for a Coinbase Exchange product, paged under the 300-bar cap."""
    rows: list[tuple[int, float]] = []
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(cursor + _COINBASE_MAX * DAY_MS, end_ms)
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity=86400&start={_iso(cursor)}&end={_iso(window_end)}"
        )
        for row in get(url) or []:
            rows.append((int(row[0]) * 1000, float(row[4])))
        cursor = window_end
    return _frame(rows)


def fetch_binance_spot_daily(
    symbol: str, start_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily closes for a Binance SPOT symbol from ``start_ms`` to now."""
    rows: list[tuple[int, float]] = []
    cursor = start_ms
    while True:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1d&startTime={cursor}&limit={_BINANCE_LIMIT}"
        )
        page = get(url) or []
        for row in page:
            rows.append((int(row[0]), float(row[4])))
        if len(page) < _BINANCE_LIMIT:
            break
        next_cursor = int(page[-1][0]) + DAY_MS
        if next_cursor <= cursor:
            # A full page whose last open_time didn't advance past the cursor
            # we requested (stale/misbehaving response) — stop instead of
            # re-requesting the same window forever.
            break
        cursor = next_cursor
    return _frame(rows)
