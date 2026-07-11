# M2 Session Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Additive, read-only session layer for the daily brief — a
bundle-level session clock, a per-symbol recap of the last 3 completed
sessions, and 180d session-tendency percentages — deterministic at fixed
`--as-of`.

**Architecture:** New pure calendar-math primitive
`analytics/session_windows.py` (no DB/pandas) + conn-free adapter
`analytics/brief/sessions.py`; `bundle.py` stays the only DB-toucher and
reuses the existing `stats/session.py::compute_session_breakdown` for
tendencies. Additive `SessionClock` on `BriefBundle` and `SessionState` on
`SymbolPanel`, flowing through renderer → Pydantic → Svelte.

**Tech Stack:** Python 3.11 / pandas / DuckDB (read-only) / FastAPI +
Pydantic / Svelte 5. Spec:
`docs/superpowers/specs/2026-07-10-m2-session-layer-design.md`.

## Global Constraints

- **DoD gate per task:** `make lint-py && make typecheck` green before every
  commit; task test suite green. Final task also runs `make test`,
  `make test-regression` (goldens MUST be unmoved), `make web-build`,
  `make lint-md`.
- **mypy strict:** every function fully annotated, including `-> None` on
  test functions.
- **Read-only + additive:** no DB writes, no schema changes, no change to any
  existing brief field, line format, or behavior. All new dataclasses are
  `@dataclass(frozen=True)`.
