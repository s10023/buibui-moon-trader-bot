"""brief/external — M3 external-context types, contract, and loader."""

from dataclasses import asdict
from typing import Any

from analytics.brief.external import validate_snapshot_dict
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
