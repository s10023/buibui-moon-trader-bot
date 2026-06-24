"""Calendar/structural reference levels — look-ahead-safe geometry.

Pure functions that compute horizontal reference levels (period opens, the
Monday range, and previous-period extremes) from 1d OHLCV, anchored to an entry
timestamp. Every level uses only **completed prior periods**; current-period
*opens* are fixed at the period's open, so they are known intra-period without
look-ahead. Sibling to ``analytics/zones_lib.py`` (swing-point geometry);
reusable by a future ``reference_level`` detector.

Week convention: Monday 00:00 UTC (= Monday 08:00 MYT). Weekly Open == Monday
Open in 24/7 crypto, so a single ``WO`` level covers both.

Levels (``LEVEL_NAMES``):

- ``MO`` / ``WO`` / ``DO`` — current month / week / day open
- ``MonH`` / ``MonL`` — this week's Monday high / low (``None`` when the entry is
  itself on Monday — the range is still forming)
- ``PDH`` / ``PDL`` — previous day high / low
- ``PWH`` / ``PWL`` — previous ISO-week high / low
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

LEVEL_NAMES: tuple[str, ...] = (
    "MO",
    "WO",
    "DO",
    "MonH",
    "MonL",
    "PDH",
    "PDL",
    "PWH",
    "PWL",
)


def _scalar_or_none(frame: pd.DataFrame, col: str) -> float | None:
    """First value of ``frame[col]`` as a float, or ``None`` when empty."""
    if frame.empty:
        return None
    return float(frame[col].iloc[0])


def compute_levels(
    daily_ohlcv: pd.DataFrame, entry_ts_ms: int
) -> dict[str, float | None]:
    """Reference levels known at ``entry_ts_ms`` from 1d OHLCV (UTC).

    ``daily_ohlcv`` columns: ``open_time`` (Unix ms), ``open``/``high``/``low``.
    A level is ``None`` when its source period is absent from the frame or when
    it is excluded by the look-ahead rule (Monday range on a Monday entry).
    """
    if daily_ohlcv.empty:
        empty: dict[str, float | None] = {}
        for name in LEVEL_NAMES:
            empty[name] = None
        return empty

    df = daily_ohlcv.copy()
    df["_date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.normalize()

    ts = pd.Timestamp(entry_ts_ms, unit="ms", tz="UTC")
    entry_date = ts.normalize()
    week_monday = entry_date - pd.Timedelta(days=int(entry_date.weekday()))
    prev_week_monday = week_monday - pd.Timedelta(days=7)
    month_first = entry_date.replace(day=1)

    def open_on(date: pd.Timestamp) -> float | None:
        return _scalar_or_none(df.loc[df["_date"] == date], "open")

    # Current-period opens — fixed at period start, known intra-period.
    levels: dict[str, float | None] = {
        "MO": open_on(month_first),
        "WO": open_on(week_monday),
        "DO": open_on(entry_date),
    }

    # Previous day extremes — most recent completed day strictly before entry day.
    prev_days = df.loc[df["_date"] < entry_date]
    if prev_days.empty:
        levels["PDH"] = levels["PDL"] = None
    else:
        last_prev = prev_days.loc[prev_days["_date"] == prev_days["_date"].max()]
        levels["PDH"] = float(last_prev["high"].iloc[0])
        levels["PDL"] = float(last_prev["low"].iloc[0])

    # This week's Monday range — only once Monday has completed.
    if entry_date == week_monday:
        levels["MonH"] = levels["MonL"] = None
    else:
        mon_row = df.loc[df["_date"] == week_monday]
        levels["MonH"] = _scalar_or_none(mon_row, "high")
        levels["MonL"] = _scalar_or_none(mon_row, "low")

    # Previous ISO-week extremes (Mon..Sun, fully completed before this week).
    prev_week = df.loc[(df["_date"] >= prev_week_monday) & (df["_date"] < week_monday)]
    if prev_week.empty:
        levels["PWH"] = levels["PWL"] = None
    else:
        levels["PWH"] = float(prev_week["high"].max())
        levels["PWL"] = float(prev_week["low"].min())

    return levels


def compute_levels_table(daily_ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Vectorized per-day level table — one row per calendar day, indexed by date.

    Each row holds the levels *as of an entry on that day*, identical to
    ``compute_levels(daily_ohlcv, <noon that day>)`` but computed in one pass so
    the audit can tag hundreds of thousands of trades cheaply. Undefined levels
    are ``NaN`` (the ``None`` of the scalar form). Columns are ``LEVEL_NAMES``.
    """
    if daily_ohlcv.empty:
        return pd.DataFrame(columns=list(LEVEL_NAMES))

    d = daily_ohlcv.copy()
    d["_date"] = pd.to_datetime(d["open_time"], unit="ms", utc=True).dt.normalize()
    d = d.sort_values("_date").reset_index(drop=True)

    weekday = d["_date"].dt.weekday
    week_monday = d["_date"] - pd.to_timedelta(weekday, unit="D")
    month_first = d["_date"] - pd.to_timedelta(d["_date"].dt.day - 1, unit="D")

    open_by_date = d.set_index("_date")["open"]
    high_by_date = d.set_index("_date")["high"]
    low_by_date = d.set_index("_date")["low"]

    table = pd.DataFrame(index=pd.Index(d["_date"], name="date"))
    table["MO"] = month_first.map(open_by_date).to_numpy()
    table["WO"] = week_monday.map(open_by_date).to_numpy()
    table["DO"] = d["open"].to_numpy()

    mon_h = week_monday.map(high_by_date).to_numpy()
    mon_l = week_monday.map(low_by_date).to_numpy()
    is_monday = (weekday == 0).to_numpy()
    table["MonH"] = np.where(is_monday, np.nan, mon_h)
    table["MonL"] = np.where(is_monday, np.nan, mon_l)

    table["PDH"] = d["high"].shift(1).to_numpy()
    table["PDL"] = d["low"].shift(1).to_numpy()

    week_high = d.groupby(week_monday)["high"].max()
    week_low = d.groupby(week_monday)["low"].min()
    prev_week = week_monday - pd.to_timedelta(7, unit="D")
    table["PWH"] = prev_week.map(week_high).to_numpy()
    table["PWL"] = prev_week.map(week_low).to_numpy()

    return table[list(LEVEL_NAMES)]


