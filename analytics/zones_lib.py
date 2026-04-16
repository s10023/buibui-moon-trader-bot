"""Structural zone extraction for chart overlay rendering.

Returns geometry dicts (not trade signals) — zone bounds, start time, active status.
Separate from indicators_lib.py to keep zone rendering concerns out of the backtest pipeline.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def extract_fvg_zones(
    df: pd.DataFrame,
    lookback: int = 100,
    min_gap_pct: float = 0.001,
    max_zones: int = 30,
) -> list[dict[str, Any]]:
    """Return FVG price boxes. active=False once the CE (midpoint) is crossed."""
    n = len(df)
    if n < 3:
        return []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    start = max(1, n - lookback - 1)
    zones: list[dict[str, Any]] = []

    for i in range(start, n - 1):
        prev_high = highs[i - 1]
        prev_low = lows[i - 1]
        nxt_low = lows[i + 1]
        nxt_high = highs[i + 1]

        # Bullish FVG: candle[i-1].high < candle[i+1].low
        if prev_high < nxt_low:
            gap_bot, gap_top = prev_high, nxt_low
            mid = (gap_bot + gap_top) / 2
            if (gap_top - gap_bot) < min_gap_pct * mid:
                continue
            # Filled when any subsequent low crosses the CE from above
            active = bool(np.all(lows[i + 2 :] > mid)) if i + 2 < n else True
            zones.append(
                {
                    "zone_type": "fvg",
                    "direction": "bull",
                    "zone_low": gap_bot,
                    "zone_high": gap_top,
                    "start_ms": int(open_times[i - 1]),
                    "active": active,
                }
            )

        # Bearish FVG: candle[i-1].low > candle[i+1].high
        elif prev_low > nxt_high:
            gap_bot, gap_top = nxt_high, prev_low
            mid = (gap_bot + gap_top) / 2
            if (gap_top - gap_bot) < min_gap_pct * mid:
                continue
            active = bool(np.all(highs[i + 2 :] < mid)) if i + 2 < n else True
            zones.append(
                {
                    "zone_type": "fvg",
                    "direction": "bear",
                    "zone_low": gap_bot,
                    "zone_high": gap_top,
                    "start_ms": int(open_times[i - 1]),
                    "active": active,
                }
            )

    # Active zones first, then the 5 most recent filled ones (for context)
    active_zones = [z for z in zones if z["active"]]
    inactive_zones = [z for z in zones if not z["active"]][-5:]
    combined = active_zones + inactive_zones
    return combined[-max_zones:]


def extract_order_block_zones(
    df: pd.DataFrame,
    lookback: int = 100,
    displacement_pct: float = 0.003,
    max_zones: int = 20,
) -> list[dict[str, Any]]:
    """Return OB price boxes. active=False once the zone is fully mitigated."""
    n = len(df)
    if n < 3:
        return []

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    start = max(0, n - lookback - 2)
    zones: list[dict[str, Any]] = []

    for i in range(start, n - 2):
        ob_open = opens[i]
        ob_high = highs[i]
        ob_low = lows[i]
        ob_close = closes[i]
        disp_close = closes[i + 1]

        # Bearish OB: bullish candle + bearish displacement
        if ob_close > ob_open and disp_close < ob_low * (1 - displacement_pct):
            ob_zone_bot = ob_open
            ob_zone_top = ob_close
            # Mitigated: price enters zone AND closes below its bottom
            active = True
            for j in range(i + 2, n):
                if highs[j] >= ob_zone_bot and closes[j] < ob_zone_bot:
                    active = False
                    break
            zones.append(
                {
                    "zone_type": "ob",
                    "direction": "bear",
                    "zone_low": ob_zone_bot,
                    "zone_high": ob_zone_top,
                    "start_ms": int(open_times[i]),
                    "active": active,
                }
            )

        # Bullish OB: bearish candle + bullish displacement
        elif ob_open > ob_close and disp_close > ob_high * (1 + displacement_pct):
            ob_zone_bot = ob_close
            ob_zone_top = ob_open
            active = True
            for j in range(i + 2, n):
                if lows[j] <= ob_zone_top and closes[j] > ob_zone_top:
                    active = False
                    break
            zones.append(
                {
                    "zone_type": "ob",
                    "direction": "bull",
                    "zone_low": ob_zone_bot,
                    "zone_high": ob_zone_top,
                    "start_ms": int(open_times[i]),
                    "active": active,
                }
            )

    active_zones = [z for z in zones if z["active"]]
    inactive_zones = [z for z in zones if not z["active"]][-3:]
    return (active_zones + inactive_zones)[-max_zones:]


def extract_eqh_eql_zones(
    df: pd.DataFrame,
    lookback: int = 100,
    tolerance_pct: float = 0.003,
    swing_n: int = 5,
    max_zones: int = 10,
) -> list[dict[str, Any]]:
    """Return EQH/EQL horizontal lines (liquidity pool levels).

    Finds pairs of swing highs (or lows) within tolerance_pct of each other.
    active=False when the pool has been swept (wick above EQH or below EQL).
    """
    n = len(df)
    if n < 2 * swing_n + 1:
        return []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    start = max(swing_n, n - lookback - swing_n)

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    for i in range(start, n - swing_n):
        lo = max(0, i - swing_n)
        hi = min(n, i + swing_n + 1)
        if highs[i] == float(np.max(highs[lo:hi])):
            swing_highs.append((i, highs[i]))
        if lows[i] == float(np.min(lows[lo:hi])):
            swing_lows.append((i, lows[i]))

    zones: list[dict[str, Any]] = []

    # Cluster equal highs (EQH)
    seen_eqh: set[int] = set()
    for j in range(len(swing_highs)):
        if j in seen_eqh:
            continue
        for k in range(j + 1, len(swing_highs)):
            if k in seen_eqh:
                continue
            idx_j, price_j = swing_highs[j]
            idx_k, price_k = swing_highs[k]
            if abs(price_j - price_k) / price_j < tolerance_pct:
                pool_price = (price_j + price_k) / 2
                # Swept if any subsequent wick exceeded the pool + tolerance
                threshold = pool_price * (1 + tolerance_pct)
                active = (
                    bool(np.all(highs[idx_k + 1 :] <= threshold))
                    if idx_k + 1 < n
                    else True
                )
                zones.append(
                    {
                        "zone_type": "eqh",
                        "direction": "bear",
                        "price": pool_price,
                        "start_ms": int(open_times[idx_j]),
                        "label": "EQH",
                        "active": active,
                    }
                )
                seen_eqh.add(j)
                seen_eqh.add(k)
                break

    # Cluster equal lows (EQL)
    seen_eql: set[int] = set()
    for j in range(len(swing_lows)):
        if j in seen_eql:
            continue
        for k in range(j + 1, len(swing_lows)):
            if k in seen_eql:
                continue
            idx_j, price_j = swing_lows[j]
            idx_k, price_k = swing_lows[k]
            if abs(price_j - price_k) / price_j < tolerance_pct:
                pool_price = (price_j + price_k) / 2
                threshold = pool_price * (1 - tolerance_pct)
                active = (
                    bool(np.all(lows[idx_k + 1 :] >= threshold))
                    if idx_k + 1 < n
                    else True
                )
                zones.append(
                    {
                        "zone_type": "eql",
                        "direction": "bull",
                        "price": pool_price,
                        "start_ms": int(open_times[idx_j]),
                        "label": "EQL",
                        "active": active,
                    }
                )
                seen_eql.add(j)
                seen_eql.add(k)
                break

    return zones[-max_zones:]


def extract_bos_zones(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    lookback: int = 100,
    max_zones: int = 8,
) -> list[dict[str, Any]]:
    """Return recent unbroken swing high/low levels (structural support/resistance).

    These are the levels traders watch for BOS — a close beyond them confirms a
    Break of Structure. active=False when the level has already been broken.
    """
    n = len(df)
    if n < swing_lookback * 3:
        return []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    start = max(swing_lookback, n - lookback - swing_lookback)
    zones: list[dict[str, Any]] = []

    for i in range(start, n - swing_lookback):
        lo = max(0, i - swing_lookback)
        hi = min(n, i + swing_lookback + 1)
        conf_start = i + swing_lookback + 1

        # Swing high → potential bearish BOS level
        if highs[i] == float(np.max(highs[lo:hi])):
            broken = (
                bool(np.any(closes[conf_start:] > highs[i]))
                if conf_start < n
                else False
            )
            if not broken:  # only return unbroken (active) structural levels
                zones.append(
                    {
                        "zone_type": "bos",
                        "direction": "bear",
                        "price": float(highs[i]),
                        "start_ms": int(open_times[i]),
                        "label": "R",
                        "active": True,
                    }
                )

        # Swing low → potential bullish BOS level
        if lows[i] == float(np.min(lows[lo:hi])):
            broken = (
                bool(np.any(closes[conf_start:] < lows[i])) if conf_start < n else False
            )
            if not broken:
                zones.append(
                    {
                        "zone_type": "bos",
                        "direction": "bull",
                        "price": float(lows[i]),
                        "start_ms": int(open_times[i]),
                        "label": "S",
                        "active": True,
                    }
                )

    # Most recent unbroken levels only
    return zones[-max_zones:]


def extract_fib_golden_zones(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    bos_lookback: int = 5,
) -> list[dict[str, Any]]:
    """Return current Fib Golden Zone box (0.5–0.618) from the most recent BOS swing.

    Returns 0 or 1 zone. The zone represents where price is expected to retrace
    after a confirmed BOS — the 50–61.8% retracement pocket.
    """
    from analytics.indicators_lib import _find_bos_swing  # noqa: PLC0415

    n = len(df)
    if n < swing_lookback + bos_lookback + 2:
        return []

    open_times = df["open_time"].to_numpy(dtype=int)
    bos = _find_bos_swing(df, swing_lookback, bos_lookback)
    if bos is None:
        return []

    sl_price, sh_price, direction = bos
    swing_range = sh_price - sl_price
    if swing_range <= 0.0:
        return []

    if direction == "long":
        # Bullish BOS: retracement zone 50–61.8% from swing high downward
        zone_high = sh_price - 0.5 * swing_range
        zone_low = sh_price - 0.618 * swing_range
        dir_out = "bull"
    else:
        # Bearish BOS: retracement zone 50–61.8% from swing low upward
        zone_low = sl_price + 0.5 * swing_range
        zone_high = sl_price + 0.618 * swing_range
        dir_out = "bear"

    start_idx = max(0, n - swing_lookback - bos_lookback - 1)
    return [
        {
            "zone_type": "fib_zone",
            "direction": dir_out,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "start_ms": int(open_times[start_idx]),
            "active": True,
        }
    ]


def extract_ote_zones(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    bos_lookback: int = 5,
) -> list[dict[str, Any]]:
    """Return current OTE zone box (0.618–0.786) from the most recent BOS swing.

    Returns 0 or 1 zone. The deeper retracement pocket used by ICT OTE entries.
    """
    from analytics.indicators_lib import _find_bos_swing  # noqa: PLC0415

    n = len(df)
    if n < swing_lookback + bos_lookback + 2:
        return []

    open_times = df["open_time"].to_numpy(dtype=int)
    bos = _find_bos_swing(df, swing_lookback, bos_lookback)
    if bos is None:
        return []

    sl_price, sh_price, direction = bos
    swing_range = sh_price - sl_price
    if swing_range <= 0.0:
        return []

    if direction == "long":
        zone_high = sh_price - 0.618 * swing_range
        zone_low = sh_price - 0.786 * swing_range
        dir_out = "bull"
    else:
        zone_low = sl_price + 0.618 * swing_range
        zone_high = sl_price + 0.786 * swing_range
        dir_out = "bear"

    start_idx = max(0, n - swing_lookback - bos_lookback - 1)
    return [
        {
            "zone_type": "ote",
            "direction": dir_out,
            "zone_low": zone_low,
            "zone_high": zone_high,
            "start_ms": int(open_times[start_idx]),
            "active": True,
        }
    ]


def extract_swing_points(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    lookback: int = 100,
    max_points: int = 30,
) -> list[dict[str, Any]]:
    """Return recent 3-bar pivot swing highs and lows."""
    n = len(df)
    if n < swing_lookback * 2 + 1:
        return []

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    start = max(swing_lookback, n - lookback - swing_lookback)
    points: list[dict[str, Any]] = []

    for i in range(start, n - swing_lookback):
        lo = max(0, i - swing_lookback)
        hi = min(n, i + swing_lookback + 1)

        if highs[i] == float(np.max(highs[lo:hi])):
            points.append(
                {
                    "swing_type": "high",
                    "price": float(highs[i]),
                    "time_ms": int(open_times[i]),
                }
            )
        if lows[i] == float(np.min(lows[lo:hi])):
            points.append(
                {
                    "swing_type": "low",
                    "price": float(lows[i]),
                    "time_ms": int(open_times[i]),
                }
            )

    return points[-max_points:]
