"""Shared helpers used by multiple detectors (and zones_lib).

Extracted from `analytics/indicators_lib.py` in strat-1 (`_find_bos_swing`,
`volume_confirm`) and strat-2 (`_MYT`, `_fmt_time`, `_empty_signals`,
`_signals_to_df`). No behaviour change.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from analytics.strategies._base import SIGNAL_COLUMNS

_MYT = timezone(timedelta(hours=8))


def _fmt_time(ts_ms: int) -> str:
    """Format a Unix ms timestamp as a short MYT (UTC+8) string for alert context."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=_MYT).strftime("%d-%b %H:%M")


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=SIGNAL_COLUMNS)


def _signals_to_df(signals: list[dict[str, object]]) -> pd.DataFrame:
    if not signals:
        return _empty_signals()
    df = pd.DataFrame(signals)
    # Ensure all expected columns exist; fill low_volume with False for detectors
    # that don't use a volume gate (keeps the column schema uniform).
    for col in SIGNAL_COLUMNS:
        if col not in df.columns:
            df[col] = False if col == "low_volume" else None
    return (
        df[SIGNAL_COLUMNS].drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    )


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    """Standard recursive EMA (adjust=False) with alpha = 2 / (span + 1).

    Why adjust=False: this matches the canonical trading-platform EMA
    (TradingView, MT4, Binance) — pandas' default `adjust=True` uses an
    unequal weighting at series start that drifts from broker values.
    """
    return series.astype(float).ewm(span=span, adjust=False).mean()


def ema_cross_count(
    close: pd.Series,
    ema: pd.Series,
    idx: int,
    lookback: int,
) -> int:
    """Count price/EMA sign changes in the last `lookback` bars ending at idx.

    A "cross" is a sign flip in (close - ema) between two consecutive non-zero
    samples within the window. Zero-difference bars are skipped (treated as
    inheriting the prior sign) so a single touch does not inflate the count.
    """
    start = max(0, idx - lookback + 1)
    if start > idx:
        return 0
    diffs = close.iloc[start : idx + 1].to_numpy(dtype=float) - ema.iloc[
        start : idx + 1
    ].to_numpy(dtype=float)
    count = 0
    last_sign = 0
    for d in diffs:
        s = 1 if d > 0 else (-1 if d < 0 else 0)
        if s == 0:
            continue
        if last_sign != 0 and s != last_sign:
            count += 1
        last_sign = s
    return count


def compute_htf_ema_slope(
    closes: pd.Series,
    period: int,
    slope_lookback: int,
) -> float | None:
    """Compute the EMA slope as a fraction over the last `slope_lookback` bars.

    Returns (ema[-1] - ema[-1 - slope_lookback]) / ema[-1 - slope_lookback].
    Positive = up-slope; negative = down-slope.

    Returns None when:
    - the series has fewer than `period + slope_lookback + 1` candles (warmup),
    - the EMA value `slope_lookback` bars ago is zero (degenerate),
    - or the input is empty.

    Uses the same EMA semantics as compute_ema (adjust=False).
    """
    if closes is None or len(closes) < period + slope_lookback + 1:
        return None
    ema = compute_ema(closes, period)
    now = float(ema.iloc[-1])
    then = float(ema.iloc[-1 - slope_lookback])
    if then == 0.0:
        return None
    return (now - then) / then


def is_trending(
    close: pd.Series,
    ema_fast: pd.Series,
    ema_slow: pd.Series,
    idx: int,
    slope_lookback: int = 10,
    regime_lookback: int = 20,
    max_crosses: int = 2,
    min_slope_pct: float = 0.003,
) -> bool:
    """Binary regime gate: trending vs range/chop.

    Trending requires both:
    - few enough price/fast-EMA crosses in `regime_lookback` bars
      (range markets cross the EMA repeatedly),
    - slow-EMA |slope| over `slope_lookback` bars >= `min_slope_pct`
      (a flat EMA = no directional conviction).
    """
    if idx < max(slope_lookback, regime_lookback - 1):
        return False
    slow_now = float(ema_slow.iloc[idx])
    slow_then = float(ema_slow.iloc[idx - slope_lookback])
    if slow_then == 0.0:
        return False
    slope_pct = abs((slow_now - slow_then) / slow_then)
    if slope_pct < min_slope_pct:
        return False
    crosses = ema_cross_count(close, ema_fast, idx, regime_lookback)
    return crosses <= max_crosses


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
    "_empty_signals",
    "_find_bos_swing",
    "_fmt_time",
    "_signals_to_df",
    "compute_ema",
    "ema_cross_count",
    "is_trending",
    "volume_confirm",
]
