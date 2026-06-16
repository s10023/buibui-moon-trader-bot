from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools.forecast_audit import build_report_row

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
        for i in range(320)
    ]
    upsert_ohlcv(conn, pd.DataFrame(rows))


def test_build_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 320)
    row = build_report_row(conn, "label", symbols=["AAAUSDT"], slippage_bps=2.0)
    assert row["label"] == "label"
    assert "sharpe" in row and "max_dd" in row and "pbo" in row
