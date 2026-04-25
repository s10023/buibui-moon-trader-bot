"""Tests for the SSE stream web endpoints."""

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi.testclient import TestClient


async def _one_price_event(client: Any) -> AsyncGenerator[str]:
    data = [
        {
            "symbol": "BTCUSDT",
            "last_price": "62457.10",
            "change_15m": "+0.20%",
            "change_1h": "+1.50%",
            "change_4h": "+0.80%",
            "change_asia": "+1.20%",
            "change_24h": "+2.31%",
        }
    ]
    yield f"data: {json.dumps(data)}\n\n"


async def _one_positions_event(client: Any) -> AsyncGenerator[str]:
    data = {
        "positions": [
            {
                "symbol": "BTCUSDT",
                "side": "SHORT",
                "leverage": 25,
                "entry_price": 110032.0,
                "mark_price": 108757.0,
                "margin": 595.99,
                "notional": 14899.70,
                "pnl": 174.73,
                "pnl_pct": 1.58,
                "risk_pct": "2.3%",
                "sl_price": 109970.0,
                "sl_size": "0.135",
                "sl_usd": "148.60",
            }
        ],
        "wallet_balance": 1123.15,
        "unrealized_pnl": 481.01,
        "available_balance": 450.30,
        "total_risk_usd": 148.60,
    }
    yield f"data: {json.dumps(data)}\n\n"


def test_stream_prices_content_type(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/stream/prices returns text/event-stream content type."""
    monkeypatch.setattr(
        "web.api.routers.stream._price_event_generator",
        _one_price_event,
    )
    with web_client.stream("GET", "/api/stream/prices") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        resp.read()  # drain stream before close to avoid anyio hang on Python 3.12+


def test_stream_prices_first_event_shape(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First SSE event from /api/stream/prices contains expected price fields."""
    monkeypatch.setattr(
        "web.api.routers.stream._price_event_generator",
        _one_price_event,
    )
    with web_client.stream("GET", "/api/stream/prices") as resp:
        chunk = next(resp.iter_lines())
    assert chunk.startswith("data: ")
    payload = json.loads(chunk[len("data: ") :])
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["symbol"] == "BTCUSDT"
    assert "last_price" in payload[0]
    assert "change_24h" in payload[0]


def test_stream_positions_content_type(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/stream/positions returns text/event-stream content type."""
    monkeypatch.setattr(
        "web.api.routers.stream._positions_event_generator",
        _one_positions_event,
    )
    with web_client.stream("GET", "/api/stream/positions") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        resp.read()  # drain stream before close to avoid anyio hang on Python 3.12+


def test_stream_positions_first_event_shape(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First SSE event from /api/stream/positions contains positions + wallet fields."""
    monkeypatch.setattr(
        "web.api.routers.stream._positions_event_generator",
        _one_positions_event,
    )
    with web_client.stream("GET", "/api/stream/positions") as resp:
        chunk = next(resp.iter_lines())
    assert chunk.startswith("data: ")
    payload = json.loads(chunk[len("data: ") :])
    assert "positions" in payload
    assert len(payload["positions"]) == 1
    assert payload["positions"][0]["symbol"] == "BTCUSDT"
    assert "wallet_balance" in payload
    assert "total_risk_usd" in payload


def test_stream_prices_missing_token_returns_401(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stream prices without ?token= returns 401 when API_TOKEN is set."""
    from web.api.deps import require_token_sse
    from web.api.main import app

    monkeypatch.setenv("API_TOKEN", "test-secret")
    app.dependency_overrides.pop(require_token_sse, None)
    try:
        resp = web_client.get("/api/stream/prices")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[require_token_sse] = lambda: None


def test_stream_positions_missing_token_returns_401(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stream positions without ?token= returns 401 when API_TOKEN is set."""
    from web.api.deps import require_token_sse
    from web.api.main import app

    monkeypatch.setenv("API_TOKEN", "test-secret")
    app.dependency_overrides.pop(require_token_sse, None)
    try:
        resp = web_client.get("/api/stream/positions")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[require_token_sse] = lambda: None
