"""Configuration for the P3 cross-sectional reversal sleeve.

Frozen dataclass composing a ``ForecastConfig`` for the shared honest-cost / vol /
governor constants (mirrors ``carry.CarryConfig`` holding ``sleeve_cfg``). The
reversal-specific knobs (formation-window family, a-priori forecast scalar, FDM)
are used only to BUILD the forecast matrix; the shared book supplies everything
downstream. All constants are a-priori, NOT crypto-fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from analytics.forecast.config import ForecastConfig


@dataclass(frozen=True)
class ReversalConfig:
    sleeve_cfg: ForecastConfig = field(default_factory=ForecastConfig)
    formation_windows: tuple[int, ...] = (2, 3, 5, 7)
    reversal_scalar: float = 10.0
    fdm: float = 1.25

    def __post_init__(self) -> None:
        if not self.formation_windows:
            raise ValueError("formation_windows must be non-empty")
        if any(w < 1 for w in self.formation_windows):
            raise ValueError("formation_windows must all be >= 1")

    @property
    def vol_span(self) -> int:
        return self.sleeve_cfg.vol_span

    @property
    def cap(self) -> float:
        return self.sleeve_cfg.cap

    @classmethod
    def from_toml(cls, path: Path | str) -> ReversalConfig:
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
