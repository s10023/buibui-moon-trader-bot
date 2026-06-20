"""Size-aware execution cost model for the cross-sectional momentum sleeve.

Replaces the book's flat per-leg slippage with a per-(instrument, day) rate:

    cost_rate_i(d) = fee_pct + half_spread_i(d) + k * impact(|Δlev_i(d)| * C / ADV_i(d))

`half_spread_i(d)` is an a-priori bps tier keyed by trailing dollar-ADV; the
impact term carries the size-dependence. Pure and causal: ADV is a trailing
median shifted one day, so day-`d` cost uses liquidity through `d-1` only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
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


def turnover_cost_rate(
    leverage: pd.DataFrame,
    adv: dict[str, pd.Series],
    cfg: ExecutionCostConfig,
) -> pd.DataFrame:
    """Per-(instrument, day) turnover cost rate (fraction of capital per |Δlev|).

    `rate = fee + half_spread(ADV-tier) + k * impact(|Δlev| * capital / ADV)`.
    `impact` is `sqrt` (headline) or `linear`. NaN ADV (warm-up) -> NaN rate, so
    those cells drop out of the book's net (same skipna semantics as leverage
    warm-up). inf (zero-ADV) is mapped to NaN for the same reason.
    """
    idx = leverage.index
    adv_df = pd.DataFrame(
        {
            sym: adv.get(sym, pd.Series(np.nan, index=idx)).reindex(idx)
            for sym in leverage.columns
        },
        index=idx,
    )

    # A-priori half-spread tiers (bps -> fraction). NaN ADV falls to the alt
    # default but the impact term below makes the whole rate NaN there anyway.
    conds = [adv_df >= cfg.major_cutoff, adv_df >= cfg.mid_cutoff]
    choices = [cfg.major_bps, cfg.mid_bps]
    half_spread = pd.DataFrame(
        np.select(conds, choices, default=cfg.alt_bps) / 1e4,
        index=idx,
        columns=leverage.columns,
    )

    dlev = (leverage - leverage.shift(1).fillna(0.0)).abs()
    participation = (dlev * cfg.capital / adv_df).replace([np.inf, -np.inf], np.nan)
    if cfg.impact == "sqrt":
        impact = cfg.k * np.sqrt(participation)
    elif cfg.impact == "linear":
        impact = cfg.k * participation
    else:
        raise ValueError(f"unknown impact form: {cfg.impact!r}")

    return cfg.fee_pct + half_spread + impact
