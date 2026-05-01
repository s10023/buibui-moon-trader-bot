"""Compatibility shim — re-exports from `analytics.strategies`.

In strat-2 every detector, every shared helper, and every registry symbol moved
into the `analytics/strategies/` package. This module remains as a re-export
shim so the 30+ external import sites (web routers, signal_lib, backtest_lib,
runners, scripts, tests) keep working without edits.

The PEP 484 explicit re-export form (`X as X`) tells both ruff (F401) and mypy
(`implicit-reexport`) that these names are part of this module's public API.
"""

from analytics.strategies import DETECTOR_REGISTRY as DETECTOR_REGISTRY
from analytics.strategies import INCOMPATIBLE_PAIRS as INCOMPATIBLE_PAIRS
from analytics.strategies import KNOWN_STRATEGIES as KNOWN_STRATEGIES
from analytics.strategies import KNOWN_STRATEGY_TYPES as KNOWN_STRATEGY_TYPES
from analytics.strategies import SEASONALITY_COLUMNS as SEASONALITY_COLUMNS
from analytics.strategies import SIGNAL_COLUMNS as SIGNAL_COLUMNS
from analytics.strategies import STRATEGY_REGISTRY as STRATEGY_REGISTRY
from analytics.strategies import STRATEGY_TYPE_GROUPS as STRATEGY_TYPE_GROUPS
from analytics.strategies import ParamSpec as ParamSpec
from analytics.strategies import StrategySpec as StrategySpec
from analytics.strategies import _empty_signals as _empty_signals
from analytics.strategies import _find_bos_swing as _find_bos_swing
from analytics.strategies import _fmt_time as _fmt_time
from analytics.strategies import _signals_to_df as _signals_to_df
from analytics.strategies import detect_cvd_divergence as detect_cvd_divergence
from analytics.strategies import detect_doji as detect_doji
from analytics.strategies import detect_engulfing as detect_engulfing
from analytics.strategies import detect_eqh_eql as detect_eqh_eql
from analytics.strategies import detect_fib_golden_zone as detect_fib_golden_zone
from analytics.strategies import (
    detect_fibonacci_retracement as detect_fibonacci_retracement,
)
from analytics.strategies import detect_funding_extreme as detect_funding_extreme
from analytics.strategies import detect_fvg as detect_fvg
from analytics.strategies import detect_hammer_hanging_man as detect_hammer_hanging_man
from analytics.strategies import detect_inside_bar as detect_inside_bar
from analytics.strategies import detect_liquidity_sweep as detect_liquidity_sweep
from analytics.strategies import detect_market_structure as detect_market_structure
from analytics.strategies import detect_marubozu_retest as detect_marubozu_retest
from analytics.strategies import (
    detect_morning_evening_star as detect_morning_evening_star,
)
from analytics.strategies import detect_orb_breakout as detect_orb_breakout
from analytics.strategies import detect_order_block as detect_order_block
from analytics.strategies import detect_ote_entry as detect_ote_entry
from analytics.strategies import detect_pin_bar as detect_pin_bar
from analytics.strategies import detect_smt_divergence as detect_smt_divergence
from analytics.strategies import detect_trend_day as detect_trend_day
from analytics.strategies import detect_wick_fills as detect_wick_fills
from analytics.strategies import patch_confidence_scores as patch_confidence_scores
from analytics.strategies import seasonality_stats as seasonality_stats
from analytics.strategies import volume_confirm as volume_confirm
