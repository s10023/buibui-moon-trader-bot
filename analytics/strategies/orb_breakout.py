"""Detector: ORB Breakout — extracted from `analytics/indicators_lib.py` in strat-2.

The default ``anchor="utc_midnight"`` path is the shipped detector, unchanged.
``anchor="nyse_open"`` is H13's US-open construction (#848, pre-registration
``docs/superpowers/specs/2026-10-08-h13-us-open-orb-preregistration.md`` §3): no live
config selects it.
"""

from typing import Literal

import numpy as np
import pandas as pd

from analytics.strategies._shared import _empty_signals, _fmt_time, _signals_to_df
from analytics.trading_calendar import nyse_sessions

OrbAnchor = Literal["utc_midnight", "nyse_open"]


def detect_orb_breakout(
    df: pd.DataFrame,
    range_candles: int = 2,
    # Legacy param kept so existing callers that pass session_hour_utc= don't crash.
    # It is intentionally ignored — the anchor is chosen by ``anchor`` below.
    session_hour_utc: int = 0,
    timeframe_minutes: int = 0,
    *,
    anchor: OrbAnchor = "utc_midnight",
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
    anchor:
        ``"utc_midnight"`` (default, shipped) or ``"nyse_open"`` — see
        :func:`_detect_nyse_open`.
    """
    if anchor == "nyse_open":
        return _detect_nyse_open(df, range_candles)
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


def _detect_nyse_open(df: pd.DataFrame, range_candles: int) -> pd.DataFrame:
    """ORB anchored at each NYSE session's cash open (H13 §3).

    * Days: NYSE sessions only (``analytics.trading_calendar``) — holidays have no
      anchor; an early close keeps its 13:00 ET close.
    * Range: the ``range_candles`` bars opening at the cash open (09:30 ET, so 13:30
      or 14:30 UTC with daylight saving). A day missing any range bar is skipped,
      which also makes a timeframe that does not align to 09:30 emit nothing.
    * Window: bars after the range whose CLOSE is strictly before the session close.
      The engine fills on the next bar's open, so a signal on the bar that closes AT
      the session close would enter at the close and be flattened by the time exit
      in the same instant — no trade, only cost.
    * Fire rule, stop and ``context`` TP text are the shipped detector's.
    """
    times = df["open_time"].to_numpy(dtype=np.int64)
    if len(times) < range_candles + 1:
        return _empty_signals()
    bar_ms = int(np.median(np.diff(times)))
    pos = {int(t): i for i, t in enumerate(times)}
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    first = pd.Timestamp(int(times[0]), unit="ms", tz="UTC").date()
    last = pd.Timestamp(int(times[-1]), unit="ms", tz="UTC").date()

    signals: list[dict[str, object]] = []
    for session in nyse_sessions(first, last):
        range_pos = [
            pos.get(session.open_ms + k * bar_ms) for k in range(range_candles)
        ]
        if any(p is None for p in range_pos):
            continue
        idx = [p for p in range_pos if p is not None]
        range_high = float(highs[idx].max())
        range_low = float(lows[idx].min())
        range_width = range_high - range_low
        if range_width <= 0:
            continue
        range_ctx = f"ORB range {_fmt_time(session.open_ms)} H:{range_high:.2f} L:{range_low:.2f}"

        fired: set[str] = set()
        t = session.open_ms + range_candles * bar_ms
        while t + bar_ms < session.close_ms:
            i = pos.get(t)
            t += bar_ms
            if i is None:
                continue
            close = float(closes[i])
            open_time_ms = int(times[i])
            if close > range_high and "long" not in fired:
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
                fired.add("long")
            elif close < range_low and "short" not in fired:
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
                fired.add("short")

    return _signals_to_df(signals)
