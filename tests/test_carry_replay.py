"""Tests for the read-only carry replay front door (in-memory DuckDB)."""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np
import pandas as pd

from analytics.carry.config import CarryConfig
from analytics.carry.replay import replay_carry, replay_carry_trials
from analytics.store.schema import init_schema

_OHLCV_COLS = (
    "venue",
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "taker_buy_volume",
)
_FUNDING_COLS = ("symbol", "funding_time", "funding_rate")


def _bulk_insert(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    columns: tuple[str, ...],
    rows: list[list[Any]],
) -> None:
    """Load `rows` into `table` as ONE columnar insert, columns named both sides."""
    frame = pd.DataFrame(rows, columns=list(columns))
    cols = ", ".join(columns)
    conn.register("_seed_rows", frame)
    try:
        conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _seed_rows")
    finally:
        conn.unregister("_seed_rows")


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 300) -> list[str]:
    init_schema(conn)
    rng = np.random.default_rng(7)
    syms = ["AAA", "BBB", "CCC"]
    day_ms = 86_400_000
    bars: list[list[Any]] = []
    funding: list[list[Any]] = []
    for k, sym in enumerate(syms):
        price = 100.0
        for i in range(n):
            price *= float(np.exp(rng.normal(0.0, 0.02)))
            t = i * day_ms
            bars.append(
                ["binance", sym, "1d", t, price, price, price, price, 1000.0, 500.0]
            )
            for j in range(3):  # 3 funding rows per day
                funding.append([sym, t + j * 8 * 3_600_000, ((-1) ** k) * 0.0001])
    # ST125 — two columnar inserts rather than 3,600 round trips. Same class as the
    # ST104 seed (`tests/test_st104_score.py::_insert_trades`, which carries the
    # measurement): DuckDB is columnar, so a row-at-a-time seed spends its whole
    # margin against pytest's global `timeout = 30` on a contended runner, and
    # `executemany` is still one round trip per row.
    _bulk_insert(conn, "ohlcv_all", _OHLCV_COLS, bars)
    _bulk_insert(conn, "funding_rates", _FUNDING_COLS, funding)
    return syms


def test_replay_carry_shape() -> None:
    conn = duckdb.connect(":memory:")
    syms = _seed(conn)
    cfg = CarryConfig(carry_spans=(1, 5))
    res = replay_carry(conn, cfg, symbols=syms)
    assert len(res.daily_index) == 300
    assert res.portfolio_return.shape == (300,)
    assert np.isfinite(res.portfolio_return).all()


def test_replay_carry_trials_keys() -> None:
    conn = duckdb.connect(":memory:")
    syms = _seed(conn)
    cfg = CarryConfig(carry_spans=(1, 5))
    trials = replay_carry_trials(conn, cfg, symbols=syms)
    assert set(trials) == {"span1", "span5", "combined"}
    for v in trials.values():
        assert v.shape == (300,)
