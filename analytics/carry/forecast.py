"""Funding-carry forecast math — annualised funding, vol-adjust, scale, combine.

Pure functions over ``(price, daily-funding)`` Series. No DB, no IO. Carver-style
carry: the expected carry return (``-annualised funding`` for a perp long) risk-
adjusted by annualised price-return vol, scaled to a Carver-magnitude forecast and
capped. Funding is the perp's basis, so this is the literal carry signal. Causal
throughout (EWMA and ``ew_return_vol`` use only data through each day).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.vol import ew_return_vol


def annualized_funding(
    funding_daily: pd.Series, span: int, ann_days: float
) -> pd.Series:
    """EWMA-smoothed daily funding, annualised.

    ``funding_daily`` is the day's summed funding (~3 8-h rows already summed by
    ``load_daily_inputs``), so annualisation is ``* ann_days`` — the 3/day is already
    inside the sum. Span=1 (``adjust=False``) returns the latest value unchanged.
    """
    smoothed = funding_daily.ewm(span=span, adjust=False).mean()
    return smoothed * ann_days


def scaled_carry_forecast(
    close: pd.Series,
    funding_daily: pd.Series,
    span: int,
    scalar: float,
    vol_span: int,
    cap: float,
    ann_days: float,
) -> pd.Series:
    """Vol-adjusted, scalar-adjusted, capped single-span carry forecast.

    ``carry_adj = (-annualised_funding) / annualised_return_vol`` (long when funding is
    negative — it pays you to hold long); ``forecast = (carry_adj * scalar).clip(+/-cap)``.
    """
    ann_f = annualized_funding(funding_daily, span, ann_days)
    vol_ann = ew_return_vol(close, vol_span).mul(np.sqrt(ann_days)).reindex(ann_f.index)
    carry_adj = (-ann_f) / vol_ann
    carry_adj = carry_adj.replace([np.inf, -np.inf], np.nan)
    return (carry_adj * scalar).clip(lower=-cap, upper=cap)


def combine_carry_forecasts(
    close: pd.Series,
    funding_daily: pd.Series,
    spans: tuple[int, ...],
    scalar: float,
    fdm: float,
    vol_span: int,
    cap: float,
    ann_days: float,
) -> pd.Series:
    """Equal-weight mean of per-span carry forecasts x FDM, re-capped +/-cap.

    Mirrors ``analytics.forecast.ewmac.combine_forecasts`` (equal-weight branch).
    """
    parts = [
        scaled_carry_forecast(close, funding_daily, s, scalar, vol_span, cap, ann_days)
        for s in spans
    ]
    stacked = pd.concat(parts, axis=1)
    mean = stacked.mean(axis=1)
    return (mean * fdm).clip(lower=-cap, upper=cap)
