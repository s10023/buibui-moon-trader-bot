"""Deterministic OHLCV seeding for the brief test suite (not collected by pytest)."""

from __future__ import annotations

import math

import duckdb
import pandas as pd

from analytics.data_store import init_schema
from analytics.store.market_data import upsert_ohlcv

DAY_MS = 86_400_000
H4_MS = 14_400_000
H1_MS = 3_600_000

# 2024-01-01 00:00:00 UTC — a Monday, exactly on a day boundary.
START_MS = 1_704_067_200_000


def make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def seed_symbol(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int = START_MS,
    n_days: int = 60,
    base: float = 100.0,
) -> None:
    """Insert deterministic 1d + 4h + 1h bars for ``n_days`` from ``start_ms``.

    Prices follow a smooth sine walk so ATR/levels/zones are all non-degenerate
    and reproducible. Intraday bars linearly interpolate the daily open->close.
    """
    rows: list[dict[str, object]] = []
    for i in range(n_days):
        d_open_ms = start_ms + i * DAY_MS
        o = base + 10.0 * math.sin(i / 7.0)
        c = o * (1.0 + 0.01 * math.sin(i / 3.0))
        hi = max(o, c) * 1.01
        lo = min(o, c) * 0.99
        rows.append(_row(symbol, "1d", d_open_ms, o, hi, lo, c))
        for j in range(6):
            bo = o + (c - o) * (j / 6.0)
            bc = o + (c - o) * ((j + 1) / 6.0)
            rows.append(
                _row(
                    symbol,
                    "4h",
                    d_open_ms + j * H4_MS,
                    bo,
                    max(bo, bc) * 1.002,
                    min(bo, bc) * 0.998,
                    bc,
                )
            )
        for j in range(24):
            bo = o + (c - o) * (j / 24.0)
            bc = o + (c - o) * ((j + 1) / 24.0)
            rows.append(
                _row(
                    symbol,
                    "1h",
                    d_open_ms + j * H1_MS,
                    bo,
                    max(bo, bc) * 1.001,
                    min(bo, bc) * 0.999,
                    bc,
                )
            )
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _row(
    symbol: str,
    timeframe: str,
    open_time: int,
    o: float,
    h: float,
    lo: float,
    c: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": 1000.0,
        "taker_buy_volume": 500.0,
    }
