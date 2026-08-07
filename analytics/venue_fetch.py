"""Free, keyless daily-close fetchers for the H14 premium state tag.

``get`` is injected so the suite is network-free. Both venues are public REST;
no API key is used or required.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
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
    """Default real-network getter. Never used in tests.

    Both lines below carry `noqa: S310` (audit-url-open-for-permitted-schemes).
    `S` is not in this repo's ruff `select`, so neither suppression fires today
    — but the pair is what makes them *correct* if it is ever enabled. The
    `Request(...)` construction is flagged in its own right, and until
    2026-08-07 only the `urlopen` line was marked: a suppression that read as
    deliberate and considered while leaving the actual diagnostic site bare.
    Verified with `ruff check --select S310`: 1 error before, 0 after.
    """
    req = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": "buibui-research/1.0"}
    )
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


_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"


class YahooFetchError(RuntimeError):
    """Yahoo returned no usable series — rate limit, bad symbol, or empty result.

    Raised rather than returning an empty frame on purpose: an empty frame is
    indistinguishable downstream from "this symbol legitimately has no bars in
    the window", which would silently produce an audit over no data.
    """


def fetch_yahoo_daily(
    symbol: str, start_ms: int, end_ms: int, *, get: Getter = http_get_json
) -> pd.DataFrame:
    """Daily closes for a Yahoo Finance symbol (e.g. ``JPY=X``), keyless.

    ``period1``/``period2`` are SECONDS, and returned bar timestamps are seconds
    — both converted here so callers stay in the repo's millisecond convention.

    Null closes are dropped, never forward-filled: a synthetic close would enter
    the weekly aggregation in Task 3 and silently alter a run count.
    """
    url = (
        f"{_YAHOO_CHART}/{urllib.parse.quote(symbol, safe='')}"
        f"?period1={start_ms // 1000}&period2={end_ms // 1000}&interval=1d"
    )
    try:
        payload = get(url)
    except urllib.error.HTTPError as exc:
        raise YahooFetchError(f"Yahoo HTTP {exc.code} for {symbol}") from exc

    chart = (payload or {}).get("chart") or {}
    err = chart.get("error")
    if err:
        desc = err.get("description") if isinstance(err, dict) else str(err)
        raise YahooFetchError(f"Yahoo error for {symbol}: {desc}")

    results = chart.get("result") or []
    if not results:
        raise YahooFetchError(f"Yahoo returned no result for {symbol}")

    block = results[0]
    stamps = block.get("timestamp") or []
    quotes = (block.get("indicators") or {}).get("quote") or [{}]
    closes = quotes[0].get("close") or []

    rows: list[tuple[int, float]] = [
        (int(t) * 1000, float(c))
        for t, c in zip(stamps, closes, strict=False)
        if c is not None
    ]
    if not rows:
        raise YahooFetchError(f"Yahoo returned no usable closes for {symbol}")
    return _frame(rows)
