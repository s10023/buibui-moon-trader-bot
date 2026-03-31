"""Signal plugin registry for the signal daemon.

Maps strategy name to plugin metadata. Seasonality is excluded —
it produces stats, not actionable entry signals.

`requires_funding`, `requires_secondary`, and `confidence` flags live on
`analytics.indicators_lib.STRATEGY_REGISTRY`; they are not duplicated here.
Confidence is resolved per-TF at dispatch time via STRATEGY_REGISTRY[name].get_confidence(tf).
"""

from collections.abc import Callable
from typing import TypedDict

import pandas as pd

from analytics.indicators_lib import (
    detect_cvd_divergence,
    detect_doji,
    detect_engulfing,
    detect_eqh_eql,
    detect_fib_golden_zone,
    # detect_fibonacci_retracement,  # Legacy — superseded by fib_golden_zone
    detect_funding_extreme,
    detect_fvg,
    detect_hammer_hanging_man,
    detect_inside_bar,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_morning_evening_star,
    detect_orb_breakout,
    detect_order_block,
    detect_ote_entry,
    detect_pin_bar,
    detect_smt_divergence,
    detect_trend_day,
    detect_wick_fills,
)

DetectorFn = Callable[..., pd.DataFrame]


class SignalPlugin(TypedDict):
    detector: DetectorFn
    # NOTE: confidence removed — resolved per-TF via STRATEGY_REGISTRY[name].get_confidence(tf)


SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    "wick_fill": SignalPlugin(
        detector=detect_wick_fills,
    ),
    "marubozu": SignalPlugin(
        detector=detect_marubozu_retest,
    ),
    "orb": SignalPlugin(
        detector=detect_orb_breakout,
    ),
    "liquidity_sweep": SignalPlugin(
        detector=detect_liquidity_sweep,
    ),
    "fvg": SignalPlugin(
        detector=detect_fvg,
    ),
    "bos": SignalPlugin(
        detector=detect_market_structure,
    ),
    "funding_reversion": SignalPlugin(
        detector=detect_funding_extreme,
    ),
    "smt_divergence": SignalPlugin(
        detector=detect_smt_divergence,
    ),
    "eqh_eql": SignalPlugin(
        detector=detect_eqh_eql,
    ),
    "order_block": SignalPlugin(
        detector=detect_order_block,
    ),
    "cvd_divergence": SignalPlugin(
        detector=detect_cvd_divergence,
    ),
    "trend_day": SignalPlugin(
        detector=detect_trend_day,
    ),
    "engulfing": SignalPlugin(
        detector=detect_engulfing,
    ),
    "pin_bar": SignalPlugin(
        detector=detect_pin_bar,
    ),
    "inside_bar": SignalPlugin(
        detector=detect_inside_bar,
    ),
    "hammer_hanging_man": SignalPlugin(
        detector=detect_hammer_hanging_man,
    ),
    "doji": SignalPlugin(
        detector=detect_doji,
    ),
    "morning_evening_star": SignalPlugin(
        detector=detect_morning_evening_star,
    ),
    # Legacy — superseded by fib_golden_zone. Uncomment to re-enable.
    # "fibonacci_retracement": SignalPlugin(
    #     detector=detect_fibonacci_retracement,
    # ),
    "fib_golden_zone": SignalPlugin(
        detector=detect_fib_golden_zone,
    ),
    "ote_entry": SignalPlugin(
        detector=detect_ote_entry,
    ),
}
