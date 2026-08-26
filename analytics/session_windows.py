"""Session calendar math (MYT convention) — pure, no DB, no pandas.

The partition mirrors the SQL CASE in ``analytics/stats/session.py``
applied as a bar classification, in MYT (UTC+8, no DST):

    Asia   [08:00, 14:00)
    London [14:00, 22:00)   20:00-21:59 also matches the docstring NY
                            window; the CASE credits it to London — kept
                            here as the ``is_overlap`` display flag
    NY     [22:00, 04:00)   crosses midnight
    Off    [04:00, 08:00)

Windows are half-open ``[start_ms, end_ms)`` in Unix ms UTC. Everything
derives from ``as_of_ms`` alone — data availability is the caller's
concern.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

MYT_OFFSET_MS = 8 * 3_600_000
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000

# (label, start_hour, end_hour) in MYT; NY end 28 = 04:00 the next day.
_DAY_SESSIONS: tuple[tuple[str, int, int], ...] = (
    ("Asia", 8, 14),
    ("London", 14, 22),
    ("NY", 22, 28),
)


@dataclass(frozen=True)
class SessionWindow:
    label: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class CurrentSession:
    label: str  # "Asia" | "London" | "NY" | "Off"
    start_ms: int
    end_ms: int
    is_overlap: bool  # London 20:00-21:59 MYT (the docstring NY overlap)
    next_label: str
    next_start_ms: int


def _myt_day_start_ms(as_of_ms: int) -> int:
    """UTC ms of 00:00 MYT of the MYT day containing ``as_of_ms``."""
    shifted = as_of_ms + MYT_OFFSET_MS
    return (shifted // _DAY_MS) * _DAY_MS - MYT_OFFSET_MS


def _window(day_start_ms: int, label: str, start_h: int, end_h: int) -> SessionWindow:
    return SessionWindow(
        label=label,
        start_ms=day_start_ms + start_h * _HOUR_MS,
        end_ms=day_start_ms + end_h * _HOUR_MS,
    )


def myt_date_str(as_of_ms: int) -> str:
    """ISO ``YYYY-MM-DD`` of the MYT day containing ``as_of_ms``."""
    stamp = dt.datetime(1970, 1, 1, tzinfo=dt.UTC) + dt.timedelta(
        milliseconds=as_of_ms + MYT_OFFSET_MS
    )
    return stamp.strftime("%Y-%m-%d")


def myt_day_offset(ms: int, as_of_ms: int) -> int:
    """Whole MYT days from ``as_of_ms``'s day to ``ms``'s day.

    ``0`` is the same MYT day, ``-1`` the day before. Both sides are
    snapped to their MYT day start first, so this counts calendar days
    rather than elapsed hours: 23:59 and 00:01 either side of a midnight
    are one day apart, not zero.
    """
    return (_myt_day_start_ms(ms) - _myt_day_start_ms(as_of_ms)) // _DAY_MS


def session_at(as_of_ms: int) -> CurrentSession:
    """The session containing ``as_of_ms`` + what comes next."""
    day = _myt_day_start_ms(as_of_ms)
    hour = ((as_of_ms + MYT_OFFSET_MS) % _DAY_MS) // _HOUR_MS
    if 8 <= hour < 14:
        win = _window(day, "Asia", 8, 14)
        nxt, nxt_start = "London", win.end_ms
    elif 14 <= hour < 22:
        win = _window(day, "London", 14, 22)
        nxt, nxt_start = "NY", win.end_ms
    elif hour >= 22:
        win = _window(day, "NY", 22, 28)
        nxt, nxt_start = "Asia", win.end_ms + 4 * _HOUR_MS
    elif hour < 4:  # early hours belong to the PREVIOUS day's NY window
        win = _window(day - _DAY_MS, "NY", 22, 28)
        nxt, nxt_start = "Asia", win.end_ms + 4 * _HOUR_MS
    else:  # 4 <= hour < 8
        win = _window(day, "Off", 4, 8)
        nxt, nxt_start = "Asia", win.end_ms
    return CurrentSession(
        label=win.label,
        start_ms=win.start_ms,
        end_ms=win.end_ms,
        is_overlap=win.label == "London" and hour >= 20,
        next_label=nxt,
        next_start_ms=nxt_start,
    )


def last_completed_windows(as_of_ms: int, n: int = 3) -> list[SessionWindow]:
    """The ``n`` most recent trading windows fully completed at ``as_of_ms``.

    Completed means ``end_ms <= as_of_ms`` (the boundary counts, matching
    ``completed_bars``). Off windows never count. Chronological order.
    """
    day = _myt_day_start_ms(as_of_ms)
    days_back = n // 3 + 2  # always enough full days to cover n windows
    candidates = [
        _window(day + d * _DAY_MS, label, start_h, end_h)
        for d in range(-days_back, 1)
        for label, start_h, end_h in _DAY_SESSIONS
    ]
    completed = [w for w in candidates if w.end_ms <= as_of_ms]
    return completed[-n:]
