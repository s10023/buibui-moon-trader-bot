"""brief/external — M3 external-context types, contract, and loader."""

from dataclasses import asdict

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
