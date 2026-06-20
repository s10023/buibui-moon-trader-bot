"""Cross-sectional momentum sleeve (P3) — demeaned EWMAC relative-strength book."""

from __future__ import annotations

from analytics.xsmom.book import (
    XSBookResult,
    equity_curve,
    run_xs_backtest,
    xs_demeaned_forecasts,
    xs_forecasts,
    xs_leverage,
)
from analytics.xsmom.diagnostics import (
    BetaAttribution,
    PersistenceReport,
    beta_attribution,
    equal_weight_market_return,
    subperiod_sharpe,
)
from analytics.xsmom.execution import (
    CapacityRun,
    ExecutionCostConfig,
    dollar_adv,
    run_xs_with_costs,
    turnover_cost_rate,
)
from analytics.xsmom.replay import (
    load_daily_dollar_volumes,
    replay_xs,
    replay_xs_capacity,
    replay_xs_trials,
)
from analytics.xsmom.report import XSReport, evaluate_xs, evaluate_xs_capacity

__all__ = [
    "BetaAttribution",
    "CapacityRun",
    "ExecutionCostConfig",
    "PersistenceReport",
    "XSBookResult",
    "XSReport",
    "beta_attribution",
    "dollar_adv",
    "equal_weight_market_return",
    "equity_curve",
    "evaluate_xs",
    "evaluate_xs_capacity",
    "load_daily_dollar_volumes",
    "replay_xs",
    "replay_xs_capacity",
    "replay_xs_trials",
    "run_xs_backtest",
    "run_xs_with_costs",
    "subperiod_sharpe",
    "turnover_cost_rate",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
