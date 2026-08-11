"""Turn the spot-perp divergence into a Carver-convention forecast matrix.

Mirrors ``analytics/forecast/ewmac.py`` structure: vol-normalise, scale, cap,
then combine across spans with an FDM and re-cap. The output feeds
``run_xs_backtest(forecasts=...)`` and ``run_forecast_backtest(forecasts=...)``.

**UN-SHIFTED BY DESIGN.** Both books shift internally — ``xs_leverage`` does
``demeaned.shift(1)`` and ``instrument_returns`` does
``combine_forecasts(...).shift(1)``. Adding a shift here would double-shift and
silently destroy a day of signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# The fast legs of analytics/forecast/config.py:16 _DEFAULT_SPEEDS. EWMAC's
# speeds are fast/slow crossover PAIRS; `x` takes a single smoothing span, so
# the pairs cannot be inherited whole. Taker imbalance is a bounded, no-drift
# quantity — a 256-day EWMA of it is near-constant and would carry no signal.
DEFAULT_SPANS: tuple[int, ...] = (8, 16, 32, 64)

# Fixed a priori, never fitted. A smoothed series divided by its own trailing
# std is roughly unit-variance, so 10.0 lands the average absolute forecast near
# the Carver convention without touching the data.
FORECAST_SCALAR: float = 10.0


def cvd_forecast(x: pd.Series, span: int, vol_span: int, cap: float) -> pd.Series:
    """Vol-normalised, scaled, capped single-span forecast over the divergence."""
    smoothed = x.astype(float).ewm(span=span, adjust=False).mean()
    # Causal scale: .shift(1) mirrors analytics/forecast/vol.py ew_return_vol,
    # so day d's normaliser uses only values through d-1.
    scale = smoothed.ewm(span=vol_span, min_periods=vol_span).std().shift(1)
    normalised = smoothed / scale.where(scale != 0.0)
    out = normalised.replace([np.inf, -np.inf], np.nan) * FORECAST_SCALAR
    return out.clip(lower=-cap, upper=cap)


def cvd_combined_forecast(
    x: pd.Series,
    spans: tuple[int, ...],
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.Series:
    """Equal-weight mean of per-span forecasts x FDM, re-capped to +/-cap."""
    parts = [cvd_forecast(x, span, vol_span, cap) for span in spans]
    stacked = pd.concat(parts, axis=1)
    return (stacked.mean(axis=1) * fdm).clip(lower=-cap, upper=cap)


def cvd_forecast_matrix(
    x_by_symbol: dict[str, pd.Series],
    spans: tuple[int, ...],
    fdm: float,
    vol_span: int,
    cap: float,
) -> pd.DataFrame:
    """Per-symbol combined forecasts on the union daily index.

    Columns = symbols, index = sorted union of all symbols' dates, NaN during
    warm-up. Same contract as ``analytics/xsmom/book.py:27 xs_forecasts``,
    because it feeds the same socket. NaN warm-up is intentional: the
    cross-sectional demean skips NaN, so only warmed-up instruments contribute.
    """
    union = pd.DatetimeIndex([])
    for s in x_by_symbol.values():
        union = union.union(pd.DatetimeIndex(s.index))
    union = union.sort_values()
    cols = {
        sym: cvd_combined_forecast(x, spans, fdm, vol_span, cap).reindex(union)
        for sym, x in x_by_symbol.items()
    }
    return pd.DataFrame(cols, index=union)
