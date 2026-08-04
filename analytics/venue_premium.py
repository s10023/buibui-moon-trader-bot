"""H14 Coinbase-premium market-state tag — pure library.

Spec: docs/superpowers/specs/2026-08-04-h14-coinbase-premium-state-tag-design.md

PURE: no DB, no network, no file IO. Every threshold here is a-priori and was
written down in the spec before any avg_r was computed.

Task 3 scope (this file): the three premium series, the causal z-score, and
the two a-priori state-label axes (level, change). Task 4 layers the
day-level collapse, the one-day entry lag (spec Sec.5 / amendments.md A2), and
the pre-committed BUILD/AVOID/NO-EDGE gate (amendments.md A1/A3) on top of
these primitives — nothing in this file performs that join or that gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Z_WINDOW = 90
Z_THRESHOLD = 1.0
CHANGE_SPAN = 5

LEVEL_ELEVATED = "elevated"
LEVEL_NEUTRAL = "neutral"
LEVEL_DEPRESSED = "depressed"
CHANGE_RISING = "rising"
CHANGE_FALLING = "falling"


def build_premium_series(
    cb_btc: pd.Series, bn_btc: pd.Series, cb_usdt: pd.Series
) -> pd.DataFrame:
    """The three pre-registered series, aligned on their shared index.

    ``prem_adj`` divides out the USDT peg, which the 2026-08-04 probe measured
    as accounting for essentially the whole raw premium at that instant.
    """
    idx = cb_btc.index.intersection(bn_btc.index)
    cb, bn = cb_btc.reindex(idx), bn_btc.reindex(idx)
    usdt = cb_usdt.reindex(idx)
    return pd.DataFrame(
        {
            "prem_raw": cb / bn - 1.0,
            "prem_adj": cb / (bn * usdt) - 1.0,
            "peg_dev": usdt - 1.0,
        },
        index=idx,
    )


def causal_zscore(series: pd.Series, window: int = Z_WINDOW) -> pd.Series:
    """z of today's value against the ``window`` days STRICTLY before it.

    The ``.shift(1)`` is the causality guarantee and is asserted by a
    perturbation test — do not remove it as a "warm-up" convenience.
    """
    prior = series.shift(1)
    mean = prior.rolling(window, min_periods=window).mean()
    std = prior.rolling(window, min_periods=window).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def label_levels(z: pd.Series) -> pd.Series:
    """Pre-registered +/-1.0 thresholds. NaN (warm-up) stays NaN, never 'neutral'."""
    out: pd.Series = pd.Series(
        np.where(z >= Z_THRESHOLD, LEVEL_ELEVATED, LEVEL_NEUTRAL), index=z.index
    )
    out = out.where(z > -Z_THRESHOLD, LEVEL_DEPRESSED)
    return out.where(z.notna(), other=np.nan)


def label_changes(series: pd.Series, span: int = CHANGE_SPAN) -> pd.Series:
    """Sign of the ``span``-day change in the SMOOTHED premium.

    Spec Sec.5 says "sign of the 5-day change in the smoothed premium" but never
    defines the smoothing. Per amendments.md A4 (controller ruling): a
    ``span``-length rolling mean applied before the diff — deterministic,
    a-priori, and adds no free parameter beyond the already-registered
    ``CHANGE_SPAN``. NaN warm-up preserved (both the rolling mean's own
    warm-up and the subsequent diff's).
    """
    smoothed = series.rolling(span, min_periods=span).mean()
    delta = smoothed.diff(span)
    out: pd.Series = pd.Series(
        np.where(delta >= 0.0, CHANGE_RISING, CHANGE_FALLING), index=series.index
    )
    return out.where(delta.notna(), other=np.nan)
