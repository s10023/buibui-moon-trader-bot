"""Signal plugin registry for the signal daemon.

Maps strategy name to plugin metadata. Seasonality is excluded —
it produces stats, not actionable entry signals.

`requires_funding` and `requires_secondary` flags live on
`analytics.indicators_lib.STRATEGY_REGISTRY`; they are not duplicated here.
"""

from collections.abc import Callable
from typing import TypedDict

import pandas as pd

from analytics.indicators_lib import (
    STRATEGY_REGISTRY,
    detect_cvd_divergence,
    detect_doji,
    detect_engulfing,
    detect_eqh_eql,
    detect_fib_golden_zone,
    detect_fibonacci_retracement,
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
    confidence: int  # 1–5; mirrors STRATEGY_REGISTRY[name].confidence


SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    "wick_fill": SignalPlugin(
        detector=detect_wick_fills,
        confidence=STRATEGY_REGISTRY["wick_fill"].confidence,
    ),
    "marubozu": SignalPlugin(
        detector=detect_marubozu_retest,
        confidence=STRATEGY_REGISTRY["marubozu"].confidence,
    ),
    "orb": SignalPlugin(
        detector=detect_orb_breakout,
        confidence=STRATEGY_REGISTRY["orb"].confidence,
    ),
    "liquidity_sweep": SignalPlugin(
        detector=detect_liquidity_sweep,
        confidence=STRATEGY_REGISTRY["liquidity_sweep"].confidence,
    ),
    "fvg": SignalPlugin(
        detector=detect_fvg,
        confidence=STRATEGY_REGISTRY["fvg"].confidence,
    ),
    "bos": SignalPlugin(
        detector=detect_market_structure,
        confidence=STRATEGY_REGISTRY["bos"].confidence,
    ),
    "funding_reversion": SignalPlugin(
        detector=detect_funding_extreme,
        confidence=STRATEGY_REGISTRY["funding_reversion"].confidence,
    ),
    "smt_divergence": SignalPlugin(
        detector=detect_smt_divergence,
        confidence=STRATEGY_REGISTRY["smt_divergence"].confidence,
    ),
    "eqh_eql": SignalPlugin(
        detector=detect_eqh_eql,
        confidence=STRATEGY_REGISTRY["eqh_eql"].confidence,
    ),
    "order_block": SignalPlugin(
        detector=detect_order_block,
        confidence=STRATEGY_REGISTRY["order_block"].confidence,
    ),
    "cvd_divergence": SignalPlugin(
        detector=detect_cvd_divergence,
        confidence=STRATEGY_REGISTRY["cvd_divergence"].confidence,
    ),
    "trend_day": SignalPlugin(
        detector=detect_trend_day,
        confidence=STRATEGY_REGISTRY["trend_day"].confidence,
    ),
    "engulfing": SignalPlugin(
        detector=detect_engulfing,
        confidence=STRATEGY_REGISTRY["engulfing"].confidence,
    ),
    "pin_bar": SignalPlugin(
        detector=detect_pin_bar,
        confidence=STRATEGY_REGISTRY["pin_bar"].confidence,
    ),
    "inside_bar": SignalPlugin(
        detector=detect_inside_bar,
        confidence=STRATEGY_REGISTRY["inside_bar"].confidence,
    ),
    "hammer_hanging_man": SignalPlugin(
        detector=detect_hammer_hanging_man,
        confidence=STRATEGY_REGISTRY["hammer_hanging_man"].confidence,
    ),
    "doji": SignalPlugin(
        detector=detect_doji,
        confidence=STRATEGY_REGISTRY["doji"].confidence,
    ),
    "morning_evening_star": SignalPlugin(
        detector=detect_morning_evening_star,
        confidence=STRATEGY_REGISTRY["morning_evening_star"].confidence,
    ),
    "fibonacci_retracement": SignalPlugin(
        detector=detect_fibonacci_retracement,
        confidence=STRATEGY_REGISTRY["fibonacci_retracement"].confidence,
    ),
    "fib_golden_zone": SignalPlugin(
        detector=detect_fib_golden_zone,
        confidence=STRATEGY_REGISTRY["fib_golden_zone"].confidence,
    ),
    "ote_entry": SignalPlugin(
        detector=detect_ote_entry,
        confidence=STRATEGY_REGISTRY["ote_entry"].confidence,
    ),
}
