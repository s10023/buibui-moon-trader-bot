"""Configuration for the EWMAC trend sleeve (P2).

Frozen dataclass + a `from_toml` that picks up the shared honest-cost values
from the `[backtest]` block (`fee_pct`, `slippage_bps`). All other knobs are
Carver-standard constants and rarely change.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# (fast_span, slow_span, forecast_scalar) — pysystemtrade EWMAC scalars,
# derived from broad futures data (NOT crypto-fit) so they carry no look-ahead.
_DEFAULT_SPEEDS: tuple[tuple[int, int, float], ...] = (
    (8, 32, 5.3),
    (16, 64, 3.75),
    (32, 128, 2.65),
    (64, 256, 1.91),
)


@dataclass(frozen=True)
class ForecastConfig:
    speeds: tuple[tuple[int, int, float], ...] = _DEFAULT_SPEEDS
    vol_span: int = 32
    fdm: float = 1.25
    cap: float = 20.0
    vol_target_annual: float = 0.20
    fee_pct: float = 0.0005
    slippage_pct: float = 0.0002
    gov_window: int = 64
    g_min: float = 0.5
    g_max: float = 1.5
    annualization_days: float = 365.0

    @property
    def min_history(self) -> int:
        """Bars of warm-up an instrument needs before it can be sized."""
        longest_slow = max(slow for _, slow, _ in self.speeds)
        return longest_slow + self.vol_span

    @classmethod
    def from_toml(cls, path: Path | str) -> ForecastConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        bt = data.get("backtest", {})
        fee = float(bt.get("fee_pct", 0.0005))
        slip_bps = float(bt.get("slippage_bps", 2.0))
        return cls(fee_pct=fee, slippage_pct=slip_bps / 10_000.0)
