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
    detect_funding_extreme,
    detect_fvg,
    detect_liquidity_sweep,
    detect_market_structure,
    detect_marubozu_retest,
    detect_orb_breakout,
    detect_smt_divergence,
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
}
