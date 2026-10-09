"""Tests for the SSE stream web endpoints."""

import json
from collections.abc import AsyncGenerator, Generator
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


# ── SSE auth (#986) ───────────────────────────────────────────────────────────

_STREAMS = ["/api/stream/prices", "/api/stream/positions"]


@pytest.fixture()
def sse_auth(
    web_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient]:
    """web_client with the REAL SSE and Bearer checks and one-event streams."""
    from web.api.deps import require_token, require_token_sse
    from web.api.main import app

    monkeypatch.setattr(
        "web.api.routers.stream._price_event_generator", _one_price_event
    )
    monkeypatch.setattr(
        "web.api.routers.stream._positions_event_generator", _one_positions_event
    )
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("BUIBUI_WEB_DEV_NO_AUTH", raising=False)
    app.dependency_overrides.pop(require_token_sse, None)
    app.dependency_overrides.pop(require_token, None)
    yield web_client
    web_client.cookies.clear()


@pytest.mark.parametrize("path", _STREAMS)
def test_stream_refuses_when_api_token_unset(sse_auth: TestClient, path: str) -> None:
    """No API_TOKEN and no dev flag must refuse, not fall open (#986)."""
    assert sse_auth.get(path).status_code == 401


@pytest.mark.parametrize("path", _STREAMS)
def test_stream_dev_flag_opts_into_no_auth(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("BUIBUI_WEB_DEV_NO_AUTH", "1")
    assert sse_auth.get(path).status_code == 200


def test_stream_dev_flag_needs_exact_opt_in(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUIBUI_WEB_DEV_NO_AUTH", "0")
    assert sse_auth.get(_STREAMS[0]).status_code == 401


@pytest.mark.parametrize("path", _STREAMS)
def test_stream_missing_token_returns_401(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("API_TOKEN", "test-secret")
    assert sse_auth.get(path).status_code == 401


@pytest.mark.parametrize("path", _STREAMS)
def test_stream_ignores_query_token(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """A correct ?token= must NOT authenticate: it lands in URLs and logs."""
    monkeypatch.setenv("API_TOKEN", "test-secret")
    assert sse_auth.get(f"{path}?token=test-secret").status_code == 401


@pytest.mark.parametrize("path", _STREAMS)
def test_stream_accepts_bearer_header(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("API_TOKEN", "test-secret")
    ok = sse_auth.get(path, headers={"Authorization": "Bearer test-secret"})
    bad = sse_auth.get(path, headers={"Authorization": "Bearer wrong"})
    assert (ok.status_code, bad.status_code) == (200, 401)


@pytest.mark.parametrize("path", _STREAMS)
def test_stream_accepts_cookie(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setenv("API_TOKEN", "test-secret")
    sse_auth.cookies.set("buibui_sse", "wrong")
    assert sse_auth.get(path).status_code == 401
    sse_auth.cookies.set("buibui_sse", "test-secret")
    assert sse_auth.get(path).status_code == 200


def test_stream_session_sets_scoped_httponly_cookie(
    sse_auth: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI's route: trade the Bearer token for a cookie, then stream with it."""
    monkeypatch.setenv("API_TOKEN", "test-secret")
    assert sse_auth.post("/api/stream/session").status_code in (401, 403)
    resp = sse_auth.post(
        "/api/stream/session", headers={"Authorization": "Bearer test-secret"}
    )
    assert resp.status_code == 204
    set_cookie = resp.headers["set-cookie"].lower()
    for attr in ("buibui_sse=", "httponly", "samesite=strict", "path=/api/stream"):
        assert attr in set_cookie
    assert sse_auth.get(_STREAMS[0]).status_code == 200
