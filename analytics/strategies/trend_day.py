"""Detector: Trend Day — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_trend_day(
    df: pd.DataFrame,
    body_pct_min: float = 0.65,
    wick_max: float = 0.15,
) -> pd.DataFrame:
    """Detect Trend Day candles — sessions that open near one extreme and close near the other.

    A Trend Day is characterised by:
    - A large body relative to the total candle range (body_pct >= body_pct_min).
    - A tiny leading wick (the wick on the direction-of-travel side).

    Bullish trend day: body_pct >= body_pct_min AND lower_wick_pct <= wick_max AND close > open.
    Bearish trend day: body_pct >= body_pct_min AND upper_wick_pct <= wick_max AND close < open.

    Candles with zero range (high == low) are skipped.

    body_pct_min: minimum abs(close - open) / (high - low) ratio (default 0.65 = 65%).
    wick_max: maximum leading wick / range ratio (default 0.15 = 15%).

    Signal open_time is the candle's own open_time (the event IS the candle).
    SL is placed at the candle's opposite extreme (low for bullish, high for bearish).
    """
    if df.empty:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(len(df)):
        row = df.iloc[i]
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])

        candle_range = h - lo
        if candle_range == 0.0:
            continue

        body_pct = abs(c - o) / candle_range
        upper_wick_pct = (h - max(o, c)) / candle_range
        lower_wick_pct = (min(o, c) - lo) / candle_range

        open_time = int(row["open_time"])
        ctx = f"Trend Day: {_fmt_time(open_time)} body={body_pct:.0%}"

        if body_pct >= body_pct_min and lower_wick_pct <= wick_max and c > o:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "long",
                    "reason": f"trend_day_bull@{o:.2f}-{c:.2f}",
                    "sl_price": lo,
                    "context": ctx,
                }
            )
        elif body_pct >= body_pct_min and upper_wick_pct <= wick_max and c < o:
            signals.append(
                {
                    "open_time": open_time,
                    "direction": "short",
                    "reason": f"trend_day_bear@{o:.2f}-{c:.2f}",
                    "sl_price": h,
                    "context": ctx,
                }
            )

    return _signals_to_df(signals)
