"""EWMAC trend sleeve (P2) — continuous vol-normalised trend forecasts."""

from __future__ import annotations

from analytics.forecast.attribution import (
    RegimeCell,
    attribution_frame,
    book_day_attribution,
    dominant_regime,
    effective_independent_series,
    instrument_day_attribution,
    regime_labels,
)
from analytics.forecast.book import (
    ForecastBookResult,
    equity_curve,
    instrument_returns,
    run_forecast_backtest,
)
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import (
    load_daily_bars,
    load_daily_inputs,
    replay_trials,
    replay_universe,
    replay_weight_schemes,
)
from analytics.forecast.report import G2Report, evaluate

__all__ = [
    "ForecastBookResult",
    "ForecastConfig",
    "G2Report",
    "RegimeCell",
    "attribution_frame",
    "book_day_attribution",
    "dominant_regime",
    "effective_independent_series",
    "equity_curve",
    "evaluate",
    "instrument_day_attribution",
    "instrument_returns",
    "load_daily_bars",
    "load_daily_inputs",
    "regime_labels",
    "replay_trials",
    "replay_universe",
    "replay_weight_schemes",
    "run_forecast_backtest",
]
