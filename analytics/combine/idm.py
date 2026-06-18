"""Carver Instrument Diversification Multiplier for the two-sleeve combine.

Pure math, no DB/IO. IDM = 1/√(wᵀ ρ w) scales a diversified combination back up to
the vol target; capped (Carver uses 2.5). `static_idm` uses one full-sample
correlation (a reported sensitivity — mild look-ahead); `causal_idm_series`
estimates the correlation on a trailing window through `d-1` (the headline,
no-look-ahead path).
"""

from __future__ import annotations

import math


def idm_value(w_xs: float, w_trend: float, corr: float, cap: float) -> float:
    """1/√(wᵀρw) for two sleeves, capped at `cap`.

    `var = w_xs² + w_trend² + 2·w_xs·w_trend·corr`. A non-positive `var` (e.g.
    corr ≈ −1 with equal weights) means infinite scale-up — return `cap`.
    """
    var = w_xs**2 + w_trend**2 + 2.0 * w_xs * w_trend * corr
    if var <= 0.0:
        return cap
    return min(math.sqrt(1.0 / var), cap)
