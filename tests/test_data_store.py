"""Tests for analytics/data_store.py."""

from typing import Any

import duckdb
import pandas as pd
import pytest

from analytics.data_store import (
    get_latest_open_time,
    get_ohlcv,
    get_signals_history,
    init_schema,
    upsert_funding_rates,
    upsert_ohlcv,
    upsert_open_interest,
    upsert_signals,
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
    "taker_buy_volume": 55.0,
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


_SIGNAL_ROW: dict[str, object] = {
    "symbol": "BTCUSDT",
    "timeframe": "1h",
    "strategy": "fvg",
    "open_time": 1_700_000_000_000,
    "direction": "long",
    "entry_price": 30500.0,
    "sl_price": 29000.0,
    "reason": "FVG filled",
    "confidence": 4,
    "fired_at": 1_700_000_001_000,
}


class TestInitSchema:
    def test_creates_tables(self, conn: duckdb.DuckDBPyConnection) -> None:
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert {"ohlcv", "funding_rates", "open_interest", "signals"} == tables

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


class TestTakerBuyVolume:
    def test_persists_taker_buy_volume(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        row = conn.execute("SELECT taker_buy_volume FROM ohlcv").fetchone()
        assert row is not None
        assert row[0] == 55.0

    def test_null_taker_buy_volume_accepted(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row = {**_OHLCV_ROW, "taker_buy_volume": None}
        upsert_ohlcv(conn, pd.DataFrame([row]))
        result = conn.execute("SELECT taker_buy_volume FROM ohlcv").fetchone()
        assert result is not None
        assert result[0] is None

    def test_migration_adds_column_to_existing_db(self) -> None:
        c = duckdb.connect(":memory:")
        c.execute("""
            CREATE TABLE ohlcv (
                symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                open_time BIGINT NOT NULL, open DOUBLE NOT NULL,
                high DOUBLE NOT NULL, low DOUBLE NOT NULL,
                close DOUBLE NOT NULL, volume DOUBLE NOT NULL,
                PRIMARY KEY (symbol, timeframe, open_time)
            )
        """)
        init_schema(c)
        cols = {
            r[0]
            for r in c.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'ohlcv'"
            ).fetchall()
        }
        assert "taker_buy_volume" in cols

    def test_get_ohlcv_returns_taker_buy_volume_column(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        upsert_ohlcv(conn, pd.DataFrame([_OHLCV_ROW]))
        result = get_ohlcv(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert "taker_buy_volume" in result.columns
        assert result.iloc[0]["taker_buy_volume"] == 55.0


class TestUpsertSignals:
    def test_inserts_rows(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 1

    def test_ignores_on_conflict(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        # Same PK — second insert should be ignored, not raise or update.
        upsert_signals(
            conn, pd.DataFrame([{**_SIGNAL_ROW, "reason": "updated reason"}])
        )
        row = _one(conn, "SELECT reason FROM signals")
        assert row[0] == "FVG filled"  # original preserved

    def test_empty_dataframe_is_noop(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame(columns=list(_SIGNAL_ROW.keys())))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 0

    def test_multiple_strategies_same_candle(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row2 = {**_SIGNAL_ROW, "strategy": "bos"}
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW, row2]))
        assert _one(conn, "SELECT COUNT(*) FROM signals")[0] == 2


class TestGetSignalsHistory:
    def test_returns_signals_in_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["strategy"] == "fvg"
        assert result.iloc[0]["direction"] == "long"

    def test_excludes_outside_range(self, conn: duckdb.DuckDBPyConnection) -> None:
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 1_000_000_000)
        assert result.empty

    def test_filters_by_symbol_and_timeframe(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        other = {**_SIGNAL_ROW, "symbol": "ETHUSDT"}
        upsert_signals(conn, pd.DataFrame([_SIGNAL_ROW, other]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "BTCUSDT"

    def test_returns_empty_when_no_data(self, conn: duckdb.DuckDBPyConnection) -> None:
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.empty

    def test_ordered_descending_by_open_time(
        self, conn: duckdb.DuckDBPyConnection
    ) -> None:
        row1 = {**_SIGNAL_ROW, "open_time": 1_700_000_000_000}
        row2 = {**_SIGNAL_ROW, "open_time": 1_700_003_600_000, "strategy": "bos"}
        upsert_signals(conn, pd.DataFrame([row1, row2]))
        result = get_signals_history(conn, "BTCUSDT", "1h", 0, 2_000_000_000_000)
        assert result.iloc[0]["open_time"] > result.iloc[1]["open_time"]


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
