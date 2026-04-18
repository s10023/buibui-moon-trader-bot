"""CME gap detection for crypto futures.

CME weekend closure: Friday 21:00 UTC → Sunday 22:00 UTC (+49 h).
A gap exists when the Friday close price ≠ the Sunday reopening open price.

Mirrors the logic in web/ui/src/components/CandleChart.svelte::computeCMEGap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CMEGap:
    """Most recent CME weekend gap."""

    gap_low: float
    gap_high: float
    gap_up: bool  # True = bullish (Sun open > Fri close)
    filled: bool  # True = price has since returned to fill the gap origin


def get_recent_cme_gap(
    ohlcv_df: pd.DataFrame,
    _now_sec: float | None = None,
) -> CMEGap | None:
    """Return the most recent CME weekend gap, or None if not found / too small.

    Args:
        ohlcv_df: OHLCV DataFrame with open_time (Unix ms), open, high, low, close.
        _now_sec: Override current time (Unix seconds) — for testing only.

    Detection:
        - cme_close = most recent Friday at 21:00 UTC
        - cme_open  = cme_close + 49 h (Sunday 22:00 UTC)
        - fri_close = close of the last candle whose open_time < cme_close
        - mon_open  = open of the first candle whose open_time >= cme_open
        - gap = [min(fri_close, mon_open), max(fri_close, mon_open)]

    Filled:
        - gap_up  (mon_open > fri_close): filled when any subsequent low <= gap_low
        - gap_down (mon_open < fri_close): filled when any subsequent high >= gap_high

    Returns None when:
        - DataFrame is empty
        - No Friday candle found before the CME close
        - Currently inside the CME closure window (no Monday candle yet)
        - Gap size is < 0.05% of price (noise)
    """
    if ohlcv_df.empty:
        return None

    DAY = 86_400
    WEEK = 7 * DAY

    now_sec = _now_sec if _now_sec is not None else time.time()

    # Most recent Friday at 21:00 UTC.
    # pandas weekday: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    now_ts = pd.Timestamp(now_sec, unit="s", tz="UTC")
    today_utc_midnight = int(now_sec / DAY) * DAY
    days_since_fri = (now_ts.weekday() - 4) % 7  # 0 on Fri, 1 on Sat, …
    last_fri_utc = today_utc_midnight - days_since_fri * DAY
    cme_close_sec = last_fri_utc + 21 * 3600  # Fri 21:00 UTC
    if cme_close_sec > now_sec:
        cme_close_sec -= WEEK  # this Friday is still in the future — use last week
    cme_open_sec = cme_close_sec + 49 * 3600  # Sun 22:00 UTC

    open_times_ms: np.ndarray = np.asarray(ohlcv_df["open_time"].values, dtype=np.int64)

    fri_mask: np.ndarray = open_times_ms / 1000 < cme_close_sec
    if not bool(fri_mask.any()):
        return None
    fri_idx = int(fri_mask.nonzero()[0][-1])
    fri_close = float(ohlcv_df.iloc[fri_idx]["close"])

    mon_mask: np.ndarray = open_times_ms / 1000 >= cme_open_sec
    if not bool(mon_mask.any()):
        return None  # currently inside the CME closure window
    mon_idx = int(mon_mask.nonzero()[0][0])
    mon_open = float(ohlcv_df.iloc[mon_idx]["open"])

    gap_low = min(fri_close, mon_open)
    gap_high = max(fri_close, mon_open)

    # Ignore trivially small gaps (< 0.05% of price)
    if gap_high == 0 or (gap_high - gap_low) / gap_high < 0.0005:
        return None

    gap_up = mon_open > fri_close

    subsequent = ohlcv_df.iloc[mon_idx:]
    lows: np.ndarray = np.asarray(subsequent["low"].values, dtype=float)
    highs: np.ndarray = np.asarray(subsequent["high"].values, dtype=float)
    if gap_up:
        # Bullish gap: filled when price returns down to the Friday close (gap_low)
        filled = bool((lows <= gap_low).any())
    else:
        # Bearish gap: filled when price returns up to the Friday close (gap_high)
        filled = bool((highs >= gap_high).any())

    return CMEGap(gap_low=gap_low, gap_high=gap_high, gap_up=gap_up, filled=filled)


def cme_gap_alert_warning(
    gap: CMEGap | None,
    direction: str,
    entry: float,
    tp_price: float,
) -> str | None:
    """Return a warning string when the CME gap is a relevant risk factor.

    Warning conditions (both require the gap to be unfilled):
    - LONG + gap below entry:
        An unfilled gap beneath the entry is a downside magnet — price may be
        pulled back to fill it before the rally continues.
    - SHORT + gap between entry and TP:
        An unfilled gap sitting in the path to the TP target may stall or
        reverse the move before the target is reached.

    Returns None when no warning applies (gap already filled, no gap, or gap is
    beyond the TP in the favourable direction).
    """
    if gap is None or gap.filled:
        return None

    low, high = gap.gap_low, gap.gap_high
    fmt_low = f"{low:,.2f}"
    fmt_high = f"{high:,.2f}"

    if direction == "long":
        # Unfilled gap is entirely below the entry price
        if high < entry:
            return (
                f"⚠️ CME gap {fmt_low}–{fmt_high} unfilled below — may fill before rally"
            )
    else:
        # Unfilled gap sits between entry and TP (in the path of the short move)
        if tp_price > 0 and low < entry and high > tp_price:
            return (
                f"⚠️ CME gap {fmt_low}–{fmt_high} in TP path — may stall before target"
            )

    return None
