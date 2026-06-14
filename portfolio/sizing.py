"""Two-layer position sizing (P1 spec §2) — pure math, no I/O.

Layer A: per-trade risk unit = (r_eff × equity) / |entry − stop|.
Layer B: r_eff = r_base × g_vol × g_regime × g_location × g_conviction,
then clipped by concurrent-risk and majors-cluster caps.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@dataclass(frozen=True)
class SizingConfig:
    capital: float = 10_000.0
    r_base: float = 0.0025
    vol_target_annual: float = 0.20
    vol_window_days: int = 30
    g_vol_min: float = 0.5
    g_vol_max: float = 1.5
    r_open_max: float = 0.02
    r_cluster_max: float = 0.01
    high_vol_risk_mult: float = 0.5
    apply_high_vol_halving: bool = True
    skip_floor_frac: float = 0.1
    annualization_days: float = 365.0
    clusters: tuple[tuple[str, ...], ...] = (_MAJORS,)

    @classmethod
    def from_toml(cls, path: str | Path) -> SizingConfig:
        """Build a config from a TOML file's optional `[portfolio]` table.

        Missing keys keep dataclass defaults; `clusters` accepts a TOML array
        of arrays. An absent `[portfolio]` block yields plain defaults.
        """
        with open(Path(path), "rb") as f:
            data: dict[str, Any] = tomllib.load(f)
        block = data.get("portfolio", {})
        if not isinstance(block, dict):
            raise ValueError("[portfolio] must be a TOML table")
        cfg = cls()
        kwargs: dict[str, Any] = {}
        for field_name in (
            "capital",
            "r_base",
            "vol_target_annual",
            "vol_window_days",
            "g_vol_min",
            "g_vol_max",
            "r_open_max",
            "r_cluster_max",
            "high_vol_risk_mult",
            "apply_high_vol_halving",
            "skip_floor_frac",
            "annualization_days",
        ):
            if field_name in block:
                kwargs[field_name] = block[field_name]
        if "clusters" in block:
            kwargs["clusters"] = tuple(
                tuple(str(s) for s in c) for c in block["clusters"]
            )
        return replace(cfg, **kwargs)
