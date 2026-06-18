from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.combine.book import CombinedBookResult
from analytics.combine.config import CombineConfig
from analytics.combine.replay import (
    load_sleeves,
    replay_combined,
    replay_combined_trials,
)
from analytics.forecast.book import ForecastBookResult
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from analytics.xsmom.book import XSBookResult

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, slope: float) -> None:
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
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    return conn


def test_load_sleeves_returns_both_results() -> None:
    conn = _conn()
    xs, tr = load_sleeves(conn, CombineConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(xs, XSBookResult)
    assert isinstance(tr, ForecastBookResult)


def test_replay_combined_returns_book_result() -> None:
    conn = _conn()
    res = replay_combined(conn, CombineConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, CombinedBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_combined_trials_keys() -> None:
    conn = _conn()
    trials = replay_combined_trials(
        conn, CombineConfig(), symbols=["AAAUSDT", "BBBUSDT"]
    )
    assert set(trials) == {"trend", "xs", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
