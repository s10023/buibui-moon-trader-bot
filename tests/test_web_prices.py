"""Tests for the prices web endpoint."""

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _mock_price_row() -> list[str]:
    return ["BTCUSDT", "62457.10", "+2.31%", "+1.50%", "+0.80%", "+1.20%", "+2.31%"]


def test_prices_returns_200_with_data(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/prices returns 200 with price rows."""
    monkeypatch.setattr(
        "web.api.routers.prices.load_coins_config",
        lambda: {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}},
    )
    monkeypatch.setattr(
        "web.api.routers.prices.get_price_changes",
        lambda *a, **kw: ([_mock_price_row()], []),
    )
    resp = web_client.get("/api/prices")
    assert resp.status_code == 200
    data = resp.json()
    assert "prices" in data
    assert len(data["prices"]) == 1
    assert data["prices"][0]["symbol"] == "BTCUSDT"
    assert data["prices"][0]["last_price"] == "62457.10"
    assert data["prices"][0]["change_24h"] == "+2.31%"


def test_prices_empty_symbols_returns_empty_list(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty coins config returns empty prices list."""
    monkeypatch.setattr("web.api.routers.prices.load_coins_config", lambda: {})
    monkeypatch.setattr(
        "web.api.routers.prices.get_price_changes",
        lambda *a, **kw: ([], []),
    )
    resp = web_client.get("/api/prices")
    assert resp.status_code == 200
    assert resp.json()["prices"] == []


def test_prices_multiple_symbols(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multiple symbols are returned in order."""
    rows = [
        ["BTCUSDT", "62457.10", "+2.31%", "+1.50%", "+0.80%", "+1.20%", "+2.31%"],
        ["ETHUSDT", "3408.50", "+1.74%", "+0.90%", "+0.50%", "+0.80%", "+1.74%"],
    ]
    monkeypatch.setattr(
        "web.api.routers.prices.load_coins_config",
        lambda: {
            "BTCUSDT": {"leverage": 25, "sl_percent": 2.0},
            "ETHUSDT": {"leverage": 20, "sl_percent": 2.5},
        },
    )
    monkeypatch.setattr(
        "web.api.routers.prices.get_price_changes",
        lambda *a, **kw: (rows, []),
    )
    resp = web_client.get("/api/prices")
    assert resp.status_code == 200
    prices = resp.json()["prices"]
    assert len(prices) == 2
    assert prices[0]["symbol"] == "BTCUSDT"
    assert prices[1]["symbol"] == "ETHUSDT"


def test_prices_missing_token_returns_401(web_client: TestClient) -> None:
    """Requests without Bearer token return 401."""
    from web.api.deps import require_token
    from web.api.main import app

    app.dependency_overrides.pop(require_token, None)
    try:
        resp = web_client.get("/api/prices")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[require_token] = lambda: None


def test_prices_config_load_failure_returns_503(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception from load_coins_config returns 503."""

    def _raise(*a: Any, **kw: Any) -> Any:
        raise FileNotFoundError("coins.json missing")

    monkeypatch.setattr("web.api.routers.prices.load_coins_config", _raise)
    resp = web_client.get("/api/prices")
    assert resp.status_code == 503
