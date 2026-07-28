"""P3 cross-sectional short-horizon reversal sleeve (read-only, default-off).

A sign-flipped short-horizon (2-7 day) cross-sectional return signal, fed through
the validated XS-momentum demean/leverage/cost/governor book via its injectable
`forecasts=` hook. Candidate second strong edge, decorrelated from XS-solo. Pure,
read-only over ``analytics.db``, additive — no schema/golden change.
"""

from analytics.xsmom.book import XSBookResult, equity_curve
from analytics.xsrev.book import run_xsrev_backtest
from analytics.xsrev.config import ReversalConfig
from analytics.xsrev.forecast import (
    combine_reversal_forecasts,
    reversal_forecast_matrix,
    scaled_reversal_forecast,
)
from analytics.xsrev.replay import replay_xsrev, replay_xsrev_trials

__all__ = [
    "ReversalConfig",
    "XSBookResult",
    "combine_reversal_forecasts",
    "equity_curve",
    "reversal_forecast_matrix",
    "replay_xsrev",
    "replay_xsrev_trials",
    "run_xsrev_backtest",
    "scaled_reversal_forecast",
]
