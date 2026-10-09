"""The ONE spelling of ``volume_suppress`` precedence, shared by live and backtest.

Effective flag for a (strategy, timeframe, direction) cell, highest first:

1. ``volume_suppress_{long,short}_per_tf[tf]``  (per-timeframe directional)
2. ``volume_suppress_{long,short}``             (directional)
3. ``volume_suppress``                          (strategy-wide)
4. ``[backtest].volume_suppress``               (global)

The live scanner (``analytics/signal/``), ``SignalWatchConfig`` and
``BacktestSweepConfig`` all resolve through these functions, and the engine
(``run_backtest``) picks its per-direction flag through :func:`pick_volume_suppress`.
Two classes carry the override fields (``signal_config.StrategyOverride`` and
``backtest_config.StrategyOverride``), so the helpers take a structural
:class:`VolumeSuppressOverride` rather than either class: restating this rule per
class is how the backtest came to ignore the per-timeframe tables (#970).

This module imports nothing from the repo so both config loaders can use it
without an import cycle.
"""

from collections.abc import Mapping
from typing import Protocol


class VolumeSuppressOverride(Protocol):
    """The fields a strategy override must carry to resolve ``volume_suppress``.

    Read-only properties so a dataclass attribute typed ``dict[str, bool]``
    satisfies ``Mapping[str, bool]`` (mutable protocol attributes are invariant).
    """

    @property
    def volume_suppress(self) -> bool | None: ...

    @property
    def volume_suppress_long(self) -> bool | None: ...

    @property
    def volume_suppress_short(self) -> bool | None: ...

    @property
    def volume_suppress_long_per_tf(self) -> Mapping[str, bool]: ...

    @property
    def volume_suppress_short_per_tf(self) -> Mapping[str, bool]: ...


def resolve_volume_suppress(
    strategy_params: Mapping[str, VolumeSuppressOverride] | None,
    strategy: str,
    global_suppress: bool,
) -> bool:
    """Strategy-wide flag, falling back to the global ``[backtest]`` flag."""
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None and override.volume_suppress is not None:
            return override.volume_suppress
    return global_suppress


def resolve_volume_suppress_directional(
    strategy_params: Mapping[str, VolumeSuppressOverride] | None,
    strategy: str,
    direction: str,
    tf: str | None = None,
) -> bool | None:
    """Per-tf-direction > per-direction; ``None`` when neither is set.

    ``None`` means the caller falls back to the strategy-wide flag. A ``tf`` of
    ``None`` skips the per-timeframe table.
    """
    if not strategy_params:
        return None
    override = strategy_params.get(strategy)
    if override is None:
        return None
    if direction == "long":
        if tf is not None and tf in override.volume_suppress_long_per_tf:
            return override.volume_suppress_long_per_tf[tf]
        return override.volume_suppress_long
    if direction == "short":
        if tf is not None and tf in override.volume_suppress_short_per_tf:
            return override.volume_suppress_short_per_tf[tf]
        return override.volume_suppress_short
    return None


def pick_volume_suppress(
    direction: str,
    base: bool,
    long: bool | None,
    short: bool | None,
) -> bool:
    """Choose the flag for one trade direction from already-resolved inputs.

    A directional value wins over ``base`` for its own direction. This is the
    engine's rule and the live scanner's: both call it.
    """
    if direction == "long" and long is not None:
        return long
    if direction == "short" and short is not None:
        return short
    return base


def effective_volume_suppress(
    strategy_params: Mapping[str, VolumeSuppressOverride] | None,
    strategy: str,
    global_suppress: bool,
    direction: str,
    tf: str | None = None,
) -> bool:
    """The full precedence chain for one (strategy, tf, direction) cell."""
    return pick_volume_suppress(
        direction,
        resolve_volume_suppress(strategy_params, strategy, global_suppress),
        resolve_volume_suppress_directional(strategy_params, strategy, "long", tf),
        resolve_volume_suppress_directional(strategy_params, strategy, "short", tf),
    )
