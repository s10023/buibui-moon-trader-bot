"""brief/external — M3 external-context types, contract, and loader."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb
import pytest

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.brief.external import load_external_state, validate_snapshot_dict
from analytics.brief.types import (
    ExternalClusterRow,
    ExternalSnapshot,
    ExternalState,
    error_panel,
)
from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

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
        venue=None,
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


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d["clusters"].__setitem__(0, "not-a-dict"), "not an object"),
        (lambda d: d["clusters"][0].pop("kind"), "bad keys"),
        (lambda d: d["clusters"][0].update(price_lo="x"), "prices not numbers"),
        (
            lambda d: d["clusters"][0].update(price_lo=9.0, price_hi=1.0),
            "price_lo > price_hi",
        ),
        (
            lambda d: d["clusters"][0].update(price_lo=-1.0, price_hi=2.0),
            "price_lo <= 0",
        ),
        (lambda d: d["clusters"][0].update(kind="magic"), "kind"),
        (lambda d: d["clusters"][0].update(intensity="nuclear"), "intensity"),
        (lambda d: d["clusters"][0].update(label=7), "label not a string"),
        (lambda d: d.update(source=""), "source"),
        (lambda d: d.update(symbol=7), "symbol"),
        (lambda d: d.update(notes=None), "notes"),
        (lambda d: d.update(spot_price_hint="high"), "spot_price_hint"),
        (lambda d: d.update(verified=False), "verified"),
        (lambda d: d.update(clusters=[]), "clusters"),
    ],
)
def test_validate_snapshot_dict_rejects(mutate, fragment) -> None:  # type: ignore[no-untyped-def]
    data = _valid_snapshot()
    mutate(data)
    problems = validate_snapshot_dict(data)
    assert problems, f"expected a problem for {fragment}"
    assert any(fragment in p for p in problems)


def test_venue_absent_and_null_both_valid_and_unspecified(tmp_path: Path) -> None:
    legacy = _valid_snapshot()  # no venue key at all
    nulled = _valid_snapshot()
    nulled["venue"] = None
    assert validate_snapshot_dict(legacy) == []
    assert validate_snapshot_dict(nulled) == []


def test_venue_must_be_nonempty_string_or_null() -> None:
    for bad in ("", 7, ["binance"]):
        data = _valid_snapshot()
        data["venue"] = bad
        assert any("venue" in p for p in validate_snapshot_dict(data))


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


def test_same_panel_window_different_venue_coexist(tmp_path: Path) -> None:
    a = _valid_snapshot()
    a["venue"] = "binance"
    b = _valid_snapshot()
    b["venue"] = "hyperliquid"
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 2
    assert {s.venue for s in state.snapshots} == {"binance", "hyperliquid"}


def test_same_panel_window_different_scope_coexist(tmp_path: Path) -> None:
    a = _valid_snapshot()
    a["scope"] = "pair"
    b = _valid_snapshot()
    b["scope"] = "agg"
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 2


def test_identical_full_key_still_latest_wins(tmp_path: Path) -> None:
    older = _valid_snapshot()
    newer = _valid_snapshot()
    newer["captured_at_ms"] = older["captured_at_ms"] + 3_600_000
    for d in (older, newer):
        d["venue"] = "binance"
    (tmp_path / "a.json").write_text(json.dumps(older))
    (tmp_path / "b.json").write_text(json.dumps(newer))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 1
    assert state.snapshots[0].captured_at_ms == newer["captured_at_ms"]


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


def test_below_side_cap_keeps_nearest_first(tmp_path: Path) -> None:
    data = _valid_snapshot()
    # 4 bands below ref=100, nearest first should survive the cap of 3.
    data["clusters"] = [
        {
            "price_lo": lo,
            "price_hi": lo + 1.0,
            "kind": "liq",
            "intensity": "low",
            "label": "",
        }
        for lo in (90.0, 80.0, 70.0, 60.0)
    ]
    (tmp_path / "coinglass_liq_map_1d_BTCUSDT.json").write_text(json.dumps(data))
    state, _ = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    below = state.snapshots[0].clusters_below
    assert len(below) == 3
    mids = [(r.price_lo + r.price_hi) / 2 for r in below]
    assert mids == sorted(mids, reverse=True)  # nearest (least deep) first


def test_ref_close_zero_no_deviation_note(tmp_path: Path) -> None:
    data = _valid_snapshot()
    data["spot_price_hint"] = 123.0
    (tmp_path / "coinglass_liq_map_1d_BTCUSDT.json").write_text(json.dumps(data))
    state, notes = load_external_state(
        tmp_path, "BTCUSDT", 0.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert not any("deviates" in n for n in notes)
    assert state.snapshots[0].spot_hint_deviation is False


def test_stale_file_note_suppressed_when_fresh_exists(tmp_path: Path) -> None:
    fresh = _valid_snapshot()
    stale = _valid_snapshot()
    stale["captured_at_ms"] = AS_OF - int(72 * 3_600_000)
    (tmp_path / "a_fresh.json").write_text(json.dumps(fresh))
    (tmp_path / "b_stale.json").write_text(json.dumps(stale))
    state, notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert state is not None
    assert len(state.snapshots) == 1
    # stale sibling skipped pre-dedup; with fresh present, no re-drop note fires
    assert not any("stale" in n for n in notes)


def test_unreadable_directory_entry_becomes_note(tmp_path: Path) -> None:
    (tmp_path / "dir.json").mkdir()  # read_text -> IsADirectoryError (OSError)
    _, notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert any("unreadable dir.json" in n for n in notes)


def test_invalid_file_noted_only_in_its_own_symbols_pass(tmp_path: Path) -> None:
    # bad intensity -> invalid, but symbol is readable: ETHUSDT owns it.
    bad = _valid_snapshot(symbol="ETHUSDT")  # the file's existing builder (line 60)
    bad["clusters"][0]["intensity"] = "nuclear"
    (tmp_path / "coinglass_liq_map_1d_ETHUSDT.json").write_text(json.dumps(bad))

    _, btc_notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    _, eth_notes = load_external_state(
        tmp_path, "ETHUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert btc_notes == []
    assert any("invalid" in n for n in eth_notes)


def test_symbolless_invalid_file_still_noted_everywhere(tmp_path: Path) -> None:
    (tmp_path / "junk.json").write_text('{"schema": "external-levels-v1"}')
    _, btc_notes = load_external_state(
        tmp_path, "BTCUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    _, eth_notes = load_external_state(
        tmp_path, "ETHUSDT", 100.0, 2.0, AS_OF, ("coinglass",), 48.0, 3
    )
    assert any("invalid" in n for n in btc_notes)
    assert any("invalid" in n for n in eth_notes)


BUNDLE_AS_OF = START_MS + 60 * DAY_MS


def _bundle_cfg(tmp_path: Path, external_dir: Path) -> BriefConfig:
    return BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=BUNDLE_AS_OF,
        ledger_path=tmp_path / "absent.jsonl",
        priors_path=tmp_path / "absent.json",
        external_dir=external_dir,
    )


def test_bundle_wires_external_block(tmp_path: Path) -> None:
    conn: duckdb.DuckDBPyConnection = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bare = compute_brief(conn, _bundle_cfg(tmp_path, tmp_path / "missing"))
    panel = bare.panels[0]
    assert panel.error is None and panel.external is None
    ref = panel.ref_close
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    snap = _valid_snapshot(
        captured_at_ms=BUNDLE_AS_OF - HOUR_MS,
        ingested_at_ms=BUNDLE_AS_OF,
        spot_price_hint=ref,
        clusters=[
            {
                "price_lo": ref * 1.04,
                "price_hi": ref * 1.05,
                "kind": "liq",
                "intensity": "high",
                "label": "",
            },
            {
                "price_lo": ref * 0.95,
                "price_hi": ref * 0.96,
                "kind": "liq",
                "intensity": "med",
                "label": "",
            },
        ],
    )
    (ext_dir / "coinglass_liq_heatmap_BTCUSDT.json").write_text(json.dumps(snap))
    wired = compute_brief(conn, _bundle_cfg(tmp_path, ext_dir))
    ext = wired.panels[0].external
    assert ext is not None
    assert len(ext.snapshots) == 1
    assert len(ext.snapshots[0].clusters_above) == 1
    assert len(ext.snapshots[0].clusters_below) == 1
    conn.close()
