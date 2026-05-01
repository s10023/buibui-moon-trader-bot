"""Strategy registry assembler.

In strat-1 this module re-exports from `analytics.indicators_lib` (where the
20 detectors and STRATEGY_REGISTRY still live). In strat-2 this becomes the
explicit-tuple-driven assembler.

The actual binding from `analytics.indicators_lib` happens at *first attribute
access* via module-level `__getattr__`. Eager top-level `from
analytics.indicators_lib import ...` would create a circular import, because
`indicators_lib.py` itself imports from `analytics.strategies._base/_shared/
_seasonality` — and importing those siblings runs `analytics/strategies/__init__.py`,
which would then try to re-load `_registry` while `indicators_lib` is still
partially initialised (matches the signal-3 `format_confluence_alert` precedent).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from analytics.indicators_lib import (
        DETECTOR_REGISTRY,
        INCOMPATIBLE_PAIRS,
        KNOWN_STRATEGIES,
        KNOWN_STRATEGY_TYPES,
        STRATEGY_REGISTRY,
        STRATEGY_TYPE_GROUPS,
    )


_EXPORTS = frozenset(
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
    if name in _EXPORTS:
        from analytics import indicators_lib  # noqa: PLC0415

        return getattr(indicators_lib, name)
    raise AttributeError(
        f"module 'analytics.strategies._registry' has no attribute {name!r}"
    )


__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
]
