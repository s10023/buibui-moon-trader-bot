from __future__ import annotations

import duckdb
import pandas as pd

from analytics.forecast.book import ForecastBookResult
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import load_daily_inputs, replay_universe
from analytics.store import init_schema
from analytics.store.market_data import upsert_funding_rates, upsert_ohlcv

_DAY = 86_400_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n: int) -> None:
    t0 = 1_600_000_000_000
    rows = []
    for i in range(n):
        price = 100.0 + i  # uptrend
        rows.append(
            {
                "symbol": symbol,
                "timeframe": "1d",
                "open_time": t0 + i * _DAY,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 1000.0,
                "taker_buy_volume": 500.0,
            }
        )
    upsert_ohlcv(conn, pd.DataFrame(rows))
    # funding 3x/day
    f = []
    for i in range(n * 3):
        f.append(
            {
                "symbol": symbol,
                "funding_time": t0 + i * (_DAY // 3),
                "funding_rate": 0.0001,
            }
        )
    upsert_funding_rates(conn, pd.DataFrame(f))


def test_load_daily_inputs_sums_funding_per_day() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    closes, fundings = load_daily_inputs(conn, ["AAAUSDT"])
    assert "AAAUSDT" in closes
    # 3 funding intervals/day x 0.0001 -> ~0.0003/day where covered
    assert abs(fundings["AAAUSDT"].dropna().iloc[10] - 0.0003) < 1e-9


def test_replay_universe_runs_and_returns_result() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    _seed(conn, "BBBUSDT", 320)
    res = replay_universe(conn, ForecastConfig(), symbols=["AAAUSDT", "BBBUSDT"])
    assert isinstance(res, ForecastBookResult)
    assert set(res.per_instrument_net) == {"AAAUSDT", "BBBUSDT"}
    assert len(res.daily_index) > 0
