"""Tests for analytics.forecast.replay.replay_weight_schemes."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import replay_universe, replay_weight_schemes
from analytics.forecast.weights import candidate_schemes
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int, slope: float) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + slope * i,
            "high": 101.0 + slope * i,
            "low": 99.0 + slope * i,
            "close": 100.0 + slope * i,
            "volume": 1000.0,
            "taker_buy_volume": 500.0,
        }
        for i in range(n)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_weight_schemes_has_all_schemes() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320, 1.0)
    out = replay_weight_schemes(conn, ForecastConfig(), symbols=["AAAUSDT"])
    assert set(out) == set(candidate_schemes(ForecastConfig()))
    assert all(isinstance(v, ForecastBookResult) for v in out.values())


def test_equal_scheme_matches_default_combined_book() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320, 1.0)
    _seed(conn, "BBBUSDT", 320, 0.5)
    syms = ["AAAUSDT", "BBBUSDT"]
    out = replay_weight_schemes(conn, ForecastConfig(), symbols=syms)
    default = replay_universe(conn, ForecastConfig(), symbols=syms)
    np.testing.assert_allclose(
        out["equal"].portfolio_return,
        default.portfolio_return,
        rtol=1e-9,
        atol=1e-12,
    )
