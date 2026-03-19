"""Tests for the OHLCV web endpoint."""

from typing import Any
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from web.api.deps import get_db, require_token
from web.api.main import app


@pytest.fixture()
def client_with_mock_db() -> Any:
    """TestClient with get_db and require_token overridden."""
    mock_conn = MagicMock(spec=duckdb.DuckDBPyConnection)
    app.dependency_overrides[get_db] = lambda: mock_conn
    app.dependency_overrides[require_token] = lambda: None
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_no_auth() -> None:
    """Health endpoint requires no auth."""
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_token_returns_403_or_401() -> None:
    """Requests without Bearer token are rejected."""
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/ohlcv?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=1")
    assert resp.status_code in (401, 403)


def test_ohlcv_returns_candles(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OHLCV endpoint returns candle data from mocked DB."""
    sample = pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "open": [30000.0],
            "high": [30500.0],
            "low": [29500.0],
            "close": [30200.0],
            "volume": [100.0],
            "taker_buy_volume": [50.0],
        }
    )
    monkeypatch.setattr("web.api.routers.ohlcv.get_ohlcv", lambda *a, **kw: sample)
    resp = client_with_mock_db.get(
        "/api/ohlcv?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=9999999999999"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candles"]) == 1
    assert data["candles"][0]["open_time"] == 1_700_000_000_000
    assert data["candles"][0]["open"] == 30000.0


def test_ohlcv_with_funding(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OHLCV endpoint includes funding when include_funding=true."""
    candle_df = pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "open": [30000.0],
            "high": [30500.0],
            "low": [29500.0],
            "close": [30200.0],
            "volume": [100.0],
            "taker_buy_volume": [None],
        }
    )
    funding_df = pd.DataFrame(
        {
            "funding_time": [1_700_000_000_000],
            "funding_rate": [0.0001],
        }
    )
    monkeypatch.setattr("web.api.routers.ohlcv.get_ohlcv", lambda *a, **kw: candle_df)
    monkeypatch.setattr(
        "web.api.routers.ohlcv.get_funding_rates", lambda *a, **kw: funding_df
    )
    resp = client_with_mock_db.get(
        "/api/ohlcv?symbol=BTCUSDT&timeframe=1h&start_ms=0&end_ms=9999999999999&include_funding=true"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["funding"] is not None
    assert len(data["funding"]) == 1
    assert data["funding"][0]["funding_rate"] == 0.0001
