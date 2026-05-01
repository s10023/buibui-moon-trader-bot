"""Detector: Fibonacci Golden Zone after BOS — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _find_bos_swing, _signals_to_df


def detect_fib_golden_zone(
    df: pd.DataFrame,
    swing_lookback: int = 20,
    bos_lookback: int = 5,
) -> pd.DataFrame:
    """Detect Fibonacci golden zone (0.5–0.618) entry after a confirmed BOS.

    Algorithm:
    1. Detect the most recent BOS within the last `swing_lookback` bars.
    2. Compute Fibonacci retracement levels from the BOS swing.
    3. Signal fires when the current candle close is inside the 0.5–0.618 band.

    LONG (bullish BOS — swing_low → swing_high → BOS above swing_high):
    - Entry zone: fib 0.618 ≤ close ≤ fib 0.5 (retracing down into golden zone).
    - SL: below the swing_low that defined the BOS leg.
    - TP: 1.618 extension above the swing_high.

    SHORT (bearish BOS — swing_high → swing_low → BOS below swing_low):
    - Entry zone: fib 0.5 ≤ close ≤ fib 0.618 (bouncing up into golden zone).
    - SL: above the swing_high that defined the BOS leg.
    - TP: 1.618 extension below the swing_low.
    """
    n = len(df)
    if n < swing_lookback + 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []
    closes = df["close"].to_numpy(dtype=float)
    open_times = df["open_time"].to_numpy(dtype=int)

    # Only evaluate the last candle (real-time use case: does the new bar enter the zone?)
    for sig_i in range(swing_lookback + 2, n):
        bos = _find_bos_swing(df.iloc[: sig_i + 1], swing_lookback, bos_lookback)
        if bos is None:
            continue

        sl_price_bos, sh_price_bos, direction = bos
        swing_range = sh_price_bos - sl_price_bos
        if swing_range <= 0.0:
            continue

        curr_close = closes[sig_i]
        open_time = open_times[sig_i]

        if direction == "long":
            # Retracement from sh_price_bos downward
            fib_0_5 = sh_price_bos - 0.5 * swing_range
            fib_0_618 = sh_price_bos - 0.618 * swing_range
            # SL: below the swing_low (the anchor of the bullish leg)
            sl_out = sl_price_bos
            # TP: 1.618 extension above swing_high
            tp = sh_price_bos + 0.618 * swing_range
            if fib_0_5 >= curr_close >= fib_0_618:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "long",
                        "reason": (
                            f"fib_golden_zone_bos@{curr_close:.2f} "
                            f"(0.618={fib_0_618:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"BOS: swing_low={sl_price_bos:.2f} "
                            f"swing_high={sh_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

        else:  # short
            # Retracement from sl_price_bos upward
            fib_0_5 = sl_price_bos + 0.5 * swing_range
            fib_0_618 = sl_price_bos + 0.618 * swing_range
            # SL: above the swing_high
            sl_out = sh_price_bos
            # TP: 1.618 extension below swing_low
            tp = sl_price_bos - 0.618 * swing_range
            if fib_0_618 >= curr_close >= fib_0_5:
                signals.append(
                    {
                        "open_time": open_time,
                        "direction": "short",
                        "reason": (
                            f"fib_golden_zone_bos@{curr_close:.2f} "
                            f"(0.618={fib_0_618:.2f})"
                        ),
                        "sl_price": sl_out,
                        "tp_price": tp,
                        "context": (
                            f"BOS: swing_high={sh_price_bos:.2f} "
                            f"swing_low={sl_price_bos:.2f} "
                            f"TP={tp:.2f} (1.618 ext)"
                        ),
                    }
                )

    return _signals_to_df(signals)
