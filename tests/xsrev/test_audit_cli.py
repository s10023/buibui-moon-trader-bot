from __future__ import annotations

import duckdb
import pandas as pd

from analytics.store import init_schema
from analytics.store.market_data import upsert_ohlcv
from tools.xsrev_audit import build_xsrev_report_row

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


def test_build_xsrev_report_row_returns_dict() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    _seed(conn, "AAAUSDT", 1.0)
    _seed(conn, "BBBUSDT", -0.5)
    row = build_xsrev_report_row(
        conn, "label", symbols=["AAAUSDT", "BBBUSDT"], slippage_bps=2.0
    )
    assert row["label"] == "label"
    for col in (
        "sharpe",
        "dsr",
        "pbo",
        "boot_lo",
        "min_trl",
        "corr_to_xs",
        "xs_sharpe",
    ):
        assert col in row
