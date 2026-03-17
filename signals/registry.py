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


SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    "wick_fill": SignalPlugin(detector=detect_wick_fills),
    "marubozu": SignalPlugin(detector=detect_marubozu_retest),
    "orb": SignalPlugin(detector=detect_orb_breakout),
    "liquidity_sweep": SignalPlugin(detector=detect_liquidity_sweep),
    "fvg": SignalPlugin(detector=detect_fvg),
    "bos": SignalPlugin(detector=detect_market_structure),
    "funding_reversion": SignalPlugin(detector=detect_funding_extreme),
    "smt_divergence": SignalPlugin(detector=detect_smt_divergence),
}
