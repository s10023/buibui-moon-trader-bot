"""Size-aware execution cost model for the cross-sectional momentum sleeve.

Replaces the book's flat per-leg slippage with a per-(instrument, day) rate:

    cost_rate_i(d) = fee_pct + half_spread_i(d) + k * impact(|Δlev_i(d)| * C / ADV_i(d))

`half_spread_i(d)` is an a-priori bps tier keyed by trailing dollar-ADV; the
impact term carries the size-dependence. Pure and causal: ADV is a trailing
median shifted one day, so day-`d` cost uses liquidity through `d-1` only.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ExecutionCostConfig:
    """A-priori, size-aware turnover-cost parameters (all swept, none fit)."""

    capital: float = 1_000_000.0  # target AUM (USD); the swept axis
    k: float = 0.1  # impact coefficient (dimensionless under sqrt)
    impact: str = "sqrt"  # "sqrt" (headline) or "linear" (robustness)
    adv_window: int = 30  # trailing window (days) for dollar-ADV
    fee_pct: float = 0.0005  # size-independent maker/taker fee (matches ForecastConfig)
    # half-spread tiers (bps) by trailing dollar-ADV (USD) cutoffs
    major_bps: float = 1.0
    mid_bps: float = 3.0
    alt_bps: float = 8.0
    major_cutoff: float = 1_000_000_000.0  # >= $1B ADV -> major tier
    mid_cutoff: float = 100_000_000.0  # >= $100M ADV -> mid tier


def dollar_adv(
    dollar_volumes: dict[str, pd.Series], window: int
) -> dict[str, pd.Series]:
    """Causal trailing-median dollar ADV per instrument.

    `dollar_volumes[sym]` is the per-day dollar volume (`volume * close`),
    day-indexed. Returns the trailing-`window` median shifted one day so the
    value at row `d` uses only days through `d-1` (no same-day leak).
    """
    out: dict[str, pd.Series] = {}
    for sym, dv in dollar_volumes.items():
        out[sym] = dv.rolling(window).median().shift(1)
    return out