def nearest_level(
    price: float, levels: Mapping[str, float | None]
) -> tuple[str, float]:
    """Name + absolute distance of the level closest to ``price``.

    Skips ``None`` levels. Returns ``("", inf)`` when no level is defined.
    """
    best_name = ""
    best_dist = math.inf
    for name, lvl in levels.items():
        if lvl is None:
            continue
        dist = abs(price - lvl)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name, best_dist


def sweep_flag(
    tf_ohlcv: pd.DataFrame,
    entry_idx: int,
    level_price: float,
    direction: str,
    lookback: int = 3,
) -> bool:
    """Did price sweep ``level_price`` and reclaim/reject by the entry candle?

    ``long`` — a bar in the last ``lookback`` candles (ending at ``entry_idx``)
    wicked **below** the level and the entry candle closes back **above** it.
    ``short`` — mirror: a recent bar wicked **above** and the entry closes
    **below**. Uses only candles up to and including ``entry_idx`` (no look-ahead).
    """
    if entry_idx < 0 or entry_idx >= len(tf_ohlcv):
        return False
    lo = max(0, entry_idx - lookback + 1)
    window = tf_ohlcv.iloc[lo : entry_idx + 1]
    entry_close = float(tf_ohlcv.iloc[entry_idx]["close"])
    if direction == "long":
        wicked = bool((window["low"] < level_price).any())
        return wicked and entry_close > level_price
    wicked = bool((window["high"] > level_price).any())
    return wicked and entry_close < level_price
