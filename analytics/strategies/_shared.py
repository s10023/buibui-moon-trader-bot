"""Shared helpers used by multiple detectors (and zones_lib).

Extracted from `analytics/indicators_lib.py` in strat-1. No behaviour change.
"""

import pandas as pd


def volume_confirm(
    df: pd.DataFrame,
    idx: int,
    multiplier: float = 1.5,
    lookback: int = 20,
) -> bool:
    """Return True if the candle at `idx` has volume >= multiplier × rolling mean.

    Uses the `lookback` candles *before* idx (no lookahead) to compute the
    rolling average.  Returns True when volume data is unavailable (safe default).
    """
    if "volume" not in df.columns:
        return True
    if idx < 1:
        return True
    start = max(0, idx - lookback)
    prior_vols = df["volume"].iloc[start:idx].astype(float)
    if prior_vols.empty:
        return True
    avg = float(prior_vols.mean())
    if avg == 0.0:
        return True
    return float(df["volume"].iloc[idx]) >= multiplier * avg


def _find_bos_swing(
    df: pd.DataFrame,
    swing_lookback: int,
    bos_lookback: int,
) -> tuple[float, float, str] | None:
    """Find the most recent BOS and return (swing_low, swing_high, direction).

    Two-zone approach (no-lookahead):

    - Structural zone: [win_start, bos_start) — find the anchor swing high/low.
      Uses absolute max/min to identify the dominant structural level.
    - BOS zone: [bos_start, n-1) — check whether price broke the structural level.
      The signal candle (n-1) is never included.

    Bullish BOS:
    1. Structural zone: lowest low = swing_low, highest high after swing_low = swing_high.
    2. BOS zone: any bar has close or high > swing_high → bullish BOS confirmed.

    Bearish BOS (symmetric):
    1. Structural zone: highest high = swing_high, lowest low after swing_high = swing_low.
    2. BOS zone: any bar has close or low < swing_low → bearish BOS confirmed.

    Returns None if no clear BOS is found.
    direction: 'long' (bullish BOS) | 'short' (bearish BOS).
    """
    n = len(df)
    # Need structural zone + BOS zone + signal candle
    if n < swing_lookback + bos_lookback + 1:
        return None

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    # BOS zone: last bos_lookback bars before the signal candle
    bos_start = n - bos_lookback - 1  # inclusive
    # Structural zone: swing_lookback bars before BOS zone
    struct_start = max(0, bos_start - swing_lookback)
    struct_end = bos_start  # exclusive

    if struct_end - struct_start < 2:
        return None

    # --- Bullish BOS ---
    # Structural swing_low = min low in structural zone
    sl_local = int(lows[struct_start:struct_end].argmin())
    sl_idx = struct_start + sl_local
    sl_price = float(lows[sl_idx])
    # Structural swing_high = max high from sl_idx forward (within structural zone)
    post_sl_end = struct_end
    if sl_idx + 1 < post_sl_end:
        sh_local = int(highs[sl_idx:post_sl_end].argmax())
        sh_idx = sl_idx + sh_local
        sh_price = float(highs[sh_idx])
        if sh_price > sl_price and sh_idx > sl_idx:
            # Check BOS zone for break above sh_price
            for conf_i in range(bos_start, n - 1):
                if closes[conf_i] > sh_price or highs[conf_i] > sh_price:
                    return (sl_price, sh_price, "long")

    # --- Bearish BOS ---
    sh_local2 = int(highs[struct_start:struct_end].argmax())
    sh_idx2 = struct_start + sh_local2
    sh_price2 = float(highs[sh_idx2])
    if sh_idx2 + 1 < struct_end:
        sl_local2 = int(lows[sh_idx2:struct_end].argmin())
        sl_idx2 = sh_idx2 + sl_local2
        sl_price2 = float(lows[sl_idx2])
        if sh_price2 > sl_price2 and sl_idx2 > sh_idx2:
            for conf_i in range(bos_start, n - 1):
                if closes[conf_i] < sl_price2 or lows[conf_i] < sl_price2:
                    return (sl_price2, sh_price2, "short")

    return None


__all__ = [
    "_find_bos_swing",
    "volume_confirm",
]
