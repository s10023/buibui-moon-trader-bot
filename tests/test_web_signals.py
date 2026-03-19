"""Tests for the signals web endpoint."""

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


def _make_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
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


def _make_signals_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "direction": ["long"],
            "reason": ["FVG fill"],
            "sl_price": [29000.0],
            "context": ["bullish"],
        }
    )


def test_signals_returns_list(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signals endpoint returns detected signals."""
    monkeypatch.setattr(
        "web.api.routers.signals.get_ohlcv", lambda *a, **kw: _make_ohlcv()
    )
    monkeypatch.setattr(
        "web.api.routers.signals._detect_signals_for_strategy",
        lambda *a, **kw: _make_signals_df(),
    )
    monkeypatch.setattr("web.api.routers.signals.load_coins_config", lambda: {})

    resp = client_with_mock_db.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["fvg"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "signals" in data
    assert len(data["signals"]) == 1
    assert data["signals"][0]["direction"] == "long"
    assert data["signals"][0]["strategy"] == "fvg"


def test_signals_unknown_strategy_returns_422(
    client_with_mock_db: Any,
) -> None:
    """Unknown strategy in signals list returns 422."""
    resp = client_with_mock_db.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["not_real"],
        },
    )
    assert resp.status_code == 422


def test_signals_no_ohlcv_returns_404(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty OHLCV returns 404."""
    monkeypatch.setattr(
        "web.api.routers.signals.get_ohlcv", lambda *a, **kw: pd.DataFrame()
    )
    resp = client_with_mock_db.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["fvg"],
        },
    )
    assert resp.status_code == 404


def test_signals_empty_result(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No signals detected returns empty list (not an error)."""
    monkeypatch.setattr(
        "web.api.routers.signals.get_ohlcv", lambda *a, **kw: _make_ohlcv()
    )
    monkeypatch.setattr(
        "web.api.routers.signals._detect_signals_for_strategy",
        lambda *a, **kw: pd.DataFrame(),
    )
    monkeypatch.setattr("web.api.routers.signals.load_coins_config", lambda: {})

    resp = client_with_mock_db.post(
        "/api/signals",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "start_ms": 0,
            "end_ms": 9_999_999_999_999,
            "strategies": ["fvg"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["signals"] == []
