"""Strategies package — scaffold landed in strat-1; detectors move in strat-2.

Eager imports cover the leaf modules (`_base`, `_shared`, `_seasonality`),
which are dependency-free w.r.t. `indicators_lib`. The registry symbols
(STRATEGY_REGISTRY etc.) still live in `analytics.indicators_lib` during
strat-1 — they are exposed here lazily via `__getattr__` to avoid a circular
import (indicators_lib imports `_base`/`_shared`/`_seasonality`, so eagerly
re-importing back from indicators_lib here would deadlock the load order).
"""

from typing import TYPE_CHECKING, Any

from analytics.strategies._base import SIGNAL_COLUMNS, ParamSpec, StrategySpec
from analytics.strategies._seasonality import seasonality_stats
from analytics.strategies._shared import _find_bos_swing, volume_confirm

if TYPE_CHECKING:
    from analytics.indicators_lib import (
        DETECTOR_REGISTRY,
        INCOMPATIBLE_PAIRS,
        KNOWN_STRATEGIES,
        KNOWN_STRATEGY_TYPES,
        STRATEGY_REGISTRY,
        STRATEGY_TYPE_GROUPS,
    )


_REGISTRY_EXPORTS = frozenset(
    {
        "DETECTOR_REGISTRY",
        "INCOMPATIBLE_PAIRS",
        "KNOWN_STRATEGIES",
        "KNOWN_STRATEGY_TYPES",
        "STRATEGY_REGISTRY",
        "STRATEGY_TYPE_GROUPS",
    }
)


def __getattr__(name: str) -> Any:
    if name in _REGISTRY_EXPORTS:
        from analytics.strategies import _registry  # noqa: PLC0415

        return getattr(_registry, name)
    raise AttributeError(f"module 'analytics.strategies' has no attribute {name!r}")


__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "ParamSpec",
    "SIGNAL_COLUMNS",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
    "StrategySpec",
    "_find_bos_swing",
    "seasonality_stats",
    "volume_confirm",
]
