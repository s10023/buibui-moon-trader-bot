"""Detector: Marubozu Retest — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_marubozu_retest(
    df: pd.DataFrame,
    max_wick_ratio: float = 0.1,
    lookback: int = 30,
    min_body_pct: float = 0.005,
) -> pd.DataFrame:
    """Detect retests of Marubozu (wickless) candle open prices.

    A Marubozu is a candle where both wicks are <= max_wick_ratio × body.
    The open of a bullish Marubozu acts as support (order block).
    The open of a bearish Marubozu acts as resistance (supply zone).

    Signal fires when a later candle retests the open price zone.

    min_body_pct: suppress Marubozus where body / open_price < min_body_pct.
    Default 0.005 (0.5%) filters out small indecisive candles.
    Note: SL is placed at the wick tip (very tight); this filter reduces
    stop-outs from low-volatility candles where noise exceeds SL distance.
    """
    n = len(df)
    if n < 2:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    for i in range(n - 1):
        row = df.iloc[i]
        row_open = float(row["open"])
        row_close = float(row["close"])
        row_high = float(row["high"])
        row_low = float(row["low"])

        body = abs(row_close - row_open)
        if body == 0.0:
            continue
        if body < min_body_pct * row_open:
            continue

        upper_wick = row_high - max(row_open, row_close)
        lower_wick = min(row_open, row_close) - row_low
        is_marubozu = (
            upper_wick <= max_wick_ratio * body and lower_wick <= max_wick_ratio * body
        )
        if not is_marubozu:
            continue

        is_bullish = row_close > row_open
        end = min(i + lookback + 1, n)

        maru_ctx = f"Marubozu: {_fmt_time(int(row['open_time']))}"
        if is_bullish:
            support = row_open
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= support and float(fut["close"]) > support:
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "long",
                            "reason": f"marubozu_long@{support:.2f}",
                            "sl_price": row_low,
                            "context": maru_ctx,
                        }
                    )
                    break
        else:
            resistance = row_open
            for j in range(i + 1, end):
                fut = df.iloc[j]
                if (
                    float(fut["high"]) >= resistance
                    and float(fut["close"]) < resistance
                ):
                    signals.append(
                        {
                            "open_time": int(fut["open_time"]),
                            "direction": "short",
                            "reason": f"marubozu_short@{resistance:.2f}",
                            "sl_price": row_high,
                            "context": maru_ctx,
                        }
                    )
                    break

    return _signals_to_df(signals)
