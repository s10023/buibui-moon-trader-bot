"""P3 carry sleeve — funding-carry as a vol-scaled forecast (read-only, default-off).

Carver-style carry: the perp funding rate is the cost-of-carry, expressed as a
vol-scaled forecast (long when funding pays you to be long). Built BOTH absolute and
cross-sectional; the headline is cross-sectional. Pure, read-only over ``analytics.db``,
additive — no schema/golden change.
"""

from analytics.carry.book import (
    CarryBookResult,
    carry_forecast_matrix,
    carry_leverage,
    equity_curve,
    run_carry_backtest,
)
from analytics.carry.config import CarryConfig
from analytics.carry.forecast import (
    annualized_funding,
    combine_carry_forecasts,
    scaled_carry_forecast,
)
from analytics.carry.replay import replay_carry, replay_carry_trials
from analytics.carry.report import CarryReport, carry_gate_verdict, evaluate_carry

__all__ = [
    "CarryBookResult",
    "CarryConfig",
    "CarryReport",
    "annualized_funding",
    "carry_forecast_matrix",
    "carry_gate_verdict",
    "carry_leverage",
    "combine_carry_forecasts",
    "equity_curve",
    "evaluate_carry",
    "replay_carry",
    "replay_carry_trials",
    "run_carry_backtest",
    "scaled_carry_forecast",
]
