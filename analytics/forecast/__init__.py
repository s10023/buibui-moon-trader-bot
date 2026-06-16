"""EWMAC trend sleeve (P2) — continuous vol-normalised trend forecasts."""

from __future__ import annotations

from analytics.forecast.book import (
    ForecastBookResult,
    equity_curve,
    instrument_returns,
    run_forecast_backtest,
)
from analytics.forecast.config import ForecastConfig
from analytics.forecast.replay import (
    load_daily_inputs,
    replay_trials,
    replay_universe,
)
from analytics.forecast.report import G2Report, evaluate

__all__ = [
    "ForecastBookResult",
    "ForecastConfig",
    "G2Report",
    "equity_curve",
    "evaluate",
    "instrument_returns",
    "load_daily_inputs",
    "replay_trials",
    "replay_universe",
    "run_forecast_backtest",
]
