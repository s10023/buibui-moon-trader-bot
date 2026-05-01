"""Detector: Fair Value Gap (FVG) — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_fvg(
    df: pd.DataFrame,
    lookback: int = 50,
    min_gap_pct: float = 0.001,
    trend_filter: int = 1,
) -> pd.DataFrame:
    """Detect Fair Value Gap (3-candle imbalance) fill signals.

    Bullish FVG: candle[i-1].high < candle[i+1].low — gap up imbalance.
    Bearish FVG: candle[i-1].low > candle[i+1].high — gap down imbalance.

    Signal fires on the first candle within lookback that enters the FVG zone.
    Long = price fills bullish FVG. Short = price fills bearish FVG.

    min_gap_pct: suppress FVGs whose size is < min_gap_pct * midpoint price.
    Default 0.001 (0.1%) filters out tiny imbalances that are noise.

    trend_filter: 1 (default) enables EMA-50 trend gate — long FVGs only fire
    when close > EMA-50; short FVGs only fire when close < EMA-50. Set to 0 to
    disable and allow signals regardless of trend direction.
    """
    n = len(df)
    if n < 3:
        return _empty_signals()

    signals: list[dict[str, object]] = []

    ema50 = df["close"].ewm(span=50, adjust=False).mean()

    for i in range(1, n - 1):
        prev_high = float(df.iloc[i - 1]["high"])
        prev_low = float(df.iloc[i - 1]["low"])
        nxt_low = float(df.iloc[i + 1]["low"])
        nxt_high = float(df.iloc[i + 1]["high"])

        end = min(i + 2 + lookback, n)

        fvg_ctx = (
            f"Gap: {_fmt_time(int(df.iloc[i - 1]['open_time']))} · "
            f"{_fmt_time(int(df.iloc[i]['open_time']))} · "
            f"{_fmt_time(int(df.iloc[i + 1]['open_time']))}"
        )

        if prev_high < nxt_low:
            gap_bot = prev_high
            gap_top = nxt_low
            mid_price = (gap_bot + gap_top) / 2
            if (gap_top - gap_bot) < min_gap_pct * mid_price:
                continue
            ce = (gap_bot + gap_top) / 2
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["low"]) <= ce and float(fut["close"]) > gap_bot:
                    if trend_filter == 0 or float(fut["close"]) > float(ema50.iloc[j]):
                        signals.append(
                            {
                                "open_time": int(fut["open_time"]),
                                "direction": "long",
                                "reason": f"fvg_long@{gap_bot:.2f}-{gap_top:.2f}",
                                "sl_price": gap_bot,
                                "context": fvg_ctx,
                            }
                        )
                    break

        if prev_low > nxt_high:
            gap_top = prev_low
            gap_bot = nxt_high
            mid_price = (gap_bot + gap_top) / 2
            if (gap_top - gap_bot) < min_gap_pct * mid_price:
                continue
            ce = (gap_bot + gap_top) / 2
            for j in range(i + 2, end):
                fut = df.iloc[j]
                if float(fut["high"]) >= ce and float(fut["close"]) < gap_top:
                    if trend_filter == 0 or float(fut["close"]) < float(ema50.iloc[j]):
                        signals.append(
                            {
                                "open_time": int(fut["open_time"]),
                                "direction": "short",
                                "reason": f"fvg_short@{gap_top:.2f}-{gap_bot:.2f}",
                                "sl_price": gap_top,
                                "context": fvg_ctx,
                            }
                        )
                    break

    return _signals_to_df(signals)
