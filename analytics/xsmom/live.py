"""Live daily target-position generation for the XS-solo deploy core.

Pure (no DB I/O). Computes the *next-period* target leverage — the position to
hold during the bar after the last completed close — from the latest causal
forecast and vol, with no backtest position-alignment shift. The reconciliation
helper proves `next_period_leverage(through T) == xs_leverage(through T+1)[T+1]`,
which is both the backtest<->live consistency guarantee and the no-look-ahead
proof.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.forecast.config import ForecastConfig
from analytics.xsmom.book import xs_demeaned_forecasts, xs_leverage


def next_period_leverage(
    closes: dict[str, pd.Series], cfg: ForecastConfig
) -> pd.Series:
    """Per-instrument vol-parity leverage to hold during the next bar.

    `(demeaned[T] / 10) * (vol_target / vol_ann_asof_T)`, where `demeaned[T]` is
    the latest cross-sectionally demeaned forecast and `vol_ann_asof_T` is the
    UNSHIFTED EW return vol at `T` (uses returns through `T`). NaN for
    not-yet-warmed-up / absent instruments. Mirrors `xs_leverage` one step ahead,
    including the optional `xs_dollar_neutral` re-center.
    """
    demeaned = xs_demeaned_forecasts(closes, cfg)
    union = demeaned.index
    latest = demeaned.iloc[-1]
    ann = np.sqrt(cfg.annualization_days)

    out: dict[str, float] = {}
    for sym, close in closes.items():
        raw_std = (
            close.pct_change().ewm(span=cfg.vol_span, min_periods=cfg.vol_span).std()
        )
        vol_ann_t = float(raw_std.reindex(union).iloc[-1]) * ann
        f = float(latest.get(sym, np.nan))
        out[sym] = (f / 10.0) * (cfg.vol_target_annual / vol_ann_t)

    series = pd.Series(out).replace([np.inf, -np.inf], np.nan)
    if cfg.xs_dollar_neutral:
        series = series - series.mean()
    return series


def reconcile(
    closes: dict[str, pd.Series], cfg: ForecastConfig, cutoff: pd.Timestamp
) -> float:
    """Max abs diff between the live target as-of `cutoff` and the research book's
    leverage for the first bar after `cutoff`. ~0 when correct (NaN treated as 0
    on both sides so any active-set mismatch surfaces)."""
    truncated = {
        sym: s[s.index <= cutoff]
        for sym, s in closes.items()
        if len(s[s.index <= cutoff]) > 0
    }
    live = next_period_leverage(truncated, cfg)
    full = xs_leverage(closes, cfg)
    after = full.index[full.index > cutoff]
    if len(after) == 0:
        raise ValueError("cutoff leaves no bar after it")
    book_row = full.loc[after[0]]
    diff = (live.reindex(book_row.index).fillna(0.0) - book_row.fillna(0.0)).abs()
    return float(diff.max())


def next_period_governor(pre_returns: pd.Series, cfg: ForecastConfig) -> float:
    """Causal 20%-vol governor to apply during the next bar.

    `clip(vol_target / (trailing_std_asof_T * sqrt(ann)), g_min, g_max)`, where
    `trailing_std_asof_T` is the UNSHIFTED rolling std of the pre-governor
    portfolio returns at `T`. Cold start (< gov_window history, or degenerate
    vol) returns the neutral 1.0 — matching `portfolio.sizing.vol_governor`.
    """
    ann = np.sqrt(cfg.annualization_days)
    trailing_std = float(
        pre_returns.rolling(cfg.gov_window, min_periods=cfg.gov_window).std().iloc[-1]
    )
    if not np.isfinite(trailing_std) or trailing_std <= 0.0:
        return 1.0
    g = cfg.vol_target_annual / (trailing_std * ann)
    return float(np.clip(g, cfg.g_min, cfg.g_max))
