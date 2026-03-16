import json
import os
import time
from pathlib import Path
from typing import Any

from binance.client import Client
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter

from utils.config_validation import validate_coins_config

_DEFAULT_COINS_PATH = Path(__file__).parent.parent / "config" / "coins.json"


def sync_binance_time(client: Client) -> None:
    """Sync client time offset with Binance server."""
    try:
        server_time = client.get_server_time()["serverTime"]
    except Exception as exc:
        raise RuntimeError(
            "Failed to sync time with Binance server — check connectivity"
        ) from exc
    local_time = int(time.time() * 1000)
    client.TIME_OFFSET = server_time - local_time


def create_client() -> Client:
    """Load env vars, create Binance client, sync time."""
    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set in .env")
    client = Client(api_key, api_secret)
    # Raise connection pool size to match the batch thread pool cap (16 workers)
    client.session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
    sync_binance_time(client)
    return client


def load_coins_config(path: Path | str = _DEFAULT_COINS_PATH) -> dict[str, Any]:
    """Load and validate coins.json, return config dict."""
    with open(path) as f:
        config: dict[str, Any] = json.load(f)
    validate_coins_config(config)
    return config


def get_wallet_target() -> tuple[list[float], list[str]]:
    """Load WALLET_TARGET from environment.

    Accepts a single value or comma-separated list (e.g. "500,1000,5000").
    Returns (valid_targets, invalid_entries) so callers can surface bad values
    in the display output rather than losing them to a log that gets cleared.
    """
    raw = os.getenv("WALLET_TARGET", "")
    if not raw.strip():
        return [], []
    targets: list[float] = []
    invalid: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        try:
            targets.append(float(part))
        except ValueError:
            invalid.append(part)
    return targets, invalid
