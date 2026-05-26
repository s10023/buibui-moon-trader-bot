"""Tests for utils/binance_client.py — create_data_client DATA_SOURCE dispatch."""

import os
from unittest.mock import patch

from utils.binance_client import create_data_client
from utils.okx_client import OKXClient


def test_create_data_client_returns_okx_when_env_set() -> None:
    with patch.dict(os.environ, {"DATA_SOURCE": "okx"}):
        assert isinstance(create_data_client(), OKXClient)


def test_create_data_client_defaults_to_binance(monkeypatch: object) -> None:
    monkeypatch.delenv("DATA_SOURCE", raising=False)  # type: ignore[attr-defined]
    with patch("utils.binance_client.create_client", return_value="BINANCE") as m:
        assert create_data_client() == "BINANCE"
        m.assert_called_once()
