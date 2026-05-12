"""F9 ATR-as-min-SL floor for the live signal path.

Mirrors the backtest engine semantics in `analytics/backtest/engine.py`:
when `atr_sl_floor=True` and a structural SL is present, widen the SL to
`max(structural_dist, atr_sl_multiplier × ATR14)`. The TP is then
recomputed from `tp_r × new_sl_dist` so R:R stays at the configured
multiple — without this, widening the SL silently degrades the implied
reward-to-risk and the live alert diverges from its backtest cell.

Default off everywhere — opt-in per-strategy / per-symbol / per-TF via
`atr_sl_floor` in `config/strategy_params.toml`. With the flag off, this
helper is a no-op.
"""

import logging

import numpy as np
import pandas as pd

from analytics.backtest.engine import _compute_atr14
from analytics.signal.resolvers import (
    _resolve_atr_sl_floor,
    _resolve_atr_sl_multiplier,
    _resolve_tp_r,
)
from analytics.signal.types import SignalEvent
from analytics.signal_config import StrategyOverride

logger = logging.getLogger(__name__)


def _apply_atr_floor(
    events: list[SignalEvent],
    ohlcv_df: pd.DataFrame,
    symbol: str,
    tf: str,
    strategy_params: dict[str, StrategyOverride] | None,
    global_tp_r: float,
    global_atr_sl_multiplier: float | None,
    global_atr_sl_floor: bool,
) -> list[SignalEvent]:
    """Widen tight structural SLs to an ATR floor when the strategy opts in.

    For each event whose strategy has `atr_sl_floor` enabled:
      1. Resolve `atr_sl_multiplier` and direction-aware `tp_r`.
      2. Compute ATR14 at the signal candle (last closed bar in ohlcv_df).
      3. If `atr_sl_multiplier × ATR14 > |entry - sl_price|`, widen
         `sl_price` and recompute `tp_price = entry ± tp_r × new_sl_dist`.

    No-op when:
      - the floor is disabled for this (strategy, symbol, tf),
      - `atr_sl_multiplier` is not set (nothing to scale by),
      - the event has no structural SL (`sl_price == 0`),
      - ATR14 is unavailable (insufficient history) — falls open.

    Mutates `SignalEvent` in place and returns the same list for symmetry
    with the gate helpers. Never raises.
    """
    if not events or ohlcv_df is None or ohlcv_df.empty:
        return events

    # Signal candle = last closed bar. scanner.scan_symbol drops the
    # in-progress candle before passing data to detectors (closed_df =
    # ohlcv_df.iloc[:-1]), so the index here matches what the detector saw.
    if len(ohlcv_df) < 2:
        return events
    sig_idx = len(ohlcv_df) - 2

    # ATR14 is per (symbol, tf, sig_idx) — same value for every event in the
    # batch, so compute once. Falls open (no-op) when ATR is unavailable.
    highs = ohlcv_df["high"].to_numpy(dtype=np.float64)
    lows = ohlcv_df["low"].to_numpy(dtype=np.float64)
    closes = ohlcv_df["close"].to_numpy(dtype=np.float64)
    atr = _compute_atr14(highs, lows, closes, sig_idx)
    if atr is None:
        return events

    for event in events:
        if event.sl_price == 0.0:
            continue  # no structural SL — nothing to floor
        if not _resolve_atr_sl_floor(
            strategy_params, event.strategy, symbol, tf, global_atr_sl_floor
        ):
            continue
        atr_mult = _resolve_atr_sl_multiplier(
            strategy_params, event.strategy, symbol, tf, global_atr_sl_multiplier
        )
        if atr_mult is None:
            continue  # floor enabled but no multiplier configured — no-op

        entry = event.price
        structural_dist = abs(entry - event.sl_price)
        atr_dist = atr_mult * atr
        if atr_dist <= structural_dist:
            continue  # structural SL is already wider — no change

        eff_tp_r = _resolve_tp_r(
            strategy_params, event.strategy, symbol, tf, global_tp_r, event.direction
        )
        new_sl_dist = atr_dist
        if event.direction == "long":
            new_sl = entry - new_sl_dist
            new_tp = entry + eff_tp_r * new_sl_dist
        else:
            new_sl = entry + new_sl_dist
            new_tp = entry - eff_tp_r * new_sl_dist

        logger.info(
            "ATR floor widened %s %s %s %s: sl %.6f→%.6f (atr_mult=%.2f, atr=%.6f), "
            "tp recomputed %.6f→%.6f at tp_r=%.2f",
            symbol,
            tf,
            event.strategy,
            event.direction,
            event.sl_price,
            new_sl,
            atr_mult,
            atr,
            event.tp_price,
            new_tp,
            eff_tp_r,
        )
        event.sl_price = new_sl
        event.tp_price = new_tp

    return events
