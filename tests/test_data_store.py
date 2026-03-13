"""Tests for analytics/data_store.py."""

from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics.data_store import (
    get_latest_open_time,
    get_ohlcv,
    init_schema,
    upsert_funding_rates,
    upsert_ohlcv,
    upsert_open_interest,
)

_OHLCV_ROW: dict[str, object] = {
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "open_time": 1_700_000_000_000,
    "open": 30000.0,
    "high": 31000.0,
    "low": 29500.0,
    "close": 30500.0,
    "volume": 100.0,
}


def _one(conn: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    """Execute a query and return the single result row, asserting it exists."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    return row


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


class TestInitSchema:
    def test_creates_tables(self, conn: duckdb.DuckDBPyConnection) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert {"ohlcv", "funding_rates", "open_interest"} == tables

    def test_idempotent(self, conn: duckdb.DuckDBPyConnection) -> None:
        init_schema(conn)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert "ohlcv" in tables


class TestUpsertOhlcv:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        assert _one(conn, "SELECT COUNT(*) FROM ohlcv")[0] == 1

    def test_replaces_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        upsert_ohlcv(conn, pd.DataFrame([{**_OHLCV_ROW, "close": 99999.0}]))
        assert _one(conn, "SELECT close FROM ohlcv")[0] == 99999.0

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame(columns=list(_OHLCV_ROW.keys())))
        assert _one(conn, "SELECT COUNT(*) FROM ohlcv")[0] == 0


class TestUpsertFundingRates:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        df = pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "funding_time": 1_700_000_000_000,
                    "funding_rate": 0.0001,
                }
            ]
        )
        upsert_funding_rates(conn, df)
        assert _one(conn, "SELECT COUNT(*) FROM funding_rates")[0] == 1

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_funding_rates(
            conn, pd.DataFrame(columns=["symbol", "funding_time", "funding_rate"])
        )
        assert _one(conn, "SELECT COUNT(*) FROM funding_rates")[0] == 0


class TestUpsertOpenInterest:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        df = pd.DataFrame(
            [
                {
                    "symbol": "BTCUSDT",
                    "timestamp": 1_700_000_000_000,
                    "oi_usd": 30_000_000.0,
                }
            ]
        )
        upsert_open_interest(conn, df)
        assert _one(conn, "SELECT COUNT(*) FROM open_interest")[0] == 1

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_open_interest(
            conn, pd.DataFrame(columns=["symbol", "timestamp", "oi_usd"])
        )
        assert _one(conn, "SELECT COUNT(*) FROM open_interest")[0] == 0


class TestGetOhlcv:
    def test_returns_rows_in_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["close"] == 30500.0

    def test_excludes_rows_outside_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 1_000_000_000)
        assert result.empty

    def test_returns_empty_dataframe_when_no_data(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.empty


class TestGetLatestOpenTime:
    def test_returns_none_when_no_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        assert get_latest_open_time(conn, "BTCUSDT", "1h") is None

    def test_returns_max_open_time(self, conn: duckdb.DuckDBPyConnection) -> None:
        rows = [
            {**_OHLCV_ROW, "open_time": 1_700_000_000_000},
            {**_OHLCV_ROW, "open_time": 1_700_003_600_000},
        ]
        upsert_ohlcv(conn, pd.DataFrame(rows))
        assert get_latest_open_time(conn, "BTCUSDT", "1h") == 1_700_003_600_000
