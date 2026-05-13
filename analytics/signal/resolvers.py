"""Per-strategy parameter resolvers: tp_r, sl_pct, atr_sl_multiplier, volume_*.

Resolution order: symbol+TF → symbol → TF-specific → directional → strategy-wide → global.
"""

from analytics.signal_config import StrategyOverride


def _resolve_tp_r(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_tp_r: float,
    direction: str = "",
) -> float:
    """Resolve effective tp_r: symbol+TF → symbol → TF-specific → directional → strategy-wide → global."""
    if not strategy_params:
        return global_tp_r
    override = strategy_params.get(strategy)
    if override is None:
        return global_tp_r
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.tp_r_per_tf:
            return sym.tp_r_per_tf[tf]
        if sym.tp_r is not None:
            return sym.tp_r
    if tf in override.tp_r_per_tf:
        return override.tp_r_per_tf[tf]
    if direction == "long" and override.tp_r_long is not None:
        return override.tp_r_long
    if direction == "short" and override.tp_r_short is not None:
        return override.tp_r_short
    if override.tp_r is not None:
        return override.tp_r
    return global_tp_r


def _resolve_sl_pct(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_sl_pct: float,
) -> float:
    """Resolve effective sl_pct: symbol+TF → symbol → TF-specific → strategy-wide → global."""
    if not strategy_params:
        return global_sl_pct
    override = strategy_params.get(strategy)
    if override is None:
        return global_sl_pct
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.sl_pct_per_tf:
            return sym.sl_pct_per_tf[tf]
        if sym.sl_pct is not None:
            return sym.sl_pct
    if tf in override.sl_pct_per_tf:
        return override.sl_pct_per_tf[tf]
    if override.sl_pct is not None:
        return override.sl_pct
    return global_sl_pct


def _resolve_atr_sl_multiplier(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_atr_sl: float | None,
) -> float | None:
    """Resolve effective atr_sl_multiplier: symbol+TF → symbol → TF-specific → strategy-wide → global."""
    if not strategy_params:
        return global_atr_sl
    override = strategy_params.get(strategy)
    if override is None:
        return global_atr_sl
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.atr_sl_multiplier_per_tf:
            return sym.atr_sl_multiplier_per_tf[tf]
        if sym.atr_sl_multiplier is not None:
            return sym.atr_sl_multiplier
    if tf in override.atr_sl_multiplier_per_tf:
        return override.atr_sl_multiplier_per_tf[tf]
    if override.atr_sl_multiplier is not None:
        return override.atr_sl_multiplier
    return global_atr_sl


def _resolve_atr_sl_floor(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    symbol: str,
    tf: str,
    global_atr_sl_floor: bool,
) -> bool:
    """Resolve effective atr_sl_floor: symbol+TF → symbol → TF-specific → strategy-wide → global."""
    if not strategy_params:
        return global_atr_sl_floor
    override = strategy_params.get(strategy)
    if override is None:
        return global_atr_sl_floor
    sym = override.per_symbol.get(symbol)
    if sym is not None:
        if tf in sym.atr_sl_floor_per_tf:
            return sym.atr_sl_floor_per_tf[tf]
        if sym.atr_sl_floor is not None:
            return sym.atr_sl_floor
    if tf in override.atr_sl_floor_per_tf:
        return override.atr_sl_floor_per_tf[tf]
    if override.atr_sl_floor is not None:
        return override.atr_sl_floor
    return global_atr_sl_floor


def _resolve_volume_suppress(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    global_suppress: bool,
) -> bool:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None and override.volume_suppress is not None:
            return override.volume_suppress
    return global_suppress


def _resolve_volume_spike_boost(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
    global_boost: bool,
) -> bool:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None and override.volume_spike_boost is not None:
            return override.volume_spike_boost
    return global_boost


def _resolve_volume_suppress_long(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_long
    return None


def _resolve_volume_suppress_short(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_short
    return None


def _resolve_volume_spike_boost_long(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_long
    return None


def _resolve_volume_spike_boost_short(
    strategy_params: dict[str, StrategyOverride] | None,
    strategy: str,
) -> bool | None:
    if strategy_params:
        override = strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_short
    return None
