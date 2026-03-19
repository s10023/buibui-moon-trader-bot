"""Tests for the backtest web endpoint."""

from typing import Any
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from analytics.backtest_lib import BacktestResult, Trade
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
            "open_time": [1_700_000_000_000, 1_700_003_600_000],
            "open": [30000.0, 30100.0],
            "high": [30500.0, 31000.0],
            "low": [29500.0, 29800.0],
            "close": [30200.0, 30900.0],
            "volume": [100.0, 120.0],
            "taker_buy_volume": [50.0, 60.0],
        }
    )


def _make_signals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1_700_000_000_000],
            "direction": ["long"],
            "sl_price": [29000.0],
        }
    )


def test_backtest_returns_result(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backtest endpoint returns a valid BacktestResponse."""
    ohlcv_df = _make_ohlcv()
    signals_df = _make_signals()

    monkeypatch.setattr("web.api.routers.backtest.get_ohlcv", lambda *a, **kw: ohlcv_df)
    monkeypatch.setattr(
        "web.api.routers.backtest._detect_signals_for_strategy",
        lambda *a, **kw: signals_df,
    )

    result = BacktestResult(symbol="BTCUSDT", timeframe="1h", strategy="fvg")
    trade = Trade(
        signal_time=1_700_000_000_000,
        entry_time=1_700_003_600_000,
        entry_price=30100.0,
        direction="long",
        sl_price=29000.0,
        tp_price=32300.0,
        exit_time=1_700_007_200_000,
        exit_price=32300.0,
        outcome="win",
    )
    result.trades.append(trade)

    monkeypatch.setattr(
        "web.api.routers.backtest.run_backtest", lambda *a, **kw: result
    )

    resp = client_with_mock_db.post(
        "/api/backtest",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "fvg",
            "days": 90,
            "sl_pct": 0.02,
            "tp_r": 2.0,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["strategy"] == "fvg"
    assert data["total_trades"] == 1
    assert len(data["trades"]) == 1
    assert data["trades"][0]["outcome"] == "win"


def test_backtest_unknown_strategy_returns_422(
    client_with_mock_db: Any,
) -> None:
    """Unknown strategy returns 422."""
    resp = client_with_mock_db.post(
        "/api/backtest",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "strategy": "not_a_strategy",
        },
    )
    assert resp.status_code == 422


def test_backtest_no_data_returns_404(
    client_with_mock_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty OHLCV returns 404."""
    monkeypatch.setattr(
        "web.api.routers.backtest.get_ohlcv",
        lambda *a, **kw: pd.DataFrame(),
    )
    resp = client_with_mock_db.post(
        "/api/backtest",
        json={"symbol": "BTCUSDT", "timeframe": "1h", "strategy": "fvg"},
    )
    assert resp.status_code == 404
