"""Signal plugin registry for the signal daemon.

Maps strategy name to plugin metadata. Seasonality is excluded —
it produces stats, not actionable entry signals.
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
    requires_funding: bool
    requires_secondary: bool


SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    "wick_fill": SignalPlugin(
        detector=detect_wick_fills,
        requires_funding=False,
        requires_secondary=False,
    ),
    "marubozu": SignalPlugin(
        detector=detect_marubozu_retest,
        requires_funding=False,
        requires_secondary=False,
    ),
    "orb": SignalPlugin(
        detector=detect_orb_breakout,
        requires_funding=False,
        requires_secondary=False,
    ),
    "liquidity_sweep": SignalPlugin(
        detector=detect_liquidity_sweep,
        requires_funding=False,
        requires_secondary=False,
    ),
    "fvg": SignalPlugin(
        detector=detect_fvg,
        requires_funding=False,
        requires_secondary=False,
    ),
    "bos": SignalPlugin(
        detector=detect_market_structure,
        requires_funding=False,
        requires_secondary=False,
    ),
    "funding_reversion": SignalPlugin(
        detector=detect_funding_extreme,
        requires_funding=True,
        requires_secondary=False,
    ),
    "smt_divergence": SignalPlugin(
        detector=detect_smt_divergence,
        requires_funding=False,
        requires_secondary=True,
    ),
}
