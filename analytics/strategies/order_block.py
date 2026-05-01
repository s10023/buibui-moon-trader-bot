"""Detector: ICT Order Block — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_order_block(
    df: pd.DataFrame,
    lookback: int = 100,
    displacement_pct: float = 0.003,
) -> pd.DataFrame:
    """Detect ICT Order Block retest signals.

    Bearish OB: the last bullish candle (close > open) immediately before a
    significant bearish displacement candle (close < ob_low × (1 - displacement_pct)).
    Short signal fires on the first candle that retests the OB zone from below
    (candle enters [ob_open, ob_close]) after the displacement.
    SL = ob candle high.

    Bullish OB: the last bearish candle (open > close) immediately before a
    significant bullish displacement candle (close > ob_high × (1 + displacement_pct)).
    Long signal fires on the first candle that retests the OB zone from above
    (candle enters [ob_close, ob_open]) after the displacement.
    SL = ob candle low.

    All candles are scanned for OB formation (full history).
    `lookback` — max candles forward to search for a retest after the OB forms.
    This prevents stale OBs from generating signals months after formation.
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    opens = df["open"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    signals: list[dict[str, object]] = []

    for i in range(n - 2):
        ob_open = opens[i]
        ob_high = highs[i]
        ob_low = lows[i]
        ob_close = closes[i]
        ob_time = open_times[i]
        disp_close = closes[i + 1]

        # --- Bearish OB: bullish candle followed by bearish displacement ---
        if ob_close > ob_open and disp_close < ob_low * (1 - displacement_pct):
            ob_zone_bot = ob_open
            ob_zone_top = ob_close
            ctx = f"Bearish OB: {_fmt_time(ob_time)} [{ob_zone_bot:,.2f}–{ob_zone_top:,.2f}]"
            retest_end = min(i + 2 + lookback, n)
            for j in range(i + 2, retest_end):
                # Retest: candle enters OB zone and closes below zone top
                if (
                    highs[j] >= ob_zone_bot
                    and lows[j] <= ob_zone_top
                    and closes[j] < ob_zone_top
                ):
                    signals.append(
                        {
                            "open_time": open_times[j],
                            "direction": "short",
                            "reason": f"ob_short@{ob_zone_bot:.2f}-{ob_zone_top:.2f}",
                            "sl_price": ob_high,
                            "context": ctx,
                        }
                    )
                    break  # one signal per OB

        # --- Bullish OB: bearish candle followed by bullish displacement ---
        elif ob_open > ob_close and disp_close > ob_high * (1 + displacement_pct):
            ob_zone_bot = ob_close
            ob_zone_top = ob_open
            ctx = f"Bullish OB: {_fmt_time(ob_time)} [{ob_zone_bot:,.2f}–{ob_zone_top:,.2f}]"
            retest_end = min(i + 2 + lookback, n)
            for j in range(i + 2, retest_end):
                # Retest: candle enters OB zone and closes above zone bot
                if (
                    lows[j] <= ob_zone_top
                    and highs[j] >= ob_zone_bot
                    and closes[j] > ob_zone_bot
                ):
                    signals.append(
                        {
                            "open_time": open_times[j],
                            "direction": "long",
                            "reason": f"ob_long@{ob_zone_bot:.2f}-{ob_zone_top:.2f}",
                            "sl_price": ob_low,
                            "context": ctx,
                        }
                    )
                    break  # one signal per OB

    return _signals_to_df(signals)
