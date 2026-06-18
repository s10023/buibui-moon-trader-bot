"""Configuration for the trend×XS combine layer (P3).

Frozen dataclass holding one shared `ForecastConfig` (feeds BOTH sleeves so fees /
speeds / governor constants match) plus the combine-specific knobs. `from_toml`
defers to `ForecastConfig.from_toml` for the shared honest-cost values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from analytics.forecast.config import ForecastConfig

_VALID_IDM_MODES = ("causal", "static")


@dataclass(frozen=True)
class CombineConfig:
    sleeve_cfg: ForecastConfig = field(default_factory=ForecastConfig)
    w_xs: float = 0.5
    w_trend: float = 0.5
    idm_mode: str = "causal"
    idm_window: int = 365
    idm_min_periods: int = 120
    idm_cap: float = 2.5
    apply_governor: bool = True

    def __post_init__(self) -> None:
        if self.idm_mode not in _VALID_IDM_MODES:
            raise ValueError(f"idm_mode {self.idm_mode!r} not in {_VALID_IDM_MODES}")

    @classmethod
    def from_toml(cls, path: Path | str) -> CombineConfig:
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
