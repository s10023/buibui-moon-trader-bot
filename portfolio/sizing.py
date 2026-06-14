"""Two-layer position sizing (P1 spec §2) — pure math, no I/O.

Layer A: per-trade risk unit = (r_eff × equity) / |entry − stop|.
Layer B: r_eff = r_base × g_vol × g_regime × g_location × g_conviction,
then clipped by concurrent-risk and majors-cluster caps.
"""

from __future__ import annotations

import math
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


def risk_per_unit(entry: float, stop: float) -> float:
    """Per-unit risk in price terms = |entry − stop|."""
    return abs(entry - stop)


def position_size(risk_capital: float, entry: float, stop: float) -> float:
    """Units = risk_capital / |entry − stop| (0.0 when risk is undefined)."""
    rpu = risk_per_unit(entry, stop)
    return risk_capital / rpu if rpu > 0.0 else 0.0


def vol_governor(realized_vol_annual: float, cfg: SizingConfig) -> float:
    """g_vol = clamp(target / realized, [g_vol_min, g_vol_max]).

    Non-finite or non-positive realized vol (cold start) → neutral 1.0.
    """
    if not math.isfinite(realized_vol_annual) or realized_vol_annual <= 0.0:
        return 1.0
    g = cfg.vol_target_annual / realized_vol_annual
    return float(min(max(g, cfg.g_vol_min), cfg.g_vol_max))


def regime_multiplier(regime_label: str | None, cfg: SizingConfig) -> float:
    """high_vol → high_vol_risk_mult (when enabled); everything else → 1.0."""
    if cfg.apply_high_vol_halving and regime_label == "high_vol":
        return cfg.high_vol_risk_mult
    return 1.0


def effective_risk_fraction(
    cfg: SizingConfig,
    *,
    g_vol: float,
    g_regime: float,
    g_location: float = 1.0,
    g_conviction: float = 1.0,
) -> float:
    """r_eff = r_base × g_vol × g_regime × g_location × g_conviction (pre-cap)."""
    return cfg.r_base * g_vol * g_regime * g_location * g_conviction


def cluster_of(symbol: str, cfg: SizingConfig) -> str:
    """Cluster id for a symbol: the joined members for a configured cluster it
    belongs to, else the symbol itself (its own singleton cluster)."""
    for members in cfg.clusters:
        if symbol in members:
            return "|".join(members)
    return symbol


def apply_caps(
    r_eff: float,
    *,
    symbol: str,
    open_risk_total: float,
    open_risk_cluster: float,
    cfg: SizingConfig,
) -> float:
    """Clip r_eff by concurrent-risk + cluster headroom; scale-down-to-fit.

    Returns the admissible r_eff, or 0.0 when the remaining headroom is below
    `skip_floor_frac × r_base` (skip rather than open a dust position).
    """
    headroom_total = max(cfg.r_open_max - open_risk_total, 0.0)
    headroom_cluster = max(cfg.r_cluster_max - open_risk_cluster, 0.0)
    allowed = min(r_eff, headroom_total, headroom_cluster)
    if allowed < cfg.skip_floor_frac * cfg.r_base:
        return 0.0
    return allowed
