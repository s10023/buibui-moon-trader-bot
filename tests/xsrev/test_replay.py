from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from analytics.xsmom.book import XSBookResult
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.replay import replay_xsrev, replay_xsrev_trials

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
    upsert_ohlcv(conn, pd.DataFrame(rows), venue="binance")


def test_replay_xsrev_returns_book_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    res = replay_xsrev(conn, ReversalConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, XSBookResult)
    assert res.portfolio_return.shape[0] > 0
    assert not np.isnan(res.portfolio_return).any()


def test_replay_xsrev_trials_has_per_window_plus_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    trials = replay_xsrev_trials(conn, ReversalConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert set(trials) == {"k2", "k3", "k5", "k7", "combined"}
    for v in trials.values():
        assert isinstance(v, np.ndarray)
