"""NYSE trading-calendar wrapper — the ONLY module that imports ``exchange_calendars``.

Ported in shape from the wifey fork's ``analytics/trading_calendar.py`` (same library,
same ``XNYS`` code, same pinned major). This repo needs one thing the fork's copy does
not expose: each session's OPEN and CLOSE as UTC instants, daylight-saving aware and
with early closes (13:00 ET) applied, for H13's US-open anchor (#848).

No DB, no side effects beyond a process-lifetime cached calendar handle.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date
from typing import Any

import exchange_calendars as xcals
import pandas as pd

_CALENDAR_NAME = "XNYS"  # exchange_calendars' code for NYSE
# Explicit bounds so coverage is stable rather than the library's rolling window.
# The crypto 15m history starts 2019-09, so 2015 leaves margin on the left.
_CALENDAR_START = "2015-01-01"
_CALENDAR_END = "2030-12-31"


@functools.lru_cache(maxsize=1)
def _calendar() -> Any:
    """Process-lifetime cached XNYS calendar handle (construction is non-trivial)."""
    return xcals.get_calendar(_CALENDAR_NAME, start=_CALENDAR_START, end=_CALENDAR_END)


@dataclass(frozen=True)
class Session:
    """One NYSE trading session: its date and its open/close as UTC epoch ms."""

    day: date
    open_ms: int
    close_ms: int

    @property
    def early_close(self) -> bool:
        return self.close_ms - self.open_ms < 6.5 * 3_600_000


def nyse_sessions(start: date, end: date) -> list[Session]:
    """NYSE sessions in ``[start, end]`` inclusive, sorted ascending.

    Weekends, full holidays and special closures are absent; an early-close day is
    present with its 13:00 ET close. Out-of-range dates are clamped to the
    calendar's window rather than raising.
    """
    if end < start:
        return []
    cal = _calendar()
    lo = max(pd.Timestamp(start), cal.first_session)
    hi = min(pd.Timestamp(end), cal.last_session)
    if hi < lo:
        return []
    out: list[Session] = []
    for ts in cal.sessions_in_range(lo, hi):
        out.append(
            Session(
                day=ts.date(),
                open_ms=int(cal.session_open(ts).value // 1_000_000),
                close_ms=int(cal.session_close(ts).value // 1_000_000),
            )
        )
    return out
