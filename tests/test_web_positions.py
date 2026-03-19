"""Tests for the positions web endpoint."""

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _mock_position_row() -> list[Any]:
    return [
        "BTCUSDT",  # 0: symbol
        "SHORT",  # 1: side
        "25",  # 2: leverage
        "110032.0",  # 3: entry
        "108757.0",  # 4: mark
        "595.99",  # 5: margin
        "14899.70",  # 6: notional
        "174.73",  # 7: pnl $
        "1.58",  # 8: pnl %
        "2.3%",  # 9: risk_pct
        "109970.0",  # 10: sl_price
        "0.135",  # 11: sl_size
        "148.60",  # 12: sl_usd
    ]


def test_positions_returns_200_with_data(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/positions returns 200 with position data."""
    monkeypatch.setattr(
        "web.api.routers.positions.load_coins_config",
        lambda: {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}},
    )
    monkeypatch.setattr(
        "web.api.routers.positions.fetch_open_positions",
        lambda *a, **kw: ([_mock_position_row()], 148.60, 1123.15, 481.01, 450.30),
    )
    resp = web_client.get("/api/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert "positions" in data
    assert len(data["positions"]) == 1
    assert data["positions"][0]["symbol"] == "BTCUSDT"
    assert data["positions"][0]["side"] == "SHORT"
    assert data["wallet_balance"] == pytest.approx(1123.15)
    assert data["total_risk_usd"] == pytest.approx(148.60)


def test_positions_empty_returns_empty_list(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/positions with no open positions returns empty list."""
    monkeypatch.setattr(
        "web.api.routers.positions.load_coins_config",
        lambda: {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}},
    )
    monkeypatch.setattr(
        "web.api.routers.positions.fetch_open_positions",
        lambda *a, **kw: ([], 0.0, 500.0, 0.0, 500.0),
    )
    resp = web_client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.json()["positions"] == []


def test_positions_sl_dash_maps_to_none(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SL columns with '-' are returned as null."""
    row = _mock_position_row()
    row[10] = "-"
    row[11] = "-"
    row[12] = "-"
    monkeypatch.setattr(
        "web.api.routers.positions.load_coins_config",
        lambda: {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}},
    )
    monkeypatch.setattr(
        "web.api.routers.positions.fetch_open_positions",
        lambda *a, **kw: ([row], 0.0, 1000.0, 0.0, 1000.0),
    )
    resp = web_client.get("/api/positions")
    assert resp.status_code == 200
    pos = resp.json()["positions"][0]
    assert pos["sl_price"] is None
    assert pos["sl_size"] is None
    assert pos["sl_usd"] is None


def test_positions_missing_token_returns_401(web_client: TestClient) -> None:
    """Requests without Bearer token return 401."""
    from web.api.deps import require_token
    from web.api.main import app

    app.dependency_overrides.pop(require_token, None)
    try:
        resp = web_client.get("/api/positions")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[require_token] = lambda: None


def test_positions_config_load_failure_returns_503(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exception from load_coins_config returns 503."""

    def _raise(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("coins.json missing")

    monkeypatch.setattr("web.api.routers.positions.load_coins_config", _raise)
    resp = web_client.get("/api/positions")
    assert resp.status_code == 503


def test_positions_fetch_error_returns_503(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RuntimeError from fetch_open_positions returns 503."""
    monkeypatch.setattr(
        "web.api.routers.positions.load_coins_config",
        lambda: {"BTCUSDT": {"leverage": 25, "sl_percent": 2.0}},
    )

    def _raise(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("binance down")

    monkeypatch.setattr("web.api.routers.positions.fetch_open_positions", _raise)
    resp = web_client.get("/api/positions")
    assert resp.status_code == 503
