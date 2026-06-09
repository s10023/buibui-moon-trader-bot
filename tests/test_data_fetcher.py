"""Tests for analytics/data_fetcher.py."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from analytics.data_fetcher import (
    FUNDING_COLUMNS,
    KLINES_MAX_LIMIT,
    OHLCV_COLUMNS,
    OI_COLUMNS,
    fetch_funding_rates,
    fetch_klines,
    fetch_open_interest,
)

_KLINE_RAW: list[Any] = [
    1_700_000_000_000,
    "30000.0",
    "31000.0",
    "29500.0",
    "30500.0",
    "100.0",
    1_700_003_599_999,
    "3050000.0",
    1000,
    "50.0",
    "1525000.0",
    "0",
]
_FUNDING_RAW: dict[str, Any] = {
    "symbol": "BTCUSDT",
    "fundingTime": 1_700_000_000_000,
    "fundingRate": "0.0001",
    "markPrice": "30000.0",
}
_OI_RAW: dict[str, Any] = {
    "symbol": "BTCUSDT",
    "sumOpenInterest": "1000.0",
    "sumOpenInterestValue": "30000000.0",
    "timestamp": 1_700_000_000_000,
}


class TestFetchKlines:
    def test_returns_dataframe_with_correct_columns(self) -> None:
        client = MagicMock()
        client.futures_klines.return_value = [_KLINE_RAW]
        df = fetch_klines(client, "BTCUSDT", "1h", 0)
        assert list(df.columns) == OHLCV_COLUMNS

    def test_returns_empty_dataframe_on_empty_response(self) -> None:
        client = MagicMock()
        client.futures_klines.return_value = []
        df = fetch_klines(client, "BTCUSDT", "1h", 0)
        assert df.empty
        assert list(df.columns) == OHLCV_COLUMNS

    def test_casts_price_fields_to_float(self) -> None:
        client = MagicMock()
        client.futures_klines.return_value = [_KLINE_RAW]
        df = fetch_klines(client, "BTCUSDT", "1h", 0)
        assert df.iloc[0]["open"] == 30000.0
        assert df.iloc[0]["close"] == 30500.0

    def test_sets_symbol_and_timeframe_columns(self) -> None:
        client = MagicMock()
        client.futures_klines.return_value = [_KLINE_RAW]
        df = fetch_klines(client, "BTCUSDT", "1h", 0)
        assert df.iloc[0]["symbol"] == "BTCUSDT"
        assert df.iloc[0]["timeframe"] == "1h"

    def test_raises_on_api_error(self) -> None:
        client = MagicMock()
        client.futures_klines.side_effect = Exception("API error")
        with pytest.raises(Exception, match="API error"):
            fetch_klines(client, "BTCUSDT", "1h", 0)

    def test_includes_taker_buy_volume(self) -> None:
        client = MagicMock()
        client.futures_klines.return_value = [_KLINE_RAW]
        df = fetch_klines(client, "BTCUSDT", "1h", 0)
        assert "taker_buy_volume" in df.columns
        assert df.iloc[0]["taker_buy_volume"] == 50.0

    def test_empty_response_has_taker_buy_volume_column(self) -> None:
        client = MagicMock()
        client.futures_klines.return_value = []
        df = fetch_klines(client, "BTCUSDT", "1h", 0)
        assert "taker_buy_volume" in df.columns

    def test_max_limit_constant(self) -> None:
        assert KLINES_MAX_LIMIT == 1000


class TestFetchFundingRates:
    def test_returns_dataframe_with_correct_columns(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = [_FUNDING_RAW]
        df = fetch_funding_rates(client, "BTCUSDT")
        assert list(df.columns) == FUNDING_COLUMNS

    def test_returns_empty_dataframe_on_empty_response(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = []
        df = fetch_funding_rates(client, "BTCUSDT")
        assert df.empty
        assert list(df.columns) == FUNDING_COLUMNS

    def test_maps_binance_field_names(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = [_FUNDING_RAW]
        df = fetch_funding_rates(client, "BTCUSDT")
        assert df.iloc[0]["funding_time"] == 1_700_000_000_000
        assert df.iloc[0]["funding_rate"] == 0.0001

    def test_start_time_passes_time_kwargs(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = [_FUNDING_RAW]
        fetch_funding_rates(
            client, "BTCUSDT", limit=1000, start_time=1_000, end_time=2_000
        )
        kwargs = client.futures_funding_rate.call_args.kwargs
        assert kwargs["startTime"] == 1_000
        assert kwargs["endTime"] == 2_000
        assert kwargs["limit"] == 1000

    def test_omits_time_kwargs_when_not_given(self) -> None:
        client = MagicMock()
        client.futures_funding_rate.return_value = [_FUNDING_RAW]
        fetch_funding_rates(client, "BTCUSDT")
        kwargs = client.futures_funding_rate.call_args.kwargs
        assert "startTime" not in kwargs
        assert "endTime" not in kwargs


class TestFetchOpenInterest:
    def test_returns_dataframe_with_correct_columns(self) -> None:
        client = MagicMock()
        client.futures_open_interest_hist.return_value = [_OI_RAW]
        df = fetch_open_interest(client, "BTCUSDT", "1h")
        assert list(df.columns) == OI_COLUMNS

    def test_returns_empty_dataframe_on_empty_response(self) -> None:
        client = MagicMock()
        client.futures_open_interest_hist.return_value = []
        df = fetch_open_interest(client, "BTCUSDT", "1h")
        assert df.empty
        assert list(df.columns) == OI_COLUMNS

    def test_maps_open_interest_value(self) -> None:
        client = MagicMock()
        client.futures_open_interest_hist.return_value = [_OI_RAW]
        df = fetch_open_interest(client, "BTCUSDT", "1h")
        assert df.iloc[0]["oi_usd"] == 30_000_000.0
        assert df.iloc[0]["timestamp"] == 1_700_000_000_000


class TestFetchKlinesOKXPassthrough:
    """fetch_klines must return an OKXClient's DataFrame directly (no re-map)."""

    def test_passes_through_okx_dataframe(self) -> None:
        from utils.okx_client import OKXClient

        class _Resp:
            status_code = 200

            def json(self) -> dict[str, Any]:
                # one confirmed candle at ts 1000
                return {
                    "code": "0",
                    "data": [["1000", "1", "2", "0.5", "1.5", "100", "x", "y", "1"]],
                }

            def raise_for_status(self) -> None:
                return None

        class _Session:
            def get(self, url: str, params: dict[str, Any], timeout: float) -> "_Resp":
                return _Resp()

        client = OKXClient(session=_Session())
        df = fetch_klines(client, "BTCUSDT", "1h", 1000)
        assert list(df.columns) == OHLCV_COLUMNS
        assert df["open_time"].iloc[0] == 1000
        assert float(df["taker_buy_volume"].iloc[0]) == 100 / 2
