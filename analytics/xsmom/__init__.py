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
from analytics.xsmom.live import (
    TargetBook,
    TargetPosition,
    build_target_book,
    next_period_governor,
    next_period_leverage,
    position_deltas,
    reconcile,
    target_book_from_dict,
    target_book_to_dict,
)
from analytics.xsmom.replay import (
    load_daily_dollar_volumes,
    replay_targets,
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
    "TargetBook",
    "TargetPosition",
    "XSBookResult",
    "XSReport",
    "beta_attribution",
    "build_target_book",
    "dollar_adv",
    "equal_weight_market_return",
    "equity_curve",
    "evaluate_xs",
    "evaluate_xs_capacity",
    "load_daily_dollar_volumes",
    "next_period_governor",
    "next_period_leverage",
    "position_deltas",
    "reconcile",
    "replay_targets",
    "replay_xs",
    "replay_xs_capacity",
    "replay_xs_trials",
    "run_xs_backtest",
    "run_xs_with_costs",
    "subperiod_sharpe",
    "target_book_from_dict",
    "target_book_to_dict",
    "turnover_cost_rate",
    "xs_demeaned_forecasts",
    "xs_forecasts",
    "xs_leverage",
]
