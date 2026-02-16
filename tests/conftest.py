"""Shared fixtures for tests.

Now that business logic lives in price_lib / position_lib with dependency
injection, we no longer need import-time patching of the Binance client
or builtins.open.  Tests import from the lib modules directly and pass
mock dependencies as parameters.
"""

import json
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from string."""
    return _ANSI_RE.sub("", text)


# --- Sample data used across all tests ---

SAMPLE_COINS_CONFIG: dict[str, Any] = {
    "BTCUSDT": {"leverage": 25, "sl_percent": 2.0},
    "ETHUSDT": {"leverage": 20, "sl_percent": 2.5},
    "SOLUSDT": {"leverage": 20, "sl_percent": 3.5},
}

SAMPLE_CONFIG_JSON = json.dumps(SAMPLE_COINS_CONFIG)

SAMPLE_COIN_ORDER = list(SAMPLE_COINS_CONFIG.keys())


def _create_mock_client() -> MagicMock:
    """Create a mock Binance client with common stubs."""
    client = MagicMock()
    client.get_server_time.return_value = {"serverTime": 1700000000000}
    client.TIME_OFFSET = 0
    return client


@pytest.fixture
def sample_coins_config() -> dict[str, Any]:
    """Valid coins.json configuration."""
    return SAMPLE_COINS_CONFIG.copy()


@pytest.fixture
def sample_coin_order() -> list[str]:
    """Coin order from config."""
    return SAMPLE_COIN_ORDER.copy()


@pytest.fixture
def mock_binance_client() -> MagicMock:
    """Fresh mock Binance Client."""
    return _create_mock_client()


@pytest.fixture
def mock_ticker_data() -> list[dict[str, Any]]:
    """Sample ticker response from Binance."""
    return [
        {
            "symbol": "BTCUSDT",
            "lastPrice": "62457.10",
            "priceChangePercent": "2.31",
        },
        {
            "symbol": "ETHUSDT",
            "lastPrice": "3408.50",
            "priceChangePercent": "1.74",
        },
        {
            "symbol": "SOLUSDT",
            "lastPrice": "143.22",
            "priceChangePercent": "0.89",
        },
    ]


@pytest.fixture
def mock_kline_data() -> list[Any]:
    """Sample kline (candlestick) data."""
    return [
        1700000000000,  # open time
        "62000.00",  # open
        "62500.00",  # high
        "61800.00",  # low
        "62457.10",  # close
        "1000.0",  # volume
        1700000060000,  # close time
        "62000000.0",  # quote asset volume
        500,  # number of trades
        "500.0",  # taker buy base volume
        "31000000.0",  # taker buy quote volume
        "0",  # ignore
    ]


@pytest.fixture
def mock_futures_balance() -> list[dict[str, Any]]:
    """Sample futures account balance response."""
    return [
        {
            "asset": "USDT",
            "balance": "1123.15",
            "crossUnPnl": "290.29",
        },
        {
            "asset": "BNB",
            "balance": "0.50",
            "crossUnPnl": "0.00",
        },
    ]


@pytest.fixture
def mock_positions_data() -> list[dict[str, Any]]:
    """Sample futures position information response."""
    return [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "-0.135",
            "entryPrice": "110032.0",
            "markPrice": "108757.0",
            "notional": "-14899.70",
            "positionInitialMargin": "595.99",
            "unRealizedProfit": "174.73",
        },
        {
            "symbol": "ETHUSDT",
            "positionAmt": "-4.5",
            "entryPrice": "2616.17",
            "markPrice": "2550.10",
            "notional": "-11822.30",
            "positionInitialMargin": "591.11",
            "unRealizedProfit": "306.29",
        },
        {
            "symbol": "SOLUSDT",
            "positionAmt": "0",
            "entryPrice": "0.0",
            "markPrice": "143.22",
            "notional": "0",
            "positionInitialMargin": "0",
            "unRealizedProfit": "0.0",
        },
    ]


@pytest.fixture
def mock_stop_loss_orders() -> list[dict[str, Any]]:
    """Sample open orders with stop-loss."""
    return [
        {
            "symbol": "BTCUSDT",
            "type": "STOP_MARKET",
            "reduceOnly": True,
            "stopPrice": "109970.0",
        },
    ]
