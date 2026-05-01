"""Detector: Wick Fill — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_wick_fills(
    df: pd.DataFrame,
    min_wick_body_ratio: float = 1.5,
    lookback: int = 20,
) -> pd.DataFrame:
    """Detect candles where price fills a prior significant wick zone.

    A significant wick must be at least min_wick_body_ratio × the candle body.
    Signals on the first candle within lookback that enters the wick zone.

    Long = fills a lower wick zone (bullish).
    Short = fills an upper wick zone (bearish).
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        row = df.iloc[i]
        body = abs(float(row["close"]) - float(row["open"]))
        if body == 0.0:
            continue

        candle_open = float(row["open"])
        candle_close = float(row["close"])
        candle_high = float(row["high"])
        candle_low = float(row["low"])

        upper_wick = candle_high - max(candle_open, candle_close)
        lower_wick = min(candle_open, candle_close) - candle_low

        end = min(i + lookback + 1, n)

        if lower_wick >= min_wick_body_ratio * body:
            zone_top = min(candle_open, candle_close)
            zone_bot = candle_low
            wick_ctx = f"Wick: {_fmt_time(int(row['open_time']))}"
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= zone_top and float(fut["close"]) > zone_bot:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "long",
                            "reason": f"wick_fill_long@{zone_bot:.2f}-{zone_top:.2f}",
                            "sl_price": zone_bot,
                            "context": wick_ctx,
                        }
                    )
                    break

        if upper_wick >= min_wick_body_ratio * body:
            zone_bot = max(candle_open, candle_close)
            zone_top = candle_high
            wick_ctx = f"Wick: {_fmt_time(int(row['open_time']))}"
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= zone_bot and float(fut["close"]) < zone_top:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "short",
                            "reason": f"wick_fill_short@{zone_bot:.2f}-{zone_top:.2f}",
                            "sl_price": zone_top,
                            "context": wick_ctx,
                        }
                    )
                    break

    return _signals_to_df(signals)
