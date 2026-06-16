"""Tests for analytics.forecast.replay.replay_trials."""

from __future__ import annotations

import dataclasses

import duckdb
import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import replay_trials
from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int) -> None:
    t0 = 1_600_000_000_000
    rows = [
        {
            "symbol": symbol,
            "timeframe": "1d",
            "open_time": t0 + i * _DAY,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.0 + i,
            "volume": 1000.0,
            "taker_buy_volume": 500.0,
        }
        for i in range(n)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_replay_trials_has_per_speed_and_combined() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    trials = replay_trials(conn, ForecastConfig(), symbols=["AAAUSDT"])
    assert "combined" in trials
    assert "s8_32" in trials and "s64_256" in trials
    assert all(isinstance(v, np.ndarray) for v in trials.values())


def test_dataclasses_replace_speeds_is_single_pair() -> None:
    # guards the mechanism replay_trials uses
    cfg = ForecastConfig()
    one = dataclasses.replace(cfg, speeds=((8, 32, 5.3),))
    assert one.speeds == ((8, 32, 5.3),)
