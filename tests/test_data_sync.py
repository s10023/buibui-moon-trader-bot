"""Tests for analytics/data_sync.py."""

from typing import Any
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from analytics.data_fetcher import FUNDING_COLUMNS, KLINES_MAX_LIMIT, OHLCV_COLUMNS
from analytics.data_store import (
    get_funding_rates,
    get_latest_open_time,
    init_schema,
    upsert_ohlcv,
)
from analytics.data_sync import backfill, sync, sync_funding_rates


def _make_conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def _make_df(
    open_times: list[int], symbol: str = "BTCUSDT", timeframe: str = "1h"
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "open_time": t,
                "open": 30000.0,
                "high": 31000.0,
                "low": 29500.0,
                "close": 30500.0,
                "volume": 100.0,
            }
            for t in open_times
        ],
        columns=OHLCV_COLUMNS,
    )


class TestBackfill:
    def test_fetches_and_stores_single_batch(self) -> None:
        conn = _make_conn()
        df = _make_df([1_000, 2_000, 3_000])
        with patch("analytics.data_sync.fetch_klines", return_value=df):
            total = backfill(
                conn, object(), "BTCUSDT", "1h", 0, sleep_fn=lambda _: None
            )
        assert total == 3
        assert get_latest_open_time(conn, "BTCUSDT", "1h") == 3_000

    def test_stops_when_batch_is_smaller_than_limit(self) -> None:
        conn = _make_conn()
        small_df = _make_df(list(range(10)))
        with patch("analytics.data_sync.fetch_klines", return_value=small_df):
            total = backfill(
                conn, object(), "BTCUSDT", "1h", 0, sleep_fn=lambda _: None
            )
        assert total == 10

    def test_paginates_when_full_batch_returned(self) -> None:
        conn = _make_conn()
        full_df = _make_df(list(range(KLINES_MAX_LIMIT)))
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        call_count = 0

        def side_effect(*args: Any, **kwargs: Any) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            return full_df if call_count == 1 else empty_df

        with patch("analytics.data_sync.fetch_klines", side_effect=side_effect):
            backfill(conn, object(), "BTCUSDT", "1h", 0, sleep_fn=lambda _: None)
        assert call_count == 2

    def test_calls_sleep_between_batches(self) -> None:
        conn = _make_conn()
        full_df = _make_df(list(range(KLINES_MAX_LIMIT)))
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        sleep_calls: list[float] = []
        call_count = 0

        def side_effect(*args: Any, **kwargs: Any) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            return full_df if call_count == 1 else empty_df

        with patch("analytics.data_sync.fetch_klines", side_effect=side_effect):
            backfill(conn, object(), "BTCUSDT", "1h", 0, sleep_fn=sleep_calls.append)
        assert len(sleep_calls) == 1

    def test_returns_total_rows_upserted(self) -> None:
        conn = _make_conn()
        df = _make_df([1, 2, 3, 4, 5])
        with patch("analytics.data_sync.fetch_klines", return_value=df):
            total = backfill(
                conn, object(), "BTCUSDT", "1h", 0, sleep_fn=lambda _: None
            )
        assert total == 5


class TestSync:
    def test_fetches_from_latest_open_time(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _make_df([1_000_000]))
        captured: list[int] = []

        def capture(c: Any, cl: Any, sym: Any, tf: Any, start: int, **kw: Any) -> int:
            captured.append(start)
            return 0

        with patch("analytics.data_sync.backfill", side_effect=capture):
            sync(conn, object(), "BTCUSDT", "1h", sleep_fn=lambda _: None)
        assert captured == [1_000_000]

    def test_raises_when_no_existing_data(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="Run backfill first"):
            sync(conn, object(), "BTCUSDT", "1h")

    def test_returns_zero_when_no_new_data(self) -> None:
        conn = _make_conn()
        upsert_ohlcv(conn, _make_df([1_000_000]))
        empty_df = pd.DataFrame(columns=OHLCV_COLUMNS)
        with patch("analytics.data_sync.fetch_klines", return_value=empty_df):
            total = sync(conn, object(), "BTCUSDT", "1h", sleep_fn=lambda _: None)
        assert total == 0


def _make_funding_df(
    funding_times: list[int],
    symbol: str = "BTCUSDT",
    rate: float = 0.0001,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "funding_time": t,
                "funding_rate": rate,
            }
            for t in funding_times
        ],
        columns=FUNDING_COLUMNS,
    )


class TestSyncFundingRates:
    def test_returns_row_count_on_success(self) -> None:
        conn = _make_conn()
        df = _make_funding_df([1_000_000, 2_000_000, 3_000_000])
        with patch("analytics.data_sync.fetch_funding_rates", return_value=df):
            total = sync_funding_rates(conn, object(), "BTCUSDT")
        assert total == 3

    def test_returns_zero_on_empty_response(self) -> None:
        conn = _make_conn()
        empty_df = pd.DataFrame(columns=FUNDING_COLUMNS)
        with patch("analytics.data_sync.fetch_funding_rates", return_value=empty_df):
            total = sync_funding_rates(conn, object(), "BTCUSDT")
        assert total == 0

    def test_data_is_stored_in_db(self) -> None:
        conn = _make_conn()
        df = _make_funding_df([1_000_000, 2_000_000])
        with patch("analytics.data_sync.fetch_funding_rates", return_value=df):
            sync_funding_rates(conn, object(), "BTCUSDT")
        stored = get_funding_rates(conn, "BTCUSDT", 0, 9_999_999)
        assert len(stored) == 2
        assert list(stored["funding_time"]) == [1_000_000, 2_000_000]

    def test_limit_calculated_from_days(self) -> None:
        conn = _make_conn()
        empty_df = pd.DataFrame(columns=FUNDING_COLUMNS)
        captured_kwargs: list[Any] = []

        def capture(*args: Any, **kwargs: Any) -> pd.DataFrame:
            captured_kwargs.append(kwargs)
            return empty_df

        with patch("analytics.data_sync.fetch_funding_rates", side_effect=capture):
            sync_funding_rates(conn, object(), "BTCUSDT", days=30)
        # 30 days * 24h / 8h per funding = 90
        assert captured_kwargs[0]["limit"] == 90