- **Determinism:** all windows derive from `as_of_ms` alone; completed 1h
  bars only (the bundle's `completed_bars` output); only windows with
  `end_ms <= as_of_ms` are recapped. No wall-clock anywhere.
- **Session convention (from spec, verbatim):** MYT = UTC+8, no DST.
  Partition: Asia [08:00, 14:00) · London [14:00, 22:00) · NY [22:00, 04:00)
  crossing midnight · Off [04:00, 08:00). Hours 20–21 MYT belong to
  **London** (the stats SQL CASE order); the clock carries them as an
  `is_overlap` display flag only.
- **Notes convention:** adapter health notes are UNPREFIXED (e.g.
  `"session recap: no 1h bars for Asia window"`); the bundle prefixes the
  symbol (`f"{symbol}: {n}"`) — same as M1's `brief/indicators.py`.
- **Display times are MYT** (repo-wide timezone rule), rendered via fixed
  +8h offset arithmetic (never `strftime("%a")` locale names — use the
  `_DOW` tuple pattern).
- **Tests:** in-memory DuckDB only (`tests/_brief_fixtures.py::make_conn`);
  no network. Run tests with `PYTHONPATH=. poetry run pytest <file> -v`.
- **Commits:** conventional commits (`feat:`, `test:`, `docs:`).
- **Frontend task (Task 5) must load `/frontend-design` + `/frontend-svelte`
  skills before touching `web/ui/`** (repo rule).

---

### Task 1: `analytics/session_windows.py` primitive

**Files:**

- Create: `analytics/session_windows.py`
- Test: `tests/test_session_windows.py`

**Interfaces:**

- Consumes: nothing (stdlib + dataclasses only — no DB, no pandas).
- Produces (Tasks 2–4 depend on these exact names):
  - `MYT_OFFSET_MS: int` (= 28_800_000)
  - `SessionWindow(label: str, start_ms: int, end_ms: int)` frozen dataclass;
    half-open `[start_ms, end_ms)` in Unix ms UTC.
  - `CurrentSession(label: str, start_ms: int, end_ms: int, is_overlap: bool,
    next_label: str, next_start_ms: int)` frozen dataclass.
  - `session_at(as_of_ms: int) -> CurrentSession`
  - `last_completed_windows(as_of_ms: int, n: int = 3) -> list[SessionWindow]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_session_windows.py`. `START` below is 2024-01-01
00:00:00 UTC — a Monday, and exactly 08:00 MYT (Asia open). All expected
windows in UTC offsets from `START`: Asia `[+0h, +6h)`, London `[+6h, +14h)`,
NY `[+14h, +20h)`, Off `[+20h, +24h)`.

```python
"""session_windows — MYT session partition calendar math."""

from analytics.session_windows import (
    CurrentSession,
    SessionWindow,
    last_completed_windows,
    session_at,
)

H = 3_600_000
DAY = 86_400_000
# 2024-01-01 00:00:00 UTC — Monday, exactly 08:00 MYT (Asia open).
START = 1_704_067_200_000


def test_asia_open_boundary() -> None:
    cur = session_at(START)
    assert cur == CurrentSession(
        label="Asia",
        start_ms=START,
        end_ms=START + 6 * H,
        is_overlap=False,
        next_label="London",
        next_start_ms=START + 6 * H,
    )


def test_asia_last_millisecond() -> None:
    assert session_at(START + 6 * H - 1).label == "Asia"


def test_london_open_boundary() -> None:
    cur = session_at(START + 6 * H)
    assert cur.label == "London"
    assert cur.start_ms == START + 6 * H
    assert cur.end_ms == START + 14 * H
    assert cur.is_overlap is False
    assert cur.next_label == "NY"
    assert cur.next_start_ms == START + 14 * H


def test_london_ny_overlap_flag() -> None:
    # 19:59 MYT -> no overlap; 20:00 and 21:59 MYT -> overlap (still London).
    assert session_at(START + 12 * H - 1).is_overlap is False
    at_2000 = session_at(START + 12 * H)
    assert at_2000.label == "London" and at_2000.is_overlap is True
    at_2159 = session_at(START + 14 * H - 1)
    assert at_2159.label == "London" and at_2159.is_overlap is True


def test_ny_crosses_midnight() -> None:
    # 22:00 MYT Monday and 03:00 MYT Tuesday are the SAME NY window.
    at_open = session_at(START + 14 * H)
    at_0300 = session_at(START + 19 * H)
    assert at_open.label == at_0300.label == "NY"
    assert at_open.start_ms == at_0300.start_ms == START + 14 * H
    assert at_open.end_ms == at_0300.end_ms == START + 20 * H
    # Next Asia = window end + the 4h Off gap.
    assert at_open.next_label == "Asia"
    assert at_open.next_start_ms == START + 24 * H


def test_off_gap() -> None:
    cur = session_at(START + 20 * H)  # 04:00 MYT Tuesday
    assert cur.label == "Off"
    assert cur.start_ms == START + 20 * H
    assert cur.end_ms == START + 24 * H
    assert cur.is_overlap is False
    assert cur.next_label == "Asia"
    assert cur.next_start_ms == START + 24 * H
    assert session_at(START + 24 * H - 1).label == "Off"


def test_last_completed_at_asia_open() -> None:
    # At Monday 08:00 MYT the 3 completed sessions are ALL of Sunday's.
    wins = last_completed_windows(START, n=3)
    assert wins == [
        SessionWindow("Asia", START - 24 * H, START - 18 * H),
        SessionWindow("London", START - 18 * H, START - 10 * H),
        SessionWindow("NY", START - 10 * H, START - 4 * H),
    ]


def test_last_completed_mid_london() -> None:
    # Tuesday 14:00 MYT: Tue Asia just completed (end == as_of counts),
    # then Mon London + Mon NY before it.
    wins = last_completed_windows(START + 30 * H, n=3)
    assert wins == [
        SessionWindow("London", START + 6 * H, START + 14 * H),
        SessionWindow("NY", START + 14 * H, START + 20 * H),
        SessionWindow("Asia", START + 24 * H, START + 30 * H),
    ]


def test_last_completed_during_off() -> None:
    # Tuesday 06:00 MYT (Off): the full prior Monday cycle.
    wins = last_completed_windows(START + 22 * H, n=3)
    assert [w.label for w in wins] == ["Asia", "London", "NY"]
    assert wins[0].start_ms == START
    assert wins[2].end_ms == START + 20 * H


def test_last_completed_n_larger() -> None:
    # Ascending completed at Tue 14:00 MYT: ... Sun NY, Mon Asia,
    # Mon London, Mon NY, Tue Asia — the last five:
    wins = last_completed_windows(START + 30 * H, n=5)
    assert len(wins) == 5
    assert [w.label for w in wins] == ["NY", "Asia", "London", "NY", "Asia"]
    assert all(w.end_ms <= START + 30 * H for w in wins)
    assert wins == sorted(wins, key=lambda w: w.start_ms)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_session_windows.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named
'analytics.session_windows'`

- [ ] **Step 3: Write the implementation**

Create `analytics/session_windows.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_session_windows.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/session_windows.py tests/test_session_windows.py
git commit -m "feat(analytics): session-window calendar primitive (MYT partition)"
```

---

### Task 2: Brief types + `analytics/brief/sessions.py` adapter

**Files:**

- Modify: `analytics/brief/types.py` (add 4 dataclasses; add `sessions` to
  `SymbolPanel` + `error_panel`; add `session_clock` to `BriefBundle`)
- Create: `analytics/brief/sessions.py`
- Test: `tests/test_brief_sessions.py`
- Modify: `tests/test_brief_types.py` (error_panel/serialisation coverage)

**Interfaces:**

- Consumes: `analytics.session_windows.last_completed_windows` (Task 1),
  `analytics.stats.session.SessionResult` /
  `.SessionRow(session, high_pct, low_pct, by_dow)` (existing).
- Produces (Tasks 3–5 depend on these exact names):
  - In `types.py`:
    - `SessionClock(label: str, start_ms: int, end_ms: int, is_overlap:
      bool, next_label: str, next_start_ms: int)`
    - `SessionRecapRow(session: str, start_ms: int, end_ms: int, open:
      float, high: float, low: float, close: float, net_pct: float,
      net_atr: float | None, range_atr: float | None, n_bars: int,
      expected_bars: int, made_set_high: bool, made_set_low: bool)`
    - `SessionTendencyRow(session: str, high_pct: float, low_pct: float)`
    - `SessionState(recap: list[SessionRecapRow] | None, tendency:
      list[SessionTendencyRow] | None)`
    - `SymbolPanel.sessions: SessionState | None` — placed after
      `indicators`, before `error`, NO default (mirrors `indicators`;
      mypy forces every construction site to update).
    - `BriefBundle.session_clock: SessionClock | None` — placed after
      `day_ahead`, NO default.
  - In `brief/sessions.py`:
    `build_session_state(completed_1h: pd.DataFrame, atr14: float,
    as_of_ms: int, tendency: SessionResult | None) ->
    tuple[SessionState | None, list[str]]` — notes UNPREFIXED.

- [ ] **Step 1: Add the dataclasses to `analytics/brief/types.py`**

Insert directly after the `IndicatorState` dataclass (before `SymbolPanel`):

```python
@dataclass(frozen=True)
class SessionClock:
    label: str  # "Asia" | "London" | "NY" | "Off"
    start_ms: int
    end_ms: int
    is_overlap: bool  # London 20:00-21:59 MYT
    next_label: str
    next_start_ms: int


@dataclass(frozen=True)
class SessionRecapRow:
    session: str
    start_ms: int
    end_ms: int
    open: float
    high: float
    low: float
    close: float
    net_pct: float  # (close - open) / open * 100
    net_atr: float | None  # (close - open) / atr14; None when atr14 <= 0
    range_atr: float | None  # (high - low) / atr14; None when atr14 <= 0
    n_bars: int
    expected_bars: int  # window hours: Asia 6 / London 8 / NY 6
    made_set_high: bool  # highest high across the recap rows present
    made_set_low: bool


@dataclass(frozen=True)
class SessionTendencyRow:
    session: str
    high_pct: float  # fraction of days this session made the daily high
    low_pct: float


@dataclass(frozen=True)
class SessionState:
    recap: list[SessionRecapRow] | None  # None = no window had bars
    tendency: list[SessionTendencyRow] | None  # None = stats compute failed
```

Then:

- In `SymbolPanel`, add `sessions: SessionState | None` between
  `indicators` and `error`.
- In `error_panel(...)`, add `sessions=None,` between `indicators=None,`
  and `error=message,`.
- In `BriefBundle`, add `session_clock: SessionClock | None` between
  `day_ahead` and `panels`.

Now find every other construction site and update it (mypy strict will
also catch any missed):

```bash
grep -rn "SymbolPanel(\|BriefBundle(" analytics/ web/ cli/ tests/
```

`analytics/brief/bundle.py` is wired in Task 3 — for THIS task make the
two constructor calls there compile by adding `sessions=None,` (in
`_compute_panel`'s `SymbolPanel(...)`) and `session_clock=None,` (in
`compute_brief`'s `BriefBundle(...)`); Task 3 replaces both `None`s with
real values. Any test constructing these directly gets the same
explicit-`None` treatment.

- [ ] **Step 2: Extend `tests/test_brief_types.py`**

Follow the file's existing style; add:

```python
def test_error_panel_sessions_none() -> None:
    assert error_panel("X", "boom").sessions is None


def test_session_state_serialises() -> None:
    state = SessionState(
        recap=[
            SessionRecapRow(
                session="Asia",
                start_ms=0,
                end_ms=6 * 3_600_000,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                net_pct=50.0,
                net_atr=0.25,
                range_atr=0.75,
                n_bars=6,
                expected_bars=6,
                made_set_high=True,
                made_set_low=False,
            )
        ],
        tendency=[SessionTendencyRow(session="Asia", high_pct=0.3, low_pct=0.4)],
    )
    d = asdict(state)
    assert d["recap"][0]["session"] == "Asia"
    assert d["tendency"][0]["high_pct"] == 0.3
```

(import `asdict` from `dataclasses`, and the new types from
`analytics.brief.types`, alongside the file's existing imports).

- [ ] **Step 3: Write the failing adapter tests**

Create `tests/test_brief_sessions.py`. The bar builder makes hand-checkable
numbers: bar at hour offset `h` has open `h`, close `h+1`, high `h+2`,
low `h`. As-of is Tuesday 14:00 MYT, so the 3 completed windows (UTC
offsets from START) are London `[+6h, +14h)`, NY `[+14h, +20h)`,
Asia `[+24h, +30h)`.

```python
"""brief/sessions adapter — recap + tendency assembly and degradation."""

import pandas as pd

from analytics.brief.sessions import build_session_state
from analytics.stats.session import SessionResult, SessionRow

H = 3_600_000
# 2024-01-01 00:00:00 UTC — Monday, exactly 08:00 MYT (Asia open).
START = 1_704_067_200_000
AS_OF = START + 30 * H  # Tuesday 14:00 MYT — Tue Asia just completed


def _h1_frame(hours: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "open_time": START + h * H,
                "open": float(h),
                "high": float(h) + 2.0,
                "low": float(h),
                "close": float(h) + 1.0,
                "volume": 1.0,
            }
            for h in hours
        ]
    )


def _tendency() -> SessionResult:
    return SessionResult(
        rows=[
            SessionRow(session="Asia", high_pct=0.31, low_pct=0.38, by_dow={}),
            SessionRow(session="London", high_pct=0.35, low_pct=0.27, by_dow={}),
            SessionRow(session="NY", high_pct=0.34, low_pct=0.35, by_dow={}),
        ]
    )


def test_full_recap_three_windows() -> None:
    state, notes = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert notes == []
    assert state is not None and state.recap is not None
    assert [r.session for r in state.recap] == ["London", "NY", "Asia"]
    london, ny, asia = state.recap
    # London bars 6..13: open 6, close 14, high 15, low 6.
    assert london.open == 6.0 and london.close == 14.0
    assert london.high == 15.0 and london.low == 6.0
    assert london.net_atr == 4.0 and london.range_atr == 4.5
    assert london.n_bars == 8 and london.expected_bars == 8
    # Asia (Tue) bars 24..29: open 24, close 30, high 31, low 24.
    assert asia.open == 24.0 and asia.close == 30.0
    assert asia.net_pct == (30.0 - 24.0) / 24.0 * 100.0
    assert asia.n_bars == 6 and asia.expected_bars == 6
    # Set extremes: highest high = Asia (31), lowest low = London (6).
    assert asia.made_set_high and not asia.made_set_low
    assert london.made_set_low and not london.made_set_high
    assert not ny.made_set_high and not ny.made_set_low


def test_tendency_mirrored_without_by_dow() -> None:
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert state is not None and state.tendency is not None
    assert [t.session for t in state.tendency] == ["Asia", "London", "NY"]
    assert state.tendency[0].high_pct == 0.31
    assert state.tendency[2].low_pct == 0.35


def test_empty_window_omitted_with_note() -> None:
    # No Tuesday-Asia bars (hours 24..29 missing).
    state, notes = build_session_state(
        _h1_frame(list(range(24))), atr14=2.0, as_of_ms=AS_OF, tendency=None
    )
    assert state is not None and state.recap is not None
    assert [r.session for r in state.recap] == ["London", "NY"]
    assert notes == ["session recap: no 1h bars for Asia window"]
    # Markers recomputed over the rows PRESENT: NY high 21 beats London 15.
    london, ny = state.recap
    assert ny.made_set_high and london.made_set_low
    assert state.tendency is None


def test_partial_window_kept_with_bar_count() -> None:
    hours = [h for h in range(30) if h not in (17, 18)]  # NY loses 2 bars
    state, notes = build_session_state(
        _h1_frame(hours), atr14=2.0, as_of_ms=AS_OF, tendency=None
    )
    assert notes == []
    assert state is not None and state.recap is not None
    ny = state.recap[1]
    assert ny.session == "NY"
    assert ny.n_bars == 4 and ny.expected_bars == 6
    assert ny.open == 14.0 and ny.close == 20.0  # OHLC from available bars


def test_all_windows_empty_recap_none() -> None:
    state, notes = build_session_state(
        _h1_frame([]), atr14=2.0, as_of_ms=AS_OF, tendency=_tendency()
    )
    assert notes == ["session recap: no 1h bars in any window"]
    assert state is not None
    assert state.recap is None and state.tendency is not None


def test_both_halves_none_collapses_state() -> None:
    state, notes = build_session_state(
        _h1_frame([]), atr14=2.0, as_of_ms=AS_OF, tendency=None
    )
    assert state is None
    assert notes == ["session recap: no 1h bars in any window"]


def test_zero_atr_drops_atr_fields() -> None:
    state, _ = build_session_state(
        _h1_frame(list(range(30))), atr14=0.0, as_of_ms=AS_OF, tendency=None
    )
    assert state is not None and state.recap is not None
    assert all(r.net_atr is None and r.range_atr is None for r in state.recap)
    assert state.recap[0].net_pct != 0.0  # pct never needs ATR
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named
'analytics.brief.sessions'`

- [ ] **Step 5: Write the adapter**

Create `analytics/brief/sessions.py`:

```python
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
from analytics.session_windows import last_completed_windows
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_sessions.py
tests/test_brief_types.py -v`
Expected: all PASS

- [ ] **Step 7: Gate + commit**

`make test` must stay green here (the explicit-`None` constructor updates
from Step 1 touch existing tests).

```bash
make lint-py && make typecheck && make test
git add analytics/brief/types.py analytics/brief/sessions.py \
    analytics/brief/bundle.py tests/test_brief_sessions.py \
    tests/test_brief_types.py
git commit -m "feat(brief): session types + last-3-completed recap adapter"
```

(Include any other constructor-site test files Step 1's grep surfaced.)

---

### Task 3: Bundle wiring (clock + tendency + adapter call)

**Files:**

- Modify: `analytics/brief/bundle.py`
- Test: `tests/test_brief_bundle.py`

**Interfaces:**

- Consumes: `build_session_state` (Task 2), `session_at` (Task 1),
  `compute_session_breakdown(conn, symbol, days, *, end_ms=None)`
  (existing, already as-of-deterministic), `SessionClock` (Task 2).
- Produces: `compute_brief` returns a bundle whose `session_clock` is
  always set and whose panels carry `sessions` (or `None` + health
  notes). Tasks 4–5 render/serve these fields.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_bundle.py` (AS_OF there is
`START_MS + 60 * DAY_MS` = 2024-03-01 00:00 UTC = **08:00 MYT Friday,
exactly Asia open**; seeded 1h bars end exactly at AS_OF, so the prior
MYT day's Asia/London/NY windows are fully covered):

```python
def test_bundle_session_clock() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    clock = bundle.session_clock
    assert clock is not None
    assert clock.label == "Asia"  # 08:00 MYT — Asia open boundary
    assert clock.start_ms == AS_OF
    assert clock.end_ms == AS_OF + 6 * H1_MS
    assert clock.next_label == "London"
    assert clock.is_overlap is False


def test_panel_has_session_state() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.sessions is not None
    recap = panel.sessions.recap
    assert recap is not None
    assert [r.session for r in recap] == ["Asia", "London", "NY"]
    assert [r.n_bars for r in recap] == [r.expected_bars for r in recap]
    assert sum(r.made_set_high for r in recap) == 1
    assert sum(r.made_set_low for r in recap) == 1
    tendency = panel.sessions.tendency
    assert tendency is not None and len(tendency) == 3


def test_error_panel_has_no_sessions() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT", "NODATAUSDT")))
    assert bundle.panels[1].sessions is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_bundle.py -v`
Expected: the three new tests FAIL (`session_clock`/`sessions` are the
Task-2 placeholder `None`s); all pre-existing tests PASS.

- [ ] **Step 3: Wire the bundle**

In `analytics/brief/bundle.py`:

1. Add imports:

```python
from analytics.brief.sessions import build_session_state
from analytics.brief.types import BriefBundle, SessionClock, SymbolPanel, error_panel
from analytics.session_windows import session_at
from analytics.stats.session import compute_session_breakdown
```

(the `types` line REPLACES the existing one — same names + `SessionClock`.)

1. Add a module-level helper next to `_regime_label`:

```python
def _session_clock(as_of_ms: int) -> SessionClock:
    cur = session_at(as_of_ms)
    return SessionClock(
        label=cur.label,
        start_ms=cur.start_ms,
        end_ms=cur.end_ms,
        is_overlap=cur.is_overlap,
        next_label=cur.next_label,
        next_start_ms=cur.next_start_ms,
    )
```

1. In `_compute_panel`, directly after the `notes.extend(f"{symbol}: {n}"
for n in ind_notes)` line, add:

```python
    try:
        tendency = compute_session_breakdown(conn, symbol, cfg.stats_days, end_ms=as_of)
    except Exception as exc:  # tendency is optional; recap may still render
        tendency = None
        notes.append(f"{symbol}: session tendency failed ({exc})")
    sessions, sess_notes = build_session_state(
        completed_1h=completed_1h,
        atr14=atr,
        as_of_ms=as_of,
        tendency=tendency,
    )
    notes.extend(f"{symbol}: {n}" for n in sess_notes)
```

and replace the placeholder `sessions=None,` in the `SymbolPanel(...)`
call with `sessions=sessions,`.

Note: `build_strip` (seasonality) also calls `compute_session_breakdown`
internally — this duplicate ~ms-cheap SQL is accepted; do NOT refactor
`build_strip` (out of scope, spec non-goal).

1. In `compute_brief`, replace the placeholder `session_clock=None,` with
`session_clock=_session_clock(cfg.as_of_ms),`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_bundle.py -v`
Expected: all PASS (including the pre-existing determinism test
`test_compute_brief_deterministic`, which now covers the new fields via
dataclass equality).

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/bundle.py tests/test_brief_bundle.py
git commit -m "feat(brief): wire session clock + per-panel session state into bundle"
```

---

### Task 4: Renderer — clock line + Sessions block

**Files:**

- Modify: `analytics/brief/render.py`
- Test: `tests/test_brief_render.py`

**Interfaces:**

- Consumes: `SessionClock`, `SessionState`, `SessionRecapRow`,
  `SessionTendencyRow` (Task 2); bundle fields (Task 3); existing
  `fmt_dist` (`f"{value:+.2f}"`) and `fmt_frac` (`f"{value*100:.0f}%"`).
- Produces: frozen line formats (below) — Task 5's Svelte block mirrors
  them; the determinism test covers them.

Frozen formats:

```text
Session: London (NY overlap) 14:00–22:00 MYT · 6h12m in / 1h48m left · next NY 22:00 MYT
Session: between sessions (04:00–08:00 MYT) · next Asia 08:00 MYT
Sessions Asia    Thu 08–14 MYT · net +0.32% (+0.40 ATR) · range 1.1 ATR ·set-high
         London  Wed 14–22 MYT (4/8 bars) · net -0.85% (-1.10 ATR) · range 1.8 ATR ·set-low
         tendency: day-high Asia 31% · London 35% · NY 34% | day-low Asia 38% · London 27% · NY 35%
```

("(NY overlap)" only when `is_overlap`; "(n/m bars)" only when
`n_bars < expected_bars`; `·set-high` / `·set-low` only on the marked
rows; "range n/a" + no ATR paren when the ATR fields are None.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_render.py`. The file already imports
`compute_brief`, `render_markdown`, `make_conn`, `seed_symbol`,
`START_MS` (= 1_704_067_200_000 = 2024-01-01 00:00 UTC = **08:00 MYT
Monday**) and has a `_seeded_cfg(tmp_path)` helper (its `as_of` is
`START_MS + 60 * DAY_MS` = 08:00 MYT Friday, Asia open). Extend its
`from analytics.brief.render import (...)` block with `_clock_line` and
`_recap_bit`, and its `from analytics.brief.types import (...)` block
with `SessionClock` and `SessionRecapRow`. Then add:

```python
H1 = 3_600_000


def test_clock_line_active_session() -> None:
    clock = SessionClock(
        label="London",
        start_ms=START_MS + 6 * H1,
        end_ms=START_MS + 14 * H1,
        is_overlap=False,
        next_label="NY",
        next_start_ms=START_MS + 14 * H1,
    )
    line = _clock_line(clock, START_MS + 9 * H1)  # 17:00 MYT
    assert line == (
        "Session: London 14:00–22:00 MYT · 3h00m in / 5h00m left · "
        "next NY 22:00 MYT"
    )


def test_clock_line_overlap_and_off() -> None:
    overlap = SessionClock(
        label="London",
        start_ms=START_MS + 6 * H1,
        end_ms=START_MS + 14 * H1,
        is_overlap=True,
        next_label="NY",
        next_start_ms=START_MS + 14 * H1,
    )
    assert _clock_line(overlap, START_MS + 13 * H1) is not None
    assert "London (NY overlap)" in str(_clock_line(overlap, START_MS + 13 * H1))
    off = SessionClock(
        label="Off",
        start_ms=START_MS + 20 * H1,
        end_ms=START_MS + 24 * H1,
        is_overlap=False,
        next_label="Asia",
        next_start_ms=START_MS + 24 * H1,
    )
    assert _clock_line(off, START_MS + 21 * H1) == (
        "Session: between sessions (04:00–08:00 MYT) · next Asia 08:00 MYT"
    )
    assert _clock_line(None, START_MS) is None


def test_recap_bit_formats() -> None:
    row = SessionRecapRow(
        session="Asia",
        start_ms=START_MS,  # Mon 08:00 MYT
        end_ms=START_MS + 6 * H1,
        open=100.0,
        high=103.0,
        low=99.0,
        close=101.0,
        net_pct=1.0,
        net_atr=0.5,
        range_atr=2.0,
        n_bars=6,
        expected_bars=6,
        made_set_high=True,
        made_set_low=False,
    )
    assert _recap_bit(row) == (
        "Asia   Mon 08–14 MYT · net +1.00% (+0.50 ATR) · range 2.0 ATR ·set-high"
    )
    partial = SessionRecapRow(
        session="NY",
        start_ms=START_MS + 14 * H1,
        end_ms=START_MS + 20 * H1,
        open=100.0,
        high=103.0,
        low=99.0,
        close=101.0,
        net_pct=1.0,
        net_atr=None,
        range_atr=None,
        n_bars=4,
        expected_bars=6,
        made_set_high=False,
        made_set_low=False,
    )
    assert _recap_bit(partial) == (
        "NY     Mon 22–04 MYT (4/6 bars) · net +1.00% · range n/a"
    )


def test_markdown_carries_session_lines(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)  # as_of = Fri 08:00 MYT, Asia open
    out = render_markdown(compute_brief(conn, cfg))
    assert "\nSession: Asia 08:00–14:00 MYT · 0h00m in / 6h00m left" in out
    assert "\nSessions " in out
    assert "tendency: day-high " in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_render.py -v`
Expected: new tests FAIL with `ImportError: cannot import name
'_clock_line'`; pre-existing tests PASS.

- [ ] **Step 3: Implement the renderer additions**

In `analytics/brief/render.py`:

1. Extend the `analytics.brief.types` import with `SessionClock`,
   `SessionRecapRow`, `SessionState`, `SessionTendencyRow`.

2. Add module constants after the imports:

```python
_MYT_OFFSET_MS = 8 * 3_600_000
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
```

1. Add the helpers directly after `_indicator_lines`:

```python
def _myt_hhmm(ms: int) -> str:
    return pd.Timestamp(ms + _MYT_OFFSET_MS, unit="ms", tz="UTC").strftime("%H:%M")


def _fmt_dur(ms: int) -> str:
    minutes = ms // 60_000
    return f"{minutes // 60}h{minutes % 60:02d}m"


def _clock_line(clock: SessionClock | None, as_of_ms: int) -> str | None:
    if clock is None:
        return None
    span = f"{_myt_hhmm(clock.start_ms)}–{_myt_hhmm(clock.end_ms)} MYT"
    nxt = f"next {clock.next_label} {_myt_hhmm(clock.next_start_ms)} MYT"
    if clock.label == "Off":
        return f"Session: between sessions ({span}) · {nxt}"
    overlap = " (NY overlap)" if clock.is_overlap else ""
    elapsed = _fmt_dur(as_of_ms - clock.start_ms)
    left = _fmt_dur(clock.end_ms - as_of_ms)
    return f"Session: {clock.label}{overlap} {span} · {elapsed} in / {left} left · {nxt}"


def _recap_bit(row: SessionRecapRow) -> str:
    start = pd.Timestamp(row.start_ms + _MYT_OFFSET_MS, unit="ms", tz="UTC")
    end = pd.Timestamp(row.end_ms + _MYT_OFFSET_MS, unit="ms", tz="UTC")
    span = (
        f"{_DOW[int(start.weekday())]} "
        f"{start.strftime('%H')}–{end.strftime('%H')} MYT"
    )
    cov = (
        ""
        if row.n_bars >= row.expected_bars
        else f" ({row.n_bars}/{row.expected_bars} bars)"
    )
    atr_bit = f" ({fmt_dist(row.net_atr)} ATR)" if row.net_atr is not None else ""
    rng = f"range {row.range_atr:.1f} ATR" if row.range_atr is not None else "range n/a"
    marks = (" ·set-high" if row.made_set_high else "") + (
        " ·set-low" if row.made_set_low else ""
    )
    return f"{row.session:<7}{span}{cov} · net {row.net_pct:+.2f}%{atr_bit} · {rng}{marks}"


def _tendency_bit(rows: list[SessionTendencyRow]) -> str:
    hi = " · ".join(f"{r.session} {fmt_frac(r.high_pct)}" for r in rows)
    lo = " · ".join(f"{r.session} {fmt_frac(r.low_pct)}" for r in rows)
    return f"tendency: day-high {hi} | day-low {lo}"


def _session_lines(state: SessionState | None) -> list[str]:
    if state is None:
        return []
    bits: list[str] = []
    if state.recap is not None:
        bits.extend(_recap_bit(r) for r in state.recap)
    if state.tendency is not None:
        bits.append(_tendency_bit(state.tendency))
    if not bits:
        return []
    return [f"{'Sessions':<9}{bits[0]}"] + [f"{'':9}{b}" for b in bits[1:]]
```

1. In `_panel_lines`, directly after
   `lines.extend(_indicator_lines(panel.indicators))`, add:

```python
    lines.extend(_session_lines(panel.sessions))
```

1. In `render_markdown`, replace the two-element list literal so the
   clock renders between the header and the blank line:

```python
    lines = [
        f"BUIBUI DAILY BRIEF — {bundle.day_ahead} · as-of {as_of} UTC · data {data}",
    ]
    clock = _clock_line(bundle.session_clock, bundle.as_of_ms)
    if clock is not None:
        lines.append(clock)
    lines.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_render.py -v`
Expected: all PASS — including the pre-existing byte-determinism tests,
which now cover the new lines.

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/render.py tests/test_brief_render.py
git commit -m "feat(brief): render session clock line + per-panel Sessions block"
```

---

### Task 5: API models + Svelte UI + legend

**Files:**

- Modify: `web/api/models/brief.py`
- Modify: `web/ui/src/api.ts` (brief interfaces around line 686)
- Modify: `web/ui/src/pages/Brief.svelte`
- Test: `tests/test_web_brief.py`

**Interfaces:**

- Consumes: bundle fields (Task 3); the router already builds
  `BriefResponse` from `bundle_to_dict`, so ONLY the models need new
  fields — no router change.
- Produces: `GET /api/brief` response carries `session_clock` (top level)
  and `sessions` (per panel).

**Load `/frontend-design` and `/frontend-svelte` before the Svelte edits
(repo rule).**

- [ ] **Step 1: Write the failing API test**

Append to `tests/test_web_brief.py` (the file already has `_client`,
`make_conn`, `seed_symbol`, `START_MS`, and `AS_OF_ISO =
"2024-03-01T00:00:00Z"` — which is 08:00 MYT, Asia open, so the clock
label is deterministic):

```python
def test_get_brief_carries_sessions() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    body = res.json()
    clock = body["session_clock"]
    assert clock["label"] == "Asia"
    assert set(clock) == {
        "label",
        "start_ms",
        "end_ms",
        "is_overlap",
        "next_label",
        "next_start_ms",
    }
    sessions = body["panels"][0]["sessions"]
    assert sessions is not None
    assert [r["session"] for r in sessions["recap"]] == ["Asia", "London", "NY"]
    assert len(sessions["tendency"]) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_web_brief.py -v`
Expected: new test FAILS with `KeyError: 'session_clock'` (Pydantic drops
unknown dict keys); pre-existing tests PASS.

- [ ] **Step 3: Add the Pydantic models**

In `web/api/models/brief.py`, insert after `IndicatorStateModel` (before
`SymbolPanelModel`):

```python
class SessionClockModel(BaseModel):
    label: str
    start_ms: int
    end_ms: int
    is_overlap: bool
    next_label: str
    next_start_ms: int


class SessionRecapRowModel(BaseModel):
    session: str
    start_ms: int
    end_ms: int
    open: float
    high: float
    low: float
    close: float
    net_pct: float
    net_atr: float | None
    range_atr: float | None
    n_bars: int
    expected_bars: int
    made_set_high: bool
    made_set_low: bool


class SessionTendencyRowModel(BaseModel):
    session: str
    high_pct: float
    low_pct: float


class SessionStateModel(BaseModel):
    recap: list[SessionRecapRowModel] | None
    tendency: list[SessionTendencyRowModel] | None
```

Then add `sessions: SessionStateModel | None` to `SymbolPanelModel`
(after `indicators`) and `session_clock: SessionClockModel | None` to
`BriefResponse` (after `day_ahead`).

- [ ] **Step 4: Run the API test to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_web_brief.py -v`
Expected: all PASS

- [ ] **Step 5: Add the TypeScript interfaces**

In `web/ui/src/api.ts`, next to `BriefIndicatorState` (~line 686), add:

```typescript
export interface BriefSessionClock {
  label: string;
  start_ms: number;
  end_ms: number;
  is_overlap: boolean;
  next_label: string;
  next_start_ms: number;
}

export interface BriefSessionRecapRow {
  session: string;
  start_ms: number;
  end_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  net_pct: number;
  net_atr: number | null;
  range_atr: number | null;
  n_bars: number;
  expected_bars: number;
  made_set_high: boolean;
  made_set_low: boolean;
}

export interface BriefSessionTendencyRow {
  session: string;
  high_pct: number;
  low_pct: number;
}

export interface BriefSessionState {
  recap: BriefSessionRecapRow[] | null;
  tendency: BriefSessionTendencyRow[] | null;
}
```

Add `sessions: BriefSessionState | null;` to `BriefSymbolPanel` (next to
`indicators`, ~line 711) and `session_clock: BriefSessionClock | null;`
to `BriefResponse` (~line 773).

- [ ] **Step 6: Add the Svelte clock chip + Sessions sub-section + legend
  entries**

In `web/ui/src/pages/Brief.svelte` (mirror the markdown block's frozen
formats from Task 4; MYT via fixed +8h offset):

1. Script helpers (near the existing formatting helpers):

```typescript
const MYT_MS = 28_800_000;
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const mytHHMM = (ms: number) => new Date(ms + MYT_MS).toISOString().slice(11, 16);
const mytHH = (ms: number) => new Date(ms + MYT_MS).toISOString().slice(11, 13);
const mytDow = (ms: number) => DOW[(new Date(ms + MYT_MS).getUTCDay() + 6) % 7];
```

1. Clock chip in the header bar (next to the `ⓘ legend` button, ~line 66)
— render from `brief.session_clock`: label (or "between sessions" for
`Off`), "(NY overlap)" when `is_overlap`, and
`next {next_label} {mytHHMM(next_start_ms)} MYT`. A muted pill, styled
like the existing header chips.

2. Sessions sub-section per symbol card, directly after the
`{#if panel.indicators}` block (~line 199), same `muted` styling pattern:

```svelte
{#if panel.sessions}
  {@const s = panel.sessions}
  <div class="sessions muted">
    {#if s.recap}
      {#each s.recap as row}
        <div>
          <span class="sess-name">{row.session}</span>
          {mytDow(row.start_ms)} {mytHH(row.start_ms)}–{mytHH(row.end_ms)} MYT
          {#if row.n_bars < row.expected_bars}({row.n_bars}/{row.expected_bars} bars){/if}
          · net {row.net_pct >= 0 ? "+" : ""}{row.net_pct.toFixed(2)}%{row.net_atr !== null
            ? ` (${row.net_atr >= 0 ? "+" : ""}${row.net_atr.toFixed(2)} ATR)`
            : ""} · {row.range_atr !== null ? `range ${row.range_atr.toFixed(1)} ATR` : "range n/a"}
          {#if row.made_set_high}<span class="sess-mark">·set-high</span>{/if}
          {#if row.made_set_low}<span class="sess-mark">·set-low</span>{/if}
        </div>
      {/each}
    {/if}
    {#if s.tendency}
      <div>
        tendency: day-high {s.tendency.map((t) => `${t.session} ${Math.round(t.high_pct * 100)}%`).join(" · ")}
        | day-low {s.tendency.map((t) => `${t.session} ${Math.round(t.low_pct * 100)}%`).join(" · ")}
      </div>
    {/if}
  </div>
{/if}
```

1. Legend entries in the legend `<dl>` (~line 78) — one `<dt>/<dd>` pair
each for: **Session clock** (current MYT session; Asia 08–14 / London
14–22 / NY 22–04, 20–22 = London–NY overlap; Off = between sessions),
**Sessions** (last 3 completed sessions: net move & range in ATR14
units; (n/m bars) = partial 1h coverage), **·set-high / ·set-low** (which
of the 3 printed the set's extreme), **tendency** (180d share of days
each session made the daily high/low).

2. CSS: `.sessions` mirrors the existing `.indicators` block styles
(~line 828); `.sess-name` uses the dim label color; `.sess-mark` slightly
emphasized. Follow the page's existing dark-minimal tokens — no new
colors.

- [ ] **Step 7: Build + gate + commit**

Run: `make web-build`
Expected: clean production build (the pre-existing Backtest.svelte
warning is known noise).

```bash
make lint-py && make typecheck
git add web/api/models/brief.py web/ui/src/api.ts \
    web/ui/src/pages/Brief.svelte tests/test_web_brief.py
git commit -m "feat(brief): session clock + sessions block through API and Brief UI"
```

---

### Task 6: Docs sync + final verification

**Files:**

- Modify: `CLAUDE.md` (the `brief/` bullet in Project Structure)
- Modify: `README.md` (the daily-brief feature mention)

**Interfaces:**

- Consumes: everything shipped in Tasks 1–5.
- Produces: docs in sync; final whole-branch gate evidence.

- [ ] **Step 1: Update `CLAUDE.md`**

In the `analytics/` section's `brief/` bullet, append after the M1
sentence (ends "`_H1_FETCH_DAYS` 62."):

```text
M2 session layer: pure `analytics/session_windows.py` calendar primitive
(MYT partition Asia 08–14 / London 14–22 / NY 22–04 / Off 04–08, the
stats/session.py SQL CASE as bar-classification; `session_at` +
`last_completed_windows`) adapted by `brief/sessions.py` into a
bundle-level `BriefBundle.session_clock` + additive
`SymbolPanel.sessions` (last-3-completed-session recap vs ATR14 with
set-high/low markers + 180d tendency rows reusing
`compute_session_breakdown`); recap/tendency degrade independently to
health notes.
```

- [ ] **Step 2: Update `README.md`**

Find the daily market brief feature description (search for "brief") and
extend it with one sentence:

```text
An M2 session layer adds a bundle-level session clock (Asia/London/NY in
MYT) plus a per-symbol recap of the last 3 completed sessions (net move
and range in ATR units, set-extreme markers, partial-coverage flags) and
the 180-day session high/low tendency percentages.
```

- [ ] **Step 3: Full gate**

```bash
make lint-py && make typecheck && make test
make test-regression   # goldens MUST be unmoved — investigate ANY diff
make web-build
make lint-md
```

Expected: everything green; regression goldens byte-identical (the brief
is not on the backtest path — a golden diff means an accidental
behavioral change, STOP and report).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: sync CLAUDE.md + README for M2 session layer"
```

---

## Post-plan notes (for the orchestrator, not the workers)

- Implementation branch: `feat/m2-session-layer` off `main`; per-task
  review + opus final whole-branch review, as in M0/M1.
- Post-branch sweep additionally syncs `.claude/context/web.md` (API/UI
  reference) — kept out of Task 6 to mirror the M1 flow.
- Known accepted duplicate: `compute_session_breakdown` runs twice per
  symbol (seasonality strip + tendency). Cheap; noted in Task 3.
