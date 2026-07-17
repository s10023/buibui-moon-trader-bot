"""Brief router: happy path, param validation, dependency overrides."""

import json
from pathlib import Path

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._brief_fixtures import START_MS, make_conn, seed_symbol
from web.api.deps import get_db, require_token
from web.api.routers import brief as brief_router

AS_OF_ISO = "2024-03-01T00:00:00Z"
AS_OF_MS = 1_709_251_200_000  # == AS_OF_ISO
_H1 = 3_600_000


def _client(conn: duckdb.DuckDBPyConnection) -> TestClient:
    app = FastAPI()
    app.include_router(brief_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_get_brief_happy_path() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["panels"][0]["symbol"] == "BTCUSDT"
    assert body["panels"][0]["error"] is None
    assert body["pundit"]["priors_status"] in ("ok", "absent", "unreadable")
    assert isinstance(body["health"]["data_ok"], bool)


def test_get_brief_invalid_as_of_400() -> None:
    conn = make_conn()
    client = _client(conn)
    res = client.get("/api/brief", params={"as_of": "not-a-date"})
    assert res.status_code == 400


def test_get_brief_error_panel_embedded() -> None:
    conn = make_conn()
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "NODATAUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    assert res.json()["panels"][0]["error"] is not None


def test_get_brief_panel_includes_indicators() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    panel = res.json()["panels"][0]
    assert panel["indicators"] is not None
    # All 8 sub-models serialize (present even when a block is None).
    assert set(panel["indicators"]) == {
        "ema",
        "range_state",
        "monday",
        "candles",
        "pa",
        "bb",
        "vwap",
        "profile",
    }
    assert panel["indicators"]["ema"]["above_20"] is not None
    assert panel["indicators"]["profile"]["vs_value"] in (
        "above",
        "inside",
        "below",
    )


def test_get_brief_carries_sessions() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    body = res.json()
    clock = body["session_clock"]
    assert clock["label"] == "Asia"
    assert set(clock) == {
        "label",
        "start_ms",
        "end_ms",
        "is_overlap",
        "next_label",
        "next_start_ms",
    }
    sessions = body["panels"][0]["sessions"]
    assert sessions is not None
    assert [r["session"] for r in sessions["recap"]] == ["Asia", "London", "NY"]
    assert len(sessions["tendency"]) == 3


def test_get_brief_panel_serializes_external_key() -> None:
    # No external-context snapshots are seeded for this fixture DB, so the
    # field resolves to null — this asserts the key round-trips through the
    # Pydantic response model at all (SymbolPanelModel must declare it or
    # BriefResponse(**bundle_to_dict(bundle)) silently drops it).
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    panel = res.json()["panels"][0]
    assert "external" in panel
    assert panel["external"] is None


def test_get_brief_external_populated_path_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Router uses the DEFAULT relative external_dir -> chdir into a temp
    # tree with one fresh snapshot; nested external models must round-trip.
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    ext_dir = tmp_path / "docs" / "plans" / "external-context"
    ext_dir.mkdir(parents=True)
    snapshot = {
        "schema": "external-levels-v1",
        "source": "coinglass",
        "symbol": "BTCUSDT",
        "panel": "liq_map",
        "window": "1d",
        "scope": "pair",
        "captured_at_ms": AS_OF_MS - 14 * _H1,
        "ingested_at_ms": AS_OF_MS - 13 * _H1,
        "verified": True,
        "spot_price_hint": None,
        "clusters": [
            {
                "price_lo": 1.0,
                "price_hi": 2.0,
                "kind": "liq",
                "intensity": "low",
                "label": "",
            },
            {
                "price_lo": 900_000.0,
                "price_hi": 1_000_000.0,
                "kind": "liq",
                "intensity": "high",
                "label": "magnet",
            },
        ],
        "notes": "",
    }
    (ext_dir / "coinglass_liq_map_1d_BTCUSDT.json").write_text(json.dumps(snapshot))
    monkeypatch.chdir(tmp_path)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    ext = res.json()["panels"][0]["external"]
    assert ext is not None
    assert len(ext["snapshots"]) == 1
    snap = ext["snapshots"][0]
    assert snap["source"] == "coinglass"
    assert "venue" in snap
    assert snap["venue"] is None
    assert snap["panel"] == "liq_map"
    assert snap["window"] == "1d"
    assert snap["scope"] == "pair"
    assert snap["captured_at_ms"] == AS_OF_MS - 14 * _H1
    assert round(snap["age_hours"]) == 14
    assert snap["spot_price_hint"] is None
    assert snap["spot_hint_deviation"] is False
    # seeded closes sit far inside (2, 900k): one cluster per side.
    assert len(snap["clusters_above"]) == 1
    assert len(snap["clusters_below"]) == 1
    row = snap["clusters_above"][0]
    assert set(row) == {
        "price_lo",
        "price_hi",
        "kind",
        "intensity",
        "label",
        "dist_atr",
    }
    assert row["label"] == "magnet"
    assert row["dist_atr"] > 0
