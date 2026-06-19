"""Configuration for the P3 carry sleeve.

Frozen dataclass composing a ``ForecastConfig`` for the shared honest-cost / vol /
governor constants (mirrors ``combine.CombineConfig`` holding ``sleeve_cfg``).
Carry-specific knobs: the EWMA smoothing-span family, a FIXED a-priori carry scalar
(NOT crypto-fit — the standalone book is governor-normalised, see spec §5.3), the
forecast diversification multiplier, and the expression toggle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from analytics.forecast.config import ForecastConfig


@dataclass(frozen=True)
class CarryConfig:
    sleeve_cfg: ForecastConfig = field(default_factory=ForecastConfig)
    carry_spans: tuple[int, ...] = (1, 5, 20, 60)
    carry_scalar: float = 30.0
    fdm: float = 1.25
    cross_sectional: bool = True

    def __post_init__(self) -> None:
        if not self.carry_spans:
            raise ValueError("carry_spans must be non-empty")
        if any(s < 1 for s in self.carry_spans):
            raise ValueError("carry_spans must all be >= 1")

    @property
    def annualization_days(self) -> float:
        return self.sleeve_cfg.annualization_days

    @property
    def cap(self) -> float:
        return self.sleeve_cfg.cap

    @property
    def vol_span(self) -> int:
        return self.sleeve_cfg.vol_span

    @property
    def vol_target_annual(self) -> float:
        return self.sleeve_cfg.vol_target_annual

    @property
    def fee_pct(self) -> float:
        return self.sleeve_cfg.fee_pct

    @property
    def slippage_pct(self) -> float:
        return self.sleeve_cfg.slippage_pct

    @property
    def gov_window(self) -> int:
        return self.sleeve_cfg.gov_window

    @property
    def g_min(self) -> float:
        return self.sleeve_cfg.g_min

    @property
    def g_max(self) -> float:
        return self.sleeve_cfg.g_max

    @classmethod
    def from_toml(cls, path: Path | str) -> CarryConfig:
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
