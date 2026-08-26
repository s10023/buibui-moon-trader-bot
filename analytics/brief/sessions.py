"""Per-symbol session state for the brief panel (M2 adapter).

Conn-free: consumes the completed-1h frame the bundle already fetched and
a precomputed ``SessionResult`` (the bundle owns the DB call). Recap and
tendency degrade independently: a failed/empty half -> None + an
UNPREFIXED note (the bundle adds the symbol); both None collapses the
whole state to None.
"""

from __future__ import annotations

import pandas as pd

from analytics.brief.types import SessionRecapRow, SessionState, SessionTendencyRow
from analytics.session_windows import (
    last_completed_windows,
    myt_date_str,
    myt_day_offset,
)
from analytics.stats.session import SessionResult

_HOUR_MS = 3_600_000
_RECAP_WINDOWS = 3


def _recap_rows(
    completed_1h: pd.DataFrame, atr14: float, as_of_ms: int
) -> tuple[list[SessionRecapRow] | None, list[str]]:
    if completed_1h.empty:  # also covers a column-less empty frame
        return None, ["session recap: no 1h bars in any window"]
    windows = last_completed_windows(as_of_ms, n=_RECAP_WINDOWS)
    raw: list[tuple[str, int, int, float, float, float, float, int]] = []
    empty: list[str] = []
    for win in windows:
        sliced = completed_1h[
            (completed_1h["open_time"] >= win.start_ms)
            & (completed_1h["open_time"] < win.end_ms)
        ]
        if sliced.empty:
            empty.append(win.label)
            continue
        raw.append(
            (
                win.label,
                win.start_ms,
                win.end_ms,
                float(sliced.iloc[0]["open"]),
                float(sliced["high"].max()),
                float(sliced["low"].min()),
                float(sliced.iloc[-1]["close"]),
                len(sliced),
            )
        )
    if not raw:
        return None, ["session recap: no 1h bars in any window"]
    notes = [f"session recap: no 1h bars for {label} window" for label in empty]
    # Set extremes over the rows present; earliest wins ties (max/min pick
    # the first of equals), deterministic.
    hi_idx = max(range(len(raw)), key=lambda i: raw[i][4])
    lo_idx = min(range(len(raw)), key=lambda i: raw[i][5])
    rows = [
        SessionRecapRow(
            session=label,
            start_ms=start_ms,
            end_ms=end_ms,
            # Anchored on the window's START: an NY window opens 22:00 MYT and
            # closes 04:00 the next day, and it is named for the day it opened.
            date_myt=myt_date_str(start_ms),
            day_offset=myt_day_offset(start_ms, as_of_ms),
            open=o,
            high=h,
            low=lo,
            close=c,
            net_pct=(c - o) / o * 100.0,
            net_atr=(c - o) / atr14 if atr14 > 0 else None,
            range_atr=(h - lo) / atr14 if atr14 > 0 else None,
            n_bars=n_bars,
            expected_bars=(end_ms - start_ms) // _HOUR_MS,
            made_set_high=i == hi_idx,
            made_set_low=i == lo_idx,
        )
        for i, (label, start_ms, end_ms, o, h, lo, c, n_bars) in enumerate(raw)
    ]
    return rows, notes


def _tendency_rows(
    tendency: SessionResult | None,
) -> list[SessionTendencyRow] | None:
    if tendency is None:
        return None
    return [
        SessionTendencyRow(session=r.session, high_pct=r.high_pct, low_pct=r.low_pct)
        for r in tendency.rows
    ]


def build_session_state(
    completed_1h: pd.DataFrame,
    atr14: float,
    as_of_ms: int,
    tendency: SessionResult | None,
) -> tuple[SessionState | None, list[str]]:
    """(state, unprefixed notes); state is None only when BOTH halves are."""
    recap, notes = _recap_rows(completed_1h, atr14, as_of_ms)
    tendency_rows = _tendency_rows(tendency)
    if recap is None and tendency_rows is None:
        return None, notes
    return SessionState(recap=recap, tendency=tendency_rows), notes
