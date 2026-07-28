"""Reversal book: build the reversal forecast, run the shared XS book.

The demean, vol-parity leverage, honest-cost accrual, and 20%-vol governor are all
reused verbatim from ``analytics.xsmom.book.run_xs_backtest`` via its injectable
``forecasts=`` hook — this module only supplies the reversal forecast matrix and
the sleeve's shared ``ForecastConfig`` (``cfg.sleeve_cfg``) for the sizing/cost
constants. Returns the reused ``XSBookResult``.
"""

from __future__ import annotations

import pandas as pd

from analytics.xsmom.book import XSBookResult, equity_curve, run_xs_backtest
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import reversal_forecast_matrix

__all__ = ["XSBookResult", "equity_curve", "run_xsrev_backtest"]


def run_xsrev_backtest(
    closes: dict[str, pd.Series],
    fundings: dict[str, pd.Series],
    cfg: ReversalConfig,
    *,
    turnover_cost_rate: pd.DataFrame | None = None,
) -> XSBookResult:
    """Causal dollar-neutral long-short reversal book (reuses the XS book)."""
    forecasts = reversal_forecast_matrix(closes, cfg)
    return run_xs_backtest(
        closes,
        fundings,
        cfg.sleeve_cfg,
        turnover_cost_rate=turnover_cost_rate,
        forecasts=forecasts,
    )
