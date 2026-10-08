"""Chart-tab location overlays: anchored VWAP series + volume-profile levels.

DISPLAY ONLY, NEVER A GATE. Nothing here may gate, size or suppress a signal,
a card or an order; ``tests/test_chart_location.py`` pins that no detector,
gate, sizing or execution module imports this file. The filed brief+card
verdict is "decision hygiene, no alpha", and price LOCATION drawn on a chart
is not a second edge (issue #822).

This is rendering, not new math. Every VWAP point is the value
``analytics.indicators.anchored_vwap`` returns on the bars up to and
including that point, with the anchor the brief uses (UTC day, Monday 00:00
UTC week, first-of-month). The series is built from running sums so a 15m
chart stays linear-time, and a parity test holds it to the scalar function
at every bar. Causality follows from that construction: the value at bar
``i`` reads bars ``<= i`` only, so truncating the input never changes an
earlier point. The profile levels reuse ``build_profile`` + ``value_area``
over the brief's 60-day window of COMPLETED 1h bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple

import pandas as pd

from analytics.brief._common import DAY_MS, completed_bars
from analytics.volume_profile import build_profile, value_area

DISPLAY_ONLY_MARKER = "display only, never a gate"

Anchor = Literal["day", "week", "month"]

# The brief's volume-profile window (analytics/brief/indicators.py::_PROFILE_DAYS).
# Restated rather than imported because that name is private; a test pins parity.
PROFILE_DAYS = 60

# Anchors drawn per chart timeframe. A day-anchored VWAP on 1d bars is just
# each bar's own typical price, so it is not offered there.
ANCHORS_BY_TIMEFRAME: dict[str, tuple[Anchor, ...]] = {
    "15m": ("day", "week", "month"),
    "1h": ("day", "week", "month"),
    "4h": ("day", "week", "month"),
    "1d": ("week", "month"),
}

# 1970-01-01 was a Thursday: Monday-based weekday of epoch day 0 is 3.
_EPOCH_WEEKDAY = 3


class VwapValue(NamedTuple):
    time_ms: int  # bar open_time
    anchor_ms: int  # start of the window this value is anchored on
    value: float


@dataclass(frozen=True)
class ProfileLevels:
    poc: float
    vah: float
    val: float
    window_start_ms: int
    window_end_ms: int


def anchor_ms(open_time: pd.Series, anchor: Anchor) -> pd.Series:
    """Start (Unix ms, UTC) of the day / week / month each bar opens in.

    Same rules as the brief: UTC midnight, Monday 00:00 UTC, the 1st 00:00 UTC.
    """
    t = open_time.astype("int64")
    day = (t // DAY_MS) * DAY_MS
    if anchor == "day":
        return day
    if anchor == "week":
        weekday = ((day // DAY_MS) + _EPOCH_WEEKDAY) % 7
        return day - weekday * DAY_MS
    ts = pd.to_datetime(day, unit="ms", utc=True)
    month_start = ts - pd.to_timedelta(ts.dt.day - 1, unit="D")
    return (month_start.dt.tz_localize(None) - pd.Timestamp(0)) // pd.Timedelta(
        milliseconds=1
    )


def earliest_anchor_ms(start_ms: int) -> int:
    """The month anchor covering ``start_ms`` — the widest anchor any series needs.

    Fetching from here means the first partial window on screen is anchored on
    its real start rather than on whatever bar the chart range happened to begin.
    """
    return int(anchor_ms(pd.Series([start_ms]), "month").iloc[0])


def anchored_vwap_series(df: pd.DataFrame, anchor: Anchor) -> list[VwapValue]:
    """One value per bar; bars whose window has no volume yet are omitted.

    ``df`` must be sorted by ``open_time``. Each value equals
    ``anchored_vwap(df[df.open_time <= t], anchor_of(t))``.
    """
    if df.empty:
        return []
    open_time = df["open_time"].astype("int64").reset_index(drop=True)
    vol = df["volume"].astype(float).reset_index(drop=True)
    typical = (
        (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float))
        / 3.0
    ).reset_index(drop=True)
    group = anchor_ms(open_time, anchor)
    cum_pv = (typical * vol).groupby(group).cumsum()
    cum_v = vol.groupby(group).cumsum()
    out: list[VwapValue] = []
    for t, a, pv, v in zip(open_time, group, cum_pv, cum_v, strict=True):
        if v > 0.0:
            out.append(VwapValue(int(t), int(a), float(pv / v)))
    return out


def profile_window_start_ms(end_ms: int) -> int:
    """First open_time inside the profile window ending at ``end_ms``."""
    return end_ms - PROFILE_DAYS * DAY_MS


def profile_levels(hourly_df: pd.DataFrame, end_ms: int) -> ProfileLevels | None:
    """POC / VAH / VAL over the brief's window of completed 1h bars ending ``end_ms``.

    The forming hour is dropped (``completed_bars``), so a level never moves
    on a bar that has not closed.
    """
    window_start = profile_window_start_ms(end_ms)
    done = completed_bars(hourly_df, "1h", end_ms)
    if done.empty:
        return None
    window = done[done["open_time"] >= window_start]
    profile = build_profile(window)
    if profile is None:
        return None
    poc, vah, val = value_area(profile)
    return ProfileLevels(
        poc=poc,
        vah=vah,
        val=val,
        window_start_ms=int(window["open_time"].iloc[0]),
        window_end_ms=int(window["open_time"].iloc[-1]),
    )
