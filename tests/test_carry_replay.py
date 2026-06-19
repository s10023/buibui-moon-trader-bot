"""Tests for the read-only carry replay front door (in-memory DuckDB)."""

from __future__ import annotations

import duckdb
import numpy as np

from analytics.carry.config import CarryConfig
from analytics.carry.replay import replay_carry, replay_carry_trials
from analytics.store.schema import init_schema


def _seed(conn: duckdb.DuckDBPyConnection, n: int = 300) -> list[str]:
    init_schema(conn)
    rng = np.random.default_rng(7)
    syms = ["AAA", "BBB", "CCC"]
    day_ms = 86_400_000
    for k, sym in enumerate(syms):
        price = 100.0
        for i in range(n):
            price *= float(np.exp(rng.normal(0.0, 0.02)))
            t = i * day_ms
            conn.execute(
                "INSERT INTO ohlcv VALUES (?, '1d', ?, ?, ?, ?, ?, ?, ?)",
                [sym, t, price, price, price, price, 1000.0, 500.0],
            )
            for j in range(3):  # 3 funding rows per day
                conn.execute(
                    "INSERT INTO funding_rates VALUES (?, ?, ?)",
                    [sym, t + j * 8 * 3_600_000, ((-1) ** k) * 0.0001],
                )
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
