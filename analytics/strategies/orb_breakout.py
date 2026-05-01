"""Detector: ORB Breakout — extracted from `analytics/indicators_lib.py` in strat-2.

No behaviour change. Function body byte-identical to pre-split source.
"""

import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df


def detect_orb_breakout(
    df: pd.DataFrame,
    range_candles: int = 2,
    # Legacy param kept so existing callers that pass session_hour_utc= don't crash.
    # It is intentionally ignored — the new implementation anchors on 00:00 UTC.
    session_hour_utc: int = 0,
    timeframe_minutes: int = 0,
) -> pd.DataFrame:
    """Detect Opening Range Breakout (ORB) signals.

    For 24/7 crypto futures the session anchor is 00:00 UTC (daily open).
    The opening range is defined by the first ``range_candles`` candles of each
    UTC calendar day (default 2).  A breakout signal fires on any subsequent
    candle within the same day that *closes* outside the range:

    * close > range_high  →  LONG  (SL = range_low)
    * close < range_low   →  SHORT (SL = range_high)

    TP is placed at entry ± 1.5 × range_width (stored in ``context``).
    Only one signal per day per direction is emitted (per-day dedup).

    Parameters
    ----------
    df:
        OHLCV DataFrame with at least ``open_time``, ``high``, ``low``,
        ``close`` columns.  ``open_time`` must be Unix milliseconds UTC.
    range_candles:
        Number of candles from 00:00 UTC that form the opening range (1–4).
    session_hour_utc:
        Ignored (kept for backwards-compatibility with old callers).
    timeframe_minutes:
        Ignored (kept for backwards-compatibility with old callers).
    """
    n = len(df)
    if n < range_candles + 1:
        return _empty_signals()

    dt_utc = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    dates = dt_utc.dt.date  # calendar date in UTC

    signals: list[dict[str, object]] = []
    # Track which (date, direction) pairs have already fired to avoid duplicates.
    fired: set[tuple[object, str]] = set()

    unique_dates = dates.unique()
    for day in unique_dates:
        day_mask = dates == day
        day_idx = df.index[day_mask].tolist()

        # Need at least range_candles + 1 candles on this day.
        if len(day_idx) < range_candles + 1:
            continue

        # Opening range = first range_candles candles of the day.
        range_rows = df.loc[day_idx[:range_candles]]
        range_high = float(range_rows["high"].max())
        range_low = float(range_rows["low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            continue

        range_open_ts = int(df.loc[day_idx[0]]["open_time"])
        range_ctx = (
            f"ORB range {_fmt_time(range_open_ts)} H:{range_high:.2f} L:{range_low:.2f}"
        )

        # Check every candle after the opening range window.
        for idx in day_idx[range_candles:]:
            row = df.loc[idx]
            close = float(row["close"])
            open_time_ms = int(row["open_time"])

            if close > range_high and (day, "long") not in fired:
                tp_price = close + range_width * 1.5
                signals.append(
                    {
                        "open_time": open_time_ms,
                        "direction": "long",
                        "reason": f"orb_long@{range_high:.2f}",
                        "sl_price": range_low,
                        "context": f"{range_ctx} TP:{tp_price:.2f}",
                    }
                )
                fired.add((day, "long"))

            elif close < range_low and (day, "short") not in fired:
                tp_price = close - range_width * 1.5
                signals.append(
                    {
                        "open_time": open_time_ms,
                        "direction": "short",
                        "reason": f"orb_short@{range_low:.2f}",
                        "sl_price": range_high,
                        "context": f"{range_ctx} TP:{tp_price:.2f}",
                    }
                )
                fired.add((day, "short"))

    return _signals_to_df(signals)
