"""Tests for the live-outcomes web endpoint (GET /api/live-outcomes).

Self-contained client setup (not the shared ``web_client`` fixture): the fixture
patches ``web.api.main.duckdb.connect`` globally, which would clobber the real
``duckdb.connect`` used to build the in-memory seed connection. We create the
seed conn first, then enter the patched TestClient context.
"""

import time
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from analytics.data_store import init_schema

_NOW_MS = int(time.time() * 1000)


def _client_for(
    conn: duckdb.DuckDBPyConnection,
    binance: MagicMock | None = None,
) -> Generator[TestClient]:
    """Yield a TestClient whose get_db returns ``conn`` and get_client ``binance``."""
    from web.api.deps import get_client, get_db, require_token
    from web.api.main import app

    stub = binance if binance is not None else MagicMock()
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[get_client] = lambda: stub
    app.dependency_overrides[require_token] = lambda: None
    with (
        patch("web.api.main.duckdb.connect", return_value=MagicMock()),
        patch("web.api.main.create_client", return_value=MagicMock()),
        patch("web.api.main.init_schema"),
        TestClient(app) as client,
    ):
        yield client
    app.dependency_overrides.clear()


def _seed_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO signal_alert_outcomes
            (signal_id, symbol, tf, strategy, direction, fired_at_ms,
             entry_price, sl_price, tp_price, rr_ratio, outcome, outcome_r)
        VALUES
            ('a','BTCUSDT','1h','bos','short',?,100,95,105,1.0,'win',1.5),
            ('b','BTCUSDT','1h','bos','short',?,100,95,105,1.0,'loss',-1.0),
            ('c','ETHUSDT','15m','ema','long',?,100,95,NULL,1.0,NULL,NULL)
        """,
        (_NOW_MS, _NOW_MS, _NOW_MS),
    )
    return conn


def test_live_outcomes_endpoint() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes?days=0&min_n=1")
        assert resp.status_code == 200
        data = resp.json()

        assert data["rollup"]["total_rows"] == 3
        assert data["rollup"]["resolved"] == 2
        assert data["rollup"]["open"] == 1
        assert data["rollup"]["open_no_tp"] == 1

        cells = data["cells"]
        assert len(cells) == 1
        assert cells[0]["strategy"] == "bos"
        assert cells[0]["win_rate"] == 0.5

        assert [s["strategy"] for s in data["by_strategy"]] == ["bos"]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_empty() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rollup"]["total_rows"] == 0
        assert data["cells"] == []
        assert data["by_strategy"] == []
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_includes_symbol_chip_list() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        data = client.get("/api/live-outcomes?days=0&min_n=1").json()
        # Seed: 2 BTCUSDT rows, 1 ETHUSDT row — ordered by count desc.
        assert [(s["symbol"], s["n"]) for s in data["symbols"]] == [
            ("BTCUSDT", 2),
            ("ETHUSDT", 1),
        ]
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_live_outcomes_symbol_param_slices() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        data = client.get("/api/live-outcomes?days=0&min_n=1&symbol=ETHUSDT").json()
        assert data["rollup"]["total_rows"] == 1
        assert data["rollup"]["open"] == 1
        # Chips stay global even under the filter.
        assert len(data["symbols"]) == 2
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_marks_positions() -> None:
    conn = _seed_conn()
    binance = MagicMock()
    binance.futures_mark_price.return_value = [
        {"symbol": "ETHUSDT", "markPrice": "105.0"},
        {"symbol": "BTCUSDT", "markPrice": "64000.0"},
    ]
    client_gen = _client_for(conn, binance)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open")
        assert resp.status_code == 200
        data = resp.json()

        assert data["marks_ok"] is True
        assert data["marked_at_ms"] > 0
        # Seed row 'c' is the only open one: ETHUSDT long, entry 100, sl 95.
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["symbol"] == "ETHUSDT"
        assert pos["mark"] == 105.0
        # risk = 5, gain = 5 → +1R
        assert abs(pos["unrealized_r"] - 1.0) < 1e-9
        # tp_price is NULL on the seed row.
        assert pos["dist_tp_pct"] is None
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_survives_price_failure() -> None:
    conn = _seed_conn()
    binance = MagicMock()
    binance.futures_mark_price.side_effect = RuntimeError("binance unreachable")
    client_gen = _client_for(conn, binance)
    client = next(client_gen)
    try:
        resp = client.get("/api/live-outcomes/open")
        # Never 5xx because a price feed is down.
        assert resp.status_code == 200
        data = resp.json()

        assert data["marks_ok"] is False
        # Ledger rows still come back, price columns null.
        assert len(data["positions"]) == 1
        assert data["positions"][0]["mark"] is None
        assert data["positions"][0]["unrealized_r"] is None
        assert data["positions"][0]["entry_price"] == 100.0
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)


def test_open_endpoint_symbol_filter() -> None:
    conn = _seed_conn()
    client_gen = _client_for(conn)
    client = next(client_gen)
    try:
        data = client.get("/api/live-outcomes/open?symbol=BTCUSDT").json()
        # The only open seed row is ETHUSDT.
        assert data["positions"] == []
        assert data["symbol"] == "BTCUSDT"
    finally:
        with pytest.raises(StopIteration):
            next(client_gen)
