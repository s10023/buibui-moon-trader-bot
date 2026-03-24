"""Tests for positions write endpoints: close, sl, tp, cancel order."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# ── helpers ──────────────────────────────────────────────────────────────────


def _pos(symbol: str, side: str, amt: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "positionSide": side,
        "positionAmt": str(amt),
        "entryPrice": "50000.0",
        "markPrice": "51000.0",
        "notional": str(abs(amt) * 50000),
        "positionInitialMargin": "500.0",
        "unRealizedProfit": "100.0",
    }


# ── close position ─────────────────────────────────────────────────────────


def test_close_position_long_places_sell_market(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/positions/close on a LONG position places a SELL MARKET reduce-only order."""
    mock_client = MagicMock()
    mock_client.futures_position_information.return_value = [
        _pos("BTCUSDT", "LONG", 0.1)
    ]
    monkeypatch.setattr("web.api.routers.positions.get_client", lambda req: mock_client)

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/close",
        json={"symbol": "BTCUSDT", "position_side": "LONG"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    mock_client.futures_create_order.assert_called_once()
    call_kwargs = mock_client.futures_create_order.call_args[1]
    assert call_kwargs["side"] == "SELL"
    assert call_kwargs["type"] == "MARKET"
    assert call_kwargs["reduceOnly"] is True

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


def test_close_position_short_places_buy_market(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/positions/close on a SHORT position places a BUY MARKET reduce-only order."""
    mock_client = MagicMock()
    mock_client.futures_position_information.return_value = [
        _pos("BTCUSDT", "SHORT", -0.1)
    ]

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/close",
        json={"symbol": "BTCUSDT", "position_side": "SHORT"},
    )
    assert resp.status_code == 200
    call_kwargs = mock_client.futures_create_order.call_args[1]
    assert call_kwargs["side"] == "BUY"

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


def test_close_position_not_found_returns_404(web_client: TestClient) -> None:
    """/positions/close returns 404 when positionAmt is zero."""
    mock_client = MagicMock()
    mock_client.futures_position_information.return_value = [
        _pos("BTCUSDT", "LONG", 0.0)
    ]

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/close",
        json={"symbol": "BTCUSDT", "position_side": "LONG"},
    )
    assert resp.status_code == 404

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


def test_close_position_binance_error_returns_503(web_client: TestClient) -> None:
    """/positions/close propagates Binance errors as 503."""
    mock_client = MagicMock()
    mock_client.futures_position_information.return_value = [
        _pos("BTCUSDT", "SHORT", -0.1)
    ]
    mock_client.futures_create_order.side_effect = RuntimeError("API error")

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/close",
        json={"symbol": "BTCUSDT", "position_side": "SHORT"},
    )
    assert resp.status_code == 503

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


# ── modify SL ─────────────────────────────────────────────────────────────────


def test_modify_sl_places_stop_market_order(web_client: TestClient) -> None:
    """/positions/sl places a STOP_MARKET closePosition order."""
    mock_client = MagicMock()
    mock_client.futures_get_open_orders.return_value = []

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/sl",
        json={"symbol": "BTCUSDT", "position_side": "SHORT", "stop_price": 52000.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    call_kwargs = mock_client.futures_create_order.call_args[1]
    assert call_kwargs["type"] == "STOP_MARKET"
    assert call_kwargs["stopPrice"] == 52000.0
    assert call_kwargs["closePosition"] is True

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


def test_modify_sl_cancels_existing_sl_first(web_client: TestClient) -> None:
    """/positions/sl cancels existing STOP_MARKET orders before placing a new one."""
    existing_order: dict[str, Any] = {
        "orderId": 99,
        "symbol": "BTCUSDT",
        "type": "STOP_MARKET",
        "positionSide": "SHORT",
    }
    mock_client = MagicMock()
    mock_client.futures_get_open_orders.return_value = [existing_order]

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/sl",
        json={"symbol": "BTCUSDT", "position_side": "SHORT", "stop_price": 52000.0},
    )
    assert resp.status_code == 200
    mock_client.futures_cancel_order.assert_called_once_with(
        symbol="BTCUSDT", orderId=99
    )

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


def test_modify_sl_binance_error_returns_503(web_client: TestClient) -> None:
    """/positions/sl propagates Binance errors as 503."""
    mock_client = MagicMock()
    mock_client.futures_get_open_orders.return_value = []
    mock_client.futures_create_order.side_effect = RuntimeError("rejected")

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/sl",
        json={"symbol": "BTCUSDT", "position_side": "SHORT", "stop_price": 52000.0},
    )
    assert resp.status_code == 503

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


# ── modify TP ─────────────────────────────────────────────────────────────────


def test_modify_tp_places_take_profit_market_order(web_client: TestClient) -> None:
    """/positions/tp places a TAKE_PROFIT_MARKET closePosition order."""
    mock_client = MagicMock()
    mock_client.futures_get_open_orders.return_value = []

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.post(
        "/api/positions/tp",
        json={"symbol": "BTCUSDT", "position_side": "LONG", "stop_price": 55000.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    call_kwargs = mock_client.futures_create_order.call_args[1]
    assert call_kwargs["type"] == "TAKE_PROFIT_MARKET"
    assert call_kwargs["stopPrice"] == 55000.0
    assert call_kwargs["closePosition"] is True

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


# ── cancel order ──────────────────────────────────────────────────────────────


def test_cancel_order_calls_futures_cancel(web_client: TestClient) -> None:
    """/orders/{orderId} calls futures_cancel_order and returns ok."""
    mock_client = MagicMock()

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.delete("/api/orders/12345?symbol=BTCUSDT")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    mock_client.futures_cancel_order.assert_called_once_with(
        symbol="BTCUSDT", orderId=12345
    )

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None


def test_cancel_order_binance_error_returns_503(web_client: TestClient) -> None:
    """/orders/{orderId} propagates Binance errors as 503."""
    mock_client = MagicMock()
    mock_client.futures_cancel_order.side_effect = RuntimeError("order not found")

    from web.api.deps import get_client
    from web.api.main import app

    app.dependency_overrides[get_client] = lambda: mock_client

    resp = web_client.delete("/api/orders/99999?symbol=BTCUSDT")
    assert resp.status_code == 503

    app.dependency_overrides.clear()
    from web.api.deps import require_token

    app.dependency_overrides[require_token] = lambda: None
