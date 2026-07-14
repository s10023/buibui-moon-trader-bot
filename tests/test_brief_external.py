"""brief/external — M3 external-context types, contract, and loader."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from analytics.brief.external import load_external_state, validate_snapshot_dict
from analytics.brief.types import (
    ExternalClusterRow,
    ExternalSnapshot,
    ExternalState,
    error_panel,
)

HOUR_MS = 3_600_000


def _row(dist_atr: float = 1.8) -> ExternalClusterRow:
    return ExternalClusterRow(
        price_lo=65_800.0,
        price_hi=66_200.0,
        kind="liq",
        intensity="high",
        label="long-liq shelf",
        dist_atr=dist_atr,
    )


def test_external_types_serialise() -> None:
    snap = ExternalSnapshot(
        source="coinglass",
        panel="liq_heatmap",
        window="24h",
        scope="pair",
        captured_at_ms=1_000,
        age_hours=14.0,
        spot_price_hint=63_250.0,
        spot_hint_deviation=False,
        clusters_above=[_row()],
        clusters_below=[],
    )
    data = asdict(ExternalState(snapshots=[snap]))
    assert data["snapshots"][0]["clusters_above"][0]["price_lo"] == 65_800.0
    assert data["snapshots"][0]["window"] == "24h"


def test_symbol_panel_external_defaults_none() -> None:
    assert error_panel("BTCUSDT", "boom").external is None


AS_OF = 1_789_400_000_000


def _valid_snapshot(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "external-levels-v1",
        "source": "coinglass",
        "symbol": "BTCUSDT",
        "panel": "liq_heatmap",
        "window": "24h",
        "scope": "pair",
        "captured_at_ms": AS_OF - 14 * HOUR_MS,
        "ingested_at_ms": AS_OF - 13 * HOUR_MS,
        "verified": True,
        "spot_price_hint": 63_250.0,
        "clusters": [
            {
                "price_lo": 65_800.0,
                "price_hi": 66_200.0,
                "kind": "liq",
                "intensity": "high",
                "label": "",
            },
            {
                "price_lo": 61_200.0,
                "price_hi": 61_500.0,
                "kind": "liq",
                "intensity": "med",
                "label": "100x-heavy",
            },
        ],
        "notes": "",
    }
    data.update(overrides)
    return data


def test_validate_accepts_contract() -> None:
    assert validate_snapshot_dict(_valid_snapshot()) == []
    assert validate_snapshot_dict(_valid_snapshot(window=None, scope=None)) == []
    assert validate_snapshot_dict(_valid_snapshot(spot_price_hint=None)) == []


def test_validate_rejects_bad_shapes() -> None:
    assert validate_snapshot_dict("nope") != []
    assert validate_snapshot_dict(_valid_snapshot(extra_key=1)) != []
    assert validate_snapshot_dict(_valid_snapshot(schema="external-levels-v2")) != []
    assert validate_snapshot_dict(_valid_snapshot(panel="oi_chart")) != []
    assert validate_snapshot_dict(_valid_snapshot(scope="global")) != []
    assert validate_snapshot_dict(_valid_snapshot(verified=False)) != []
    assert validate_snapshot_dict(_valid_snapshot(clusters=[])) != []
    bad = _valid_snapshot()
    bad["clusters"][0]["price_lo"] = 99_999.0  # lo > hi
    assert validate_snapshot_dict(bad) != []
    missing = _valid_snapshot()
    del missing["window"]
    assert validate_snapshot_dict(missing) != []


def _load(
    tmp_path: Path,
    *,
    ref: float = 63_000.0,
    atr: float = 500.0,
    max_age: float = 48.0,
    rows: int = 3,
) -> tuple[object, list[str]]:
    return load_external_state(
        dir_path=tmp_path,
        symbol="BTCUSDT",
        ref_close=ref,
        atr14=atr,
        as_of_ms=AS_OF,
        allowed_sources=("coinglass", "mmt"),
        max_age_hours=max_age,
        max_rows_per_side=rows,
    )


def _write(tmp_path: Path, name: str, data: dict[str, Any]) -> None:
    (tmp_path / name).write_text(json.dumps(data))


def test_loader_missing_dir_is_silent(tmp_path: Path) -> None:
    state, notes = _load(tmp_path / "absent")
    assert state is None and notes == []


def test_loader_happy_path_sides_and_distances(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _valid_snapshot())
    state, notes = _load(tmp_path)
    assert notes == []
    assert state is not None
    snap = state.snapshots[0]  # type: ignore[attr-defined]
    assert len(snap.clusters_above) == 1 and len(snap.clusters_below) == 1
    # midpoints: 66,000 -> +6.0 ATR; 61,350 -> -3.3 ATR (atr=500, ref=63,000)
    assert snap.clusters_above[0].dist_atr == 6.0
    assert snap.clusters_below[0].dist_atr == -3.3
    assert snap.age_hours == 14.0
    assert snap.spot_hint_deviation is False


def test_loader_latest_per_source_panel_window(tmp_path: Path) -> None:
    _write(tmp_path, "old.json", _valid_snapshot(captured_at_ms=AS_OF - 20 * HOUR_MS))
    _write(tmp_path, "new.json", _valid_snapshot(captured_at_ms=AS_OF - 2 * HOUR_MS))
    _write(
        tmp_path,
        "map.json",
        _valid_snapshot(
            panel="liq_map", window="1d", captured_at_ms=AS_OF - 3 * HOUR_MS
        ),
    )
    state, _ = _load(tmp_path)
    assert state is not None
    snaps = state.snapshots  # type: ignore[attr-defined]
    assert len(snaps) == 2  # heatmap (latest of the two) + map
    heat = [s for s in snaps if s.panel == "liq_heatmap"][0]
    assert heat.age_hours == 2.0


def test_loader_ignores_other_symbols_silently(tmp_path: Path) -> None:
    _write(tmp_path, "eth.json", _valid_snapshot(symbol="ETHUSDT"))
    state, notes = _load(tmp_path)
    assert state is None and notes == []


def test_loader_notes_on_bad_files(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json")
    _write(tmp_path, "invalid.json", _valid_snapshot(panel="oi_chart"))
    _write(tmp_path, "rogue.json", _valid_snapshot(source="hyblock"))
    state, notes = _load(tmp_path)
    assert state is None
    assert any("unreadable" in n for n in notes)
    assert any("invalid" in n for n in notes)
    assert any("unknown source" in n for n in notes)


def test_loader_bad_encoding_is_note_not_exception(tmp_path: Path) -> None:
    (tmp_path / "badenc.json").write_bytes(b"\xff\xfe{ not utf8")
    state, notes = _load(tmp_path)
    assert state is None
    assert any("unreadable" in n for n in notes)


def test_loader_all_stale_note(tmp_path: Path) -> None:
    _write(tmp_path, "old.json", _valid_snapshot(captured_at_ms=AS_OF - 80 * HOUR_MS))
    state, notes = _load(tmp_path)
    assert state is None
    assert any("stale" in n and "re-drop" in n for n in notes)


def test_loader_atr_zero_omits_block(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _valid_snapshot())
    state, notes = _load(tmp_path, atr=0.0)
    assert state is None
    assert any("ATR unavailable" in n for n in notes)


def test_loader_spot_deviation_flag(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _valid_snapshot(spot_price_hint=40_000.0))
    state, notes = _load(tmp_path)
    assert state is not None
    assert state.snapshots[0].spot_hint_deviation is True  # type: ignore[attr-defined]
    assert any("spot hint deviates" in n for n in notes)


def test_loader_caps_rows_nearest_first(tmp_path: Path) -> None:
    clusters = [
        {"price_lo": p, "price_hi": p, "kind": "liq", "intensity": "low", "label": ""}
        for p in (64_000.0, 65_000.0, 66_000.0, 67_000.0, 68_000.0)
    ]
    _write(tmp_path, "a.json", _valid_snapshot(clusters=clusters))
    state, _ = _load(tmp_path, rows=2)
    assert state is not None
    above = state.snapshots[0].clusters_above  # type: ignore[attr-defined]
    assert [r.price_lo for r in above] == [64_000.0, 65_000.0]
