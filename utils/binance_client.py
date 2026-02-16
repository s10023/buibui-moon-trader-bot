import json
import os
import time
from typing import Any

from binance.client import Client
from dotenv import load_dotenv

from utils.config_validation import validate_coins_config


def sync_binance_time(client: Client) -> None:
    """Sync client time offset with Binance server."""
    server_time = client.get_server_time()["serverTime"]
    local_time = int(time.time() * 1000)
    client.TIME_OFFSET = server_time - local_time


def create_client() -> Client:
    """Load env vars, create Binance client, sync time."""
    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    client = Client(api_key, api_secret)
    sync_binance_time(client)
    return client


def load_coins_config(path: str = "config/coins.json") -> dict[str, Any]:
    """Load and validate coins.json, return config dict."""
    with open(path) as f:
        config: dict[str, Any] = json.load(f)
    validate_coins_config(config)
    return config


def get_wallet_target() -> float:
    """Load WALLET_TARGET from environment."""
    load_dotenv()
    return float(os.getenv("WALLET_TARGET", "0"))
