# Weekly Path Cone + Monthly Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a weekly price-distribution cone (168 hourly bars, Monday-anchored,
AWR14-normalized, conditioned on direction) plus a three-number monthly context line, so
the operator can read month → week → day instead of only day.

**Architecture:** A new standalone pure module `analytics/stats/weekly_cone.py`
structurally parallel to the shipped `analytics/stats/path_cone.py` but sharing no
internals with it (the daily module groups bars by UTC date, which the weekly cone does
not want; the only common code is a nine-line SQL SELECT). It is wired additively into
`StatsBundle` → the stats API → a new Svelte component, and separately into the daily
Brief as two independent sub-blocks that degrade to health notes.

**Tech Stack:** Python 3.11+, DuckDB, numpy, pandas, Pydantic v2, FastAPI, Svelte 5 +
Vite, pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-07-21-weekly-path-cone-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Definition of Done (CLAUDE.md).** A Python change is not done until `make lint-py`,
  `make typecheck`, `make test` and `make test-regression` have been **run** and their
  results **stated plainly**. Never claim green without running. UI changes additionally
  require **both** `make web-build` and `make web-check` — `web-build` is not a type gate.
- **Run tests in the FOREGROUND.** Never background a test command.
- All functions carry full type annotations including return types (`-> None` on tests).
  mypy runs strict (`disallow_untyped_defs = true`).
- Analytics tests use `duckdb.connect(":memory:")`. Never touch the real `analytics.db`.
- Tests make no network calls.
- **Regression goldens must not move.** Nothing in this plan touches the backtest
  pipeline. If `make test-regression` moves, that is a bug in this work, not an
  intentional behavioral change — stop and report it.
- **Do not modify `analytics/stats/path_cone.py`.** Its behavior and cached output stay
  byte-identical (spec §9).
- **Do not remove the try/except at `web/api/routers/stats.py:202-208`.** It is what makes
  a stale warm cache degrade to a recompute instead of a 500 (spec §4.3).
- **No forecast language in any user-facing string.** The cone is conditional on outcome,
  not a prediction (spec §3). Write "given a week closed bull, its path was…", never
  "expect", "likely to", "should".
- Week anchoring is **Monday 00:00 UTC** everywhere, matching `analytics/reference_levels.py`.
- Conventional commits (`feat:`, `test:`, `docs:`). Branch is `docs/weekly-path-cone`
  (already created; the spec is committed at `618fff5`).

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `analytics/stats/weekly_cone.py` | **Create.** Pure weekly cone computation | 1 |
| `tests/test_weekly_cone.py` | **Create.** Unit tests for the above | 1 |
| `analytics/stats/bundle.py` | **Modify.** Additive `weekly_cone` field on `StatsBundle` | 2 |
| `web/api/models/stats.py` | **Modify.** `WeeklyConeResponse`, `WeeklyConeComboResponse`, `CurrentWeekPathResponse` | 2 |
| `web/api/routers/stats.py` | **Modify.** Map bundle → response; inject live overlay | 2 |
| `tests/test_web_stats_weekly_cone.py` | **Create.** API-level tests | 2 |
| `web/ui/src/lib/cone.ts` | **Create.** Pure band/price math shared by both cones | 3 |
| `web/ui/src/components/WeeklyCone.svelte` | **Create.** Weekly cone chart | 3 |
| `web/ui/src/components/PathCone.svelte` | **Modify.** Consume shared math; behavior unchanged | 3 |
| `web/ui/src/pages/Stats.svelte` | **Modify.** Mount the new card | 3 |
| `analytics/brief/types.py` | **Modify.** `WeeklyState`, `MonthlyContext` dataclasses + panel fields | 4, 5 |
| `analytics/brief/weekly.py` | **Create.** Conn-free weekly-state adapter | 4 |
| `analytics/brief/monthly.py` | **Create.** Conn-free monthly-context adapter | 5 |
| `analytics/brief/bundle.py` | **Modify.** Fetch + wire both blocks with health notes | 4, 5 |
| `analytics/brief/render.py` | **Modify.** `_weekly_lines` / `_monthly_lines` | 4, 5 |
| `tests/test_brief_weekly.py` | **Create.** | 4 |
| `tests/test_brief_monthly.py` | **Create.** | 5 |

Tasks 1–3 deliver the weekly cone end-to-end (module → API → UI) and are independently
shippable. Tasks 4–5 add the Brief blocks and depend only on Task 1.

---

### Task 1: `analytics/stats/weekly_cone.py`

**Files:**

- Create: `analytics/stats/weekly_cone.py`
- Test: `tests/test_weekly_cone.py`

**Interfaces:**

- Consumes: the `ohlcv` table (`symbol`, `timeframe`, `open_time`, `open`, `high`, `low`,
  `close`), timeframe `'1h'`.
- Produces, relied on by Tasks 2, 4:
  - `WeeklyConeCombo(direction: str, n: int, bands: list[list[float]], low_in_by: list[float], high_in_by: list[float], mae_p: list[float], mfe_p: list[float], high_piv: list[float], low_piv: list[float])`
  - `WeeklyConeBundle(combos: dict[str, WeeklyConeCombo], total_weeks: int)` — keys are
    exactly `"all"`, `"bull"`, `"bear"`
  - `CurrentWeekPath(points: list[float], elapsed_h: int, awr14_current: float, week_open: float)`
  - `compute_weekly_cone(conn, symbol, *, now_ms: int | None = None) -> WeeklyConeBundle`
  - `compute_current_week_path(conn, symbol, *, now_ms: int | None = None) -> CurrentWeekPath | None`

**Background the implementer needs:** read `analytics/stats/path_cone.py` first. This
module is its structural twin at a different period. Do not import from it and do not
edit it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weekly_cone.py`:

```python
"""Tests for analytics/stats/weekly_cone.py using in-memory DuckDB.

Synthetic-week construction mirrors tests/test_path_cone.py: every bar opens
at 100 and closes at 100 + k. Bar 1 carries the week low (99); bar 2 carries
the week high (101). With k in [-0.5, +0.5] every other bar stays inside
[99.5, 100.5], so every complete week's range is exactly (101 - 99) / 100 =
0.02 and the trailing AWR14 of any week with 14 complete predecessors is
exactly 0.02. A week's normalized close path is then constant at k / 2:
((100 + k - 100) / 100) / 0.02 = k / 2.
"""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats.weekly_cone import (
    WEEK_BARS,
    WeeklyConeBundle,
    compute_current_week_path,
    compute_weekly_cone,
)

_SYMBOL = "WCONEUSDT"
# Fixed clock: Wednesday 2026-03-04 12:30 UTC (hour 60 of that week).
_NOW = datetime(2026, 3, 4, 12, 30, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)
_CURRENT_WEEK = date(2026, 3, 2)  # the Monday of _NOW


def _insert_week(
    conn: duckdb.DuckDBPyConnection,
    monday: date,
    *,
    k: float = 0.0,
    n_bars: int = WEEK_BARS,
) -> None:
    """Insert one synthetic Monday-anchored week of 1h bars."""
    base_ms = int(
        datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp() * 1000
    )
    close = 100.0 + k
    for h in range(n_bars):
        if h == 0:
            high, low = 100.5, 99.0
        elif h == 1:
            high, low = 101.0, 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_SYMBOL, "1h", base_ms + h * 3_600_000, 100.0, high, low, close, 100.0, 50.0],
        )


def _seed_warmup(
    conn: duckdb.DuckDBPyConnection, start_monday: date, n: int = 14
) -> None:
    """n consecutive complete doji (k=0) weeks starting at start_monday."""
    for i in range(n):
        _insert_week(conn, start_monday + timedelta(weeks=i))


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_warmup_weeks_produce_no_records(conn: duckdb.DuckDBPyConnection) -> None:
    """The first 14 qualifying weeks have no AWR window, so no records."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=14), n=14)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 0
    assert bundle.combos["all"].n == 0
    assert bundle.combos["all"].bands == []


def test_fifteenth_week_enters_population(conn: duckdb.DuckDBPyConnection) -> None:
    """One week past warmup yields exactly one record, direction bull."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 1
    assert bundle.combos["bull"].n == 1
    assert bundle.combos["bear"].n == 0
    assert bundle.combos["all"].n == 1


def test_normalization_identity(conn: duckdb.DuckDBPyConnection) -> None:
    """Normalized path at bar 168 equals week return / AWR14 (== k / 2)."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    bands = bundle.combos["bull"].bands
    assert len(bands) == WEEK_BARS
    # Single member -> every percentile equals that member's value.
    assert bands[-1] == pytest.approx([0.2] * 5)


def test_short_week_excluded(conn: duckdb.DuckDBPyConnection) -> None:
    """A 167-bar week never enters the population."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4, n_bars=167)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert bundle.total_weeks == 0


def test_week_boundary_across_year_end(conn: duckdb.DuckDBPyConnection) -> None:
    """Monday anchoring holds across a year boundary (2025-12-29 is a Monday)."""
    _seed_warmup(conn, date(2025, 12, 29) - timedelta(weeks=14), n=14)
    _insert_week(conn, date(2025, 12, 29), k=-0.4)
    now = int(datetime(2026, 1, 7, 12, 0, tzinfo=UTC).timestamp() * 1000)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=now)
    assert bundle.total_weeks == 1
    assert bundle.combos["bear"].n == 1


def test_thin_data_returns_empty_never_raises(conn: duckdb.DuckDBPyConnection) -> None:
    """No bars at all -> empty combos, no exception."""
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert isinstance(bundle, WeeklyConeBundle)
    assert bundle.total_weeks == 0
    assert set(bundle.combos) == {"all", "bull", "bear"}


def test_determinism(conn: duckdb.DuckDBPyConnection) -> None:
    """Same inputs -> identical bundle."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=16), n=15)
    a = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    b = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert a == b


def test_current_week_path_partial(conn: duckdb.DuckDBPyConnection) -> None:
    """Partial current week: elapsed_h counts only closed bars."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=14), n=14)
    _insert_week(conn, _CURRENT_WEEK, k=0.2, n_bars=61)
    path = compute_current_week_path(conn, _SYMBOL, now_ms=_NOW_MS)
    assert path is not None
    assert path.week_open == 100.0
    assert path.awr14_current == pytest.approx(0.02)
    # _NOW is 12:30 Wed = hour 60 of the week forming; 60 bars have closed.
    assert path.elapsed_h == 60
    assert len(path.points) == 61


def test_current_week_path_none_without_awr(conn: duckdb.DuckDBPyConnection) -> None:
    """Fewer than 14 complete prior weeks -> None."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=3), n=3)
    _insert_week(conn, _CURRENT_WEEK, k=0.2, n_bars=61)
    assert compute_current_week_path(conn, _SYMBOL, now_ms=_NOW_MS) is None


def test_low_high_timing_curves(conn: duckdb.DuckDBPyConnection) -> None:
    """Low is set at bar 1 and high at bar 2 by construction."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=15), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=1), k=0.4)
    combo = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS).combos["all"]
    assert combo.low_in_by[0] == pytest.approx(1.0)
    assert combo.high_in_by[0] == pytest.approx(0.0)
    assert combo.high_in_by[1] == pytest.approx(1.0)
    assert len(combo.low_in_by) == WEEK_BARS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_weekly_cone.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'analytics.stats.weekly_cone'`.

- [ ] **Step 3: Write the implementation**

Create `analytics/stats/weekly_cone.py`:

```python
"""Weekly price-distribution cone — conditional path percentiles over 1h history.

Spec: docs/superpowers/specs/2026-07-21-weekly-path-cone-design.md

- Period = Monday 00:00 UTC → Sunday 23:00 UTC (matches analytics/reference_levels.py).
- Each complete historical week's hourly-close path is normalized by that
  week's OWN trailing AWR14 (mean (high−low)/open over the last 14 complete
  weeks strictly before it) → ×AWR units comparable across vol regimes.
- Per direction (3: all/bull/bear): per-step percentile bands, low/high timing
  distributions, excursion percentiles, and pivot-magnitude percentiles.
  Weekday is the x-axis here, not a conditioning cell — so "all" IS the
  unconditional reference band rendered beneath the conditional cones.

The cone is CONDITIONAL ON OUTCOME, not a forecast: a "bull week" is defined
by its close, so the bull cone sits above the unconditional one by
construction and that separation carries no predictive information.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import duckdb
import numpy as np

BAND_PCTS = (10.0, 25.0, 50.0, 75.0, 90.0)  # bands row order p10…p90
EXCURSION_PCTS = (10.0, 50.0, 90.0)
PIVOT_PCTS = (50.0, 80.0)
DIRECTIONS = ("all", "bull", "bear")
WEEK_BARS = 168
_AWR_WINDOW = 14  # weeks
_HOUR_MS = 3_600_000
_WEEK_MS = 7 * 24 * _HOUR_MS
_CURRENT_FETCH_WEEKS = 24  # covers 14 complete prior weeks with gap slack


@dataclass
class WeeklyConeCombo:
    """One direction slice of the weekly cone. Empty lists when n == 0."""

    direction: str  # "all" | "bull" | "bear"
    n: int
    bands: list[list[float]]  # 168 steps × 5 percentiles (×AWR), steps 1…168
    low_in_by: list[float]  # 168 cumulative fractions P(week low set by hour h)
    high_in_by: list[float]
    mae_p: list[float]  # [p10, p50, p90] of per-week path minimum (×AWR)
    mfe_p: list[float]  # [p10, p50, p90] of per-week path maximum (×AWR)
    high_piv: list[float]  # [p50, p80] of (week_high − open)/open/awr14, ≥ 0
    low_piv: list[float]  # [p50, p80] of (open − week_low)/open/awr14, ≥ 0


@dataclass
class WeeklyConeBundle:
    """The 3 direction combos + population size. Deterministic per (symbol, week)."""

    combos: dict[str, WeeklyConeCombo]  # keyed "all" | "bull" | "bear"
    total_weeks: int


@dataclass
class CurrentWeekPath:
    """The forming week's normalized partial path — live, never cached."""

    points: list[float]  # normalized hourly closes (forming bar last)
    elapsed_h: int  # completed hourly bars this week (0–168)
    awr14_current: float
    week_open: float


@dataclass
class _WeekRecord:
    week: date  # the Monday
    direction: str  # "bull" | "bear" | "doji"
    norm_path: list[float]
    low_hour: int  # 1–168, earliest bar whose low equals the week low
    high_hour: int
    mae: float
    mfe: float
    high_mag: float
    low_mag: float


def _week_key(moment: datetime) -> date:
    """The Monday (UTC date) of the week containing `moment`."""
    return (moment - timedelta(days=moment.weekday())).date()


def _fetch_hourly(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int | None = None,
) -> dict[date, list[tuple[int, float, float, float, float]]]:
    """1h bars grouped by Monday-anchored week; each week's bars sorted by open_time."""
    sql = (
        "SELECT open_time, open, high, low, close FROM ohlcv "
        "WHERE symbol = ? AND timeframe = '1h' "
    )
    params: list[object] = [symbol]
    if start_ms is not None:
        sql += "AND open_time >= ? "
        params.append(start_ms)
    sql += "ORDER BY open_time"
    by_week: dict[date, list[tuple[int, float, float, float, float]]] = defaultdict(list)
    for open_time, o, h, lo, c in conn.execute(sql, params).fetchall():
        moment = datetime.fromtimestamp(int(open_time) / 1000, tz=UTC)
        by_week[_week_key(moment)].append(
            (int(open_time), float(o), float(h), float(lo), float(c))
        )
    return by_week


def _build_records(
    by_week: dict[date, list[tuple[int, float, float, float, float]]],
    current_week: date,
) -> tuple[list[_WeekRecord], float | None]:
    """Population records + AWR14 for `current_week` (None if < 14 complete priors).

    A week enters the population only if it is complete (168 bars), lies
    strictly before `current_week`, has a positive open, and has 14 complete
    prior weeks to form its trailing AWR window.
    """
    ranges: dict[date, float] = {}
    for w in sorted(by_week):
        if w >= current_week:
            continue
        bars = by_week[w]
        if len(bars) != WEEK_BARS:
            continue
        week_open = bars[0][1]
        if week_open <= 0:
            continue
        week_high = max(b[2] for b in bars)
        week_low = min(b[3] for b in bars)
        ranges[w] = (week_high - week_low) / week_open

    ordered = sorted(ranges)
    awr14: dict[date, float] = {}
    for i, w in enumerate(ordered):
        if i < _AWR_WINDOW:
            continue
        window = ordered[i - _AWR_WINDOW : i]
        val = sum(ranges[x] for x in window) / _AWR_WINDOW
        if val > 0:
            awr14[w] = val

    awr14_current: float | None = None
    if len(ordered) >= _AWR_WINDOW:
        val = sum(ranges[x] for x in ordered[-_AWR_WINDOW:]) / _AWR_WINDOW
        if val > 0:
            awr14_current = val

    records: list[_WeekRecord] = []
    for w in ordered:
        if w not in awr14:
            continue
        bars = by_week[w]
        week_open = bars[0][1]
        week_high = max(b[2] for b in bars)
        week_low = min(b[3] for b in bars)
        denom = week_open * awr14[w]
        norm_path = [(b[4] - week_open) / denom for b in bars]
        close_last = bars[-1][4]
        if close_last > week_open:
            direction = "bull"
        elif close_last < week_open:
            direction = "bear"
        else:
            direction = "doji"
        low_hour = next(i + 1 for i, b in enumerate(bars) if b[3] == week_low)
        high_hour = next(i + 1 for i, b in enumerate(bars) if b[2] == week_high)
        records.append(
            _WeekRecord(
                week=w,
                direction=direction,
                norm_path=norm_path,
                low_hour=low_hour,
                high_hour=high_hour,
                mae=min(norm_path),
                mfe=max(norm_path),
                high_mag=(week_high - week_open) / denom,
                low_mag=(week_open - week_low) / denom,
            )
        )
    return records, awr14_current


def _combo_from(direction: str, pop: list[_WeekRecord]) -> WeeklyConeCombo:
    n = len(pop)
    if n == 0:
        return WeeklyConeCombo(direction, 0, [], [], [], [], [], [], [])
    paths = np.array([r.norm_path for r in pop])
    bands = [
        [float(v) for v in row] for row in np.percentile(paths, BAND_PCTS, axis=0).T
    ]
    steps = range(1, WEEK_BARS + 1)
    low_in_by = [sum(1 for r in pop if r.low_hour <= h) / n for h in steps]
    high_in_by = [sum(1 for r in pop if r.high_hour <= h) / n for h in steps]
    mae_p = [float(v) for v in np.percentile([r.mae for r in pop], EXCURSION_PCTS)]
    mfe_p = [float(v) for v in np.percentile([r.mfe for r in pop], EXCURSION_PCTS)]
    high_piv = [float(v) for v in np.percentile([r.high_mag for r in pop], PIVOT_PCTS)]
    low_piv = [float(v) for v in np.percentile([r.low_mag for r in pop], PIVOT_PCTS)]
    return WeeklyConeCombo(
        direction=direction,
        n=n,
        bands=bands,
        low_in_by=low_in_by,
        high_in_by=high_in_by,
        mae_p=mae_p,
        mfe_p=mfe_p,
        high_piv=high_piv,
        low_piv=low_piv,
    )


def compute_weekly_cone(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> WeeklyConeBundle:
    """All-history weekly cone. Never raises on thin data (empty combos)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    current_week = _week_key(datetime.fromtimestamp(now / 1000, tz=UTC))
    records, _ = _build_records(_fetch_hourly(conn, symbol), current_week)
    combos: dict[str, WeeklyConeCombo] = {}
    for direction in DIRECTIONS:
        pop = [r for r in records if direction == "all" or r.direction == direction]
        combos[direction] = _combo_from(direction, pop)
    return WeeklyConeBundle(combos=combos, total_weeks=len(records))


def compute_current_week_path(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> CurrentWeekPath | None:
    """The forming week's normalized partial path. None when the week has no
    bars or the trailing AWR14 is unavailable (new listing / short history)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    current_week = _week_key(datetime.fromtimestamp(now / 1000, tz=UTC))
    start_ms = now - _CURRENT_FETCH_WEEKS * _WEEK_MS
    by_week = _fetch_hourly(conn, symbol, start_ms=start_ms)
    _, awr14_current = _build_records(by_week, current_week)
    if awr14_current is None:
        return None
    bars = by_week.get(current_week, [])
    if not bars:
        return None
    week_open = bars[0][1]
    if week_open <= 0:
        return None
    denom = week_open * awr14_current
    points = [(b[4] - week_open) / denom for b in bars]
    elapsed = sum(1 for b in bars if b[0] + _HOUR_MS <= now)
    return CurrentWeekPath(
        points=points,
        elapsed_h=min(elapsed, WEEK_BARS),
        awr14_current=awr14_current,
        week_open=week_open,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_weekly_cone.py -v`

Expected: 10 passed.

- [ ] **Step 5: Run the full gate**

Run, in the foreground, and state each result:

```bash
make lint-py
make typecheck
make test
make test-regression
```

Expected: lint clean, mypy clean, full suite green, 3/3 goldens unmoved.

- [ ] **Step 6: Commit**

```bash
git add analytics/stats/weekly_cone.py tests/test_weekly_cone.py
git commit -m "feat(stats): weekly path cone — pure computation module"
```

---

### Task 2: Wire the weekly cone into StatsBundle and the API

**Files:**

- Modify: `analytics/stats/bundle.py`
- Modify: `web/api/models/stats.py`
- Modify: `web/api/routers/stats.py`
- Test: `tests/test_web_stats_weekly_cone.py` (create)

**Interfaces:**

- Consumes from Task 1: `WeeklyConeBundle`, `WeeklyConeCombo`, `CurrentWeekPath`,
  `compute_weekly_cone`, `compute_current_week_path`.
- Produces, relied on by Task 3: JSON at `GET /api/stats/{symbol}` with
  `weekly_cone: {combos: {all|bull|bear: {...}}, total_weeks: int}` and optional
  `current_week_path: {points, elapsed_h, awr14_current, week_open} | null`.

**Background:** the daily cone is the exact template. `path_cone` is cached inside
`StatsBundle`; `today_path` is **not** cached and is injected by `_inject_live_fields`.
The weekly cone follows both halves of that pattern.

- [ ] **Step 1: Write the failing test**

**Fixture caution:** `compute_all` runs every per-stat compute, not just the cone, and
raises `ValueError` when a symbol has no usable OHLCV. The fixture below seeds 20 weeks of
1h bars plus 140 daily bars, which should satisfy the others — but if `compute_all` raises
or a sibling stat returns something degenerate, **extend the fixture rather than weakening
the assertion**, and say what you had to add.

Create `tests/test_web_stats_weekly_cone.py`:

```python
"""API-level tests for the weekly cone fields on GET /api/stats/{symbol}."""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats.bundle import compute_all
from web.api.models.stats import CurrentWeekPathResponse, WeeklyConeResponse

_SYMBOL = "WAPIUSDT"
_NOW = datetime(2026, 3, 4, 12, 30, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)
_CURRENT_WEEK = date(2026, 3, 2)


def _insert_week(
    conn: duckdb.DuckDBPyConnection, monday: date, *, k: float = 0.0, n_bars: int = 168
) -> None:
    base_ms = int(
        datetime(monday.year, monday.month, monday.day, tzinfo=UTC).timestamp() * 1000
    )
    close = 100.0 + k
    for h in range(n_bars):
        if h == 0:
            high, low = 100.5, 99.0
        elif h == 1:
            high, low = 101.0, 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_SYMBOL, "1h", base_ms + h * 3_600_000, 100.0, high, low, close, 100.0, 50.0],
        )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    for i in range(20):
        _insert_week(c, _CURRENT_WEEK - timedelta(weeks=20 - i), k=0.2)
    # 1d bars so compute_all's other stats have something to chew on.
    for i in range(140):
        day_ms = int(
            datetime(2025, 11, 1, tzinfo=UTC).timestamp() * 1000
        ) + i * 86_400_000
        c.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [_SYMBOL, "1d", day_ms, 100.0, 101.0, 99.0, 100.2, 100.0, 50.0],
        )
    return c


def test_bundle_carries_weekly_cone(conn: duckdb.DuckDBPyConnection) -> None:
    """StatsBundle gains a populated weekly_cone field."""
    bundle = compute_all(conn, _SYMBOL, 180)
    assert set(bundle.weekly_cone.combos) == {"all", "bull", "bear"}
    assert bundle.weekly_cone.total_weeks > 0


def test_weekly_cone_response_roundtrip() -> None:
    """The response model serializes and validates."""
    resp = WeeklyConeResponse(combos={}, total_weeks=0)
    assert WeeklyConeResponse.model_validate_json(resp.model_dump_json()) == resp


def test_current_week_path_response_optional() -> None:
    """The live overlay model is independently constructible."""
    cw = CurrentWeekPathResponse(
        points=[0.1, 0.2], elapsed_h=1, awr14_current=0.02, week_open=100.0
    )
    assert cw.elapsed_h == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. poetry run pytest tests/test_web_stats_weekly_cone.py -v`

Expected: `ImportError: cannot import name 'WeeklyConeResponse'`.

- [ ] **Step 3: Add the Pydantic models**

In `web/api/models/stats.py`, immediately after the existing `TodayPathResponse` class,
add:

```python
class WeeklyConeComboResponse(BaseModel):
    direction: str
    n: int
    bands: list[list[float]]
    low_in_by: list[float]
    high_in_by: list[float]
    mae_p: list[float]
    mfe_p: list[float]
    high_piv: list[float]
    low_piv: list[float]


class WeeklyConeResponse(BaseModel):
    combos: dict[str, WeeklyConeComboResponse]
    total_weeks: int


class CurrentWeekPathResponse(BaseModel):
    points: list[float]
    elapsed_h: int
    awr14_current: float
    week_open: float
```

Then add these two fields to the existing `StatsResponse` class body, alongside
`path_cone` and `today_path`:

```python
    weekly_cone: WeeklyConeResponse | None = None
    current_week_path: CurrentWeekPathResponse | None = None
```

Both are optional with a `None` default. This is deliberate: it keeps a stale cached
payload (written before this change) valid on re-validation rather than relying solely on
the recompute fallback.

- [ ] **Step 4: Add the field to StatsBundle**

In `analytics/stats/bundle.py`, add the import next to the existing `path_cone` import:

```python
from analytics.stats.weekly_cone import WeeklyConeBundle, compute_weekly_cone
```

Add to the `StatsBundle` dataclass body, after `path_cone`:

```python
    weekly_cone: WeeklyConeBundle
```

In `compute_all`, after the `path_cone = compute_path_cone(conn, symbol)` line:

```python
    weekly_cone = compute_weekly_cone(conn, symbol)
```

And add `weekly_cone=weekly_cone,` to the `StatsBundle(...)` constructor call.

- [ ] **Step 5: Map bundle → response and inject the live overlay**

In `web/api/routers/stats.py`, extend the imports from `analytics.stats.weekly_cone`:

```python
from analytics.stats.weekly_cone import compute_current_week_path
```

and from `web.api.models.stats`: `CurrentWeekPathResponse`, `WeeklyConeComboResponse`,
`WeeklyConeResponse`.

In `_bundle_to_response`, next to the existing `path_cone_resp` block, add:

```python
    weekly_cone_resp = WeeklyConeResponse(
        combos={
            key: WeeklyConeComboResponse(
                direction=c.direction,
                n=c.n,
                bands=c.bands,
                low_in_by=c.low_in_by,
                high_in_by=c.high_in_by,
                mae_p=c.mae_p,
                mfe_p=c.mfe_p,
                high_piv=c.high_piv,
                low_piv=c.low_piv,
            )
            for key, c in bundle.weekly_cone.combos.items()
        },
        total_weeks=bundle.weekly_cone.total_weeks,
    )
```

and pass `weekly_cone=weekly_cone_resp,` in the `StatsResponse(...)` construction.

In `_inject_live_fields`, directly after the existing today-path block, add:

```python
    # Current-week overlay — the forming week's normalized path (never cached)
    try:
        cw = compute_current_week_path(db, symbol)
        if cw is not None:
            response.current_week_path = CurrentWeekPathResponse(
                points=cw.points,
                elapsed_h=cw.elapsed_h,
                awr14_current=cw.awr14_current,
                week_open=cw.week_open,
            )
    except Exception:
        pass  # overlay is optional; never fail the stats response
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=. poetry run pytest tests/test_web_stats_weekly_cone.py tests/test_stats_lib.py tests/test_stats_end_ms.py -v`

Expected: all pass.

- [ ] **Step 7: Run the full gate**

```bash
make lint-py
make typecheck
make test
make test-regression
```

State each result.

- [ ] **Step 8: Commit**

```bash
git add analytics/stats/bundle.py web/api/models/stats.py web/api/routers/stats.py tests/test_web_stats_weekly_cone.py
git commit -m "feat(api): serve weekly cone + current-week overlay from /api/stats"
```

---

### Task 3: Stats UI — shared cone math + `WeeklyCone.svelte`

**Files:**

- Create: `web/ui/src/lib/cone.ts`
- Create: `web/ui/src/components/WeeklyCone.svelte`
- Modify: `web/ui/src/components/PathCone.svelte`
- Modify: `web/ui/src/pages/Stats.svelte`
- Modify: `web/ui/src/api.ts` (response types)

**Interfaces:**

- Consumes from Task 2: the `weekly_cone` and `current_week_path` JSON fields.
- Produces: nothing downstream.

**Load `/frontend-design` before starting** (project rule: always load it before any
Svelte/CSS/UI change). Read `web/ui/src/components/PathCone.svelte` in full first — this
task extracts from it.

This task has two halves and the order matters: **extract first, verify the daily cone is
unchanged, then build the weekly one.** Do not do both at once — a regression in the
shipped daily cone would otherwise be indistinguishable from a bug in the new component.

- [ ] **Step 1: Extract the pure math into `web/ui/src/lib/cone.ts`**

Move these out of `PathCone.svelte` unchanged in behavior. The exact current
implementations are in that file; copy them verbatim, add explicit types, and export:

- `px(...)` — the ADR/AWR-relative → price conversion used by the hover readout.
- `fmtPx(...)` — price formatting.
- the band-path (SVG `d` string) construction helper.

The module must be pure: no Svelte imports, no DOM access, no component state. It takes
numbers in and returns numbers/strings out. Give every export an explicit parameter and
return type — `make web-check` runs the TS type gate and `web-build` alone will not catch
a missing annotation.

While moving `fmtPx`, fix the known defect recorded in memory `path-cone-followups`: it
renders four decimals below 1000, and the #496 readout surfaces it five times per line,
so a mid-priced asset reads `p90 152.3400 · p75 149.8800 · …`. Choose a decimal count
from the value's magnitude instead. This is the one intentional behavior change in this
step; everything else must be byte-for-byte equivalent.

- [ ] **Step 2: Point `PathCone.svelte` at the shared module**

Replace the moved local functions with imports from `../lib/cone`. No other change to
that component.

- [ ] **Step 3: Verify the daily cone is unchanged**

```bash
make web-check
make web-build
```

Expected: both clean. Then run the dev server (`make web-dev`), open the Stats tab, and
confirm the **daily** cone still renders with its bands, today overlay, and hover readout
working, and that prices in the readout are unchanged apart from the decimal fix. State
what you observed.

- [ ] **Step 4: Commit the extraction separately**

```bash
git add web/ui/src/lib/cone.ts web/ui/src/components/PathCone.svelte
git commit -m "refactor(ui): extract shared cone math into lib/cone.ts"
```

- [ ] **Step 5: Add the API response types**

In `web/ui/src/api.ts`, add types mirroring Task 2's models exactly
(`WeeklyConeCombo`, `WeeklyCone`, `CurrentWeekPath`) and add the two optional fields to
the existing stats response type.

- [ ] **Step 6: Build `WeeklyCone.svelte`**

A sibling component, **not** an extension of `PathCone.svelte`. Requirements:

- x-axis: 168 steps, labelled by weekday (`Mon`…`Sun`) with a gridline at each day
  boundary (steps 1, 25, 49, 73, 97, 121, 145).
- Renders the selected direction combo (`bull` / `bear`) as the filled percentile cone,
  **with the `all` combo drawn underneath as the unconditional reference band** — visually
  subordinate (thinner, lower opacity, or outline-only). This pairing is the point of the
  chart; do not make `all` a selectable alternative that replaces the conditional cone.
- Overlays `current_week_path` when present, truncated at `elapsed_h`.
- Direction selector for `bull` / `bear`. No weekday selector.
- Shows `n` for the displayed combo and `total_weeks` for the population.
- Hover readout reusing `px`/`fmtPx` from `lib/cone.ts`, so weekly and daily prices agree.
- Empty state when `total_weeks === 0` or the combo's `n === 0`: render a short "not
  enough complete weeks yet" message, not a broken axis.
- **Copy constraint:** no forecast language (spec §3). Label the chart as showing what
  past weeks of that outcome did — e.g. "Paths of weeks that closed bull (n=172)" — never
  "expected path".

- [ ] **Step 7: Mount it in `Stats.svelte`**

Add the card following the existing card pattern on that page, directly after the daily
path cone so the two read top-down as week → day.

- [ ] **Step 8: Verify**

```bash
make web-check
make web-build
```

Both must be clean. Then `make web-dev`, open Stats, and confirm: the weekly cone renders,
the direction toggle switches the conditional cone while the reference band stays, the
current-week overlay appears and stops at the right hour, and the hover readout shows
prices consistent with the daily cone. State what you observed.

- [ ] **Step 9: Commit**

```bash
git add web/ui/src/lib/cone.ts web/ui/src/components/WeeklyCone.svelte web/ui/src/pages/Stats.svelte web/ui/src/api.ts
git commit -m "feat(ui): weekly path cone card on the Stats tab"
```

---

### Task 4: Brief weekly-state block

**Files:**

- Create: `analytics/brief/weekly.py`
- Modify: `analytics/brief/types.py`
- Modify: `analytics/brief/bundle.py`
- Modify: `analytics/brief/render.py`
- Test: `tests/test_brief_weekly.py` (create)

**Interfaces:**

- Consumes from Task 1: `WeeklyConeBundle`, `CurrentWeekPath`, `compute_weekly_cone`,
  `compute_current_week_path`.
- Produces: `SymbolPanel.weekly: WeeklyState | None`.

**Background:** read `analytics/brief/sessions.py` and `analytics/brief/render.py`'s
`_session_lines` first. Brief adapters are **conn-free** — the bundle owns every DB call
and passes computed inputs in. The adapter returns `(state | None, notes)` where notes are
**unprefixed** (the bundle prepends the symbol). A failed sub-block becomes a health note,
never an exception.

- [ ] **Step 1: Write the failing test**

Create `tests/test_brief_weekly.py`:

```python
"""Tests for analytics/brief/weekly.py (conn-free adapter)."""

from analytics.brief.weekly import build_weekly_state
from analytics.stats.weekly_cone import (
    CurrentWeekPath,
    WeeklyConeBundle,
    WeeklyConeCombo,
)


def _combo(direction: str, n: int) -> WeeklyConeCombo:
    return WeeklyConeCombo(
        direction=direction,
        n=n,
        bands=[[-1.0, -0.5, 0.0, 0.5, 1.0]] * 168,
        low_in_by=[i / 168 for i in range(1, 169)],
        high_in_by=[i / 168 for i in range(1, 169)],
        mae_p=[-1.0, -0.4, -0.1],
        mfe_p=[0.1, 0.4, 1.0],
        high_piv=[0.5, 0.9],
        low_piv=[0.5, 0.9],
    )


def _bundle() -> WeeklyConeBundle:
    return WeeklyConeBundle(
        combos={"all": _combo("all", 344), "bull": _combo("bull", 172), "bear": _combo("bear", 172)},
        total_weeks=344,
    )


def test_none_when_no_current_path() -> None:
    """No forming-week path -> no state, one note."""
    state, notes = build_weekly_state(cone=_bundle(), current=None)
    assert state is None
    assert len(notes) == 1


def test_none_when_population_empty() -> None:
    """Empty cone -> no state, one note."""
    empty = WeeklyConeBundle(combos={}, total_weeks=0)
    current = CurrentWeekPath(points=[0.2], elapsed_h=1, awr14_current=0.02, week_open=100.0)
    state, notes = build_weekly_state(cone=empty, current=current)
    assert state is None
    assert len(notes) == 1


def test_direction_from_current_path() -> None:
    """A positive last point reads as a bull path so far."""
    current = CurrentWeekPath(
        points=[0.0] * 62 + [0.4], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, notes = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    assert state.path_direction == "bull"
    assert state.elapsed_h == 62
    assert state.total_bars == 168
    assert state.n_conditional == 172
    assert state.n_unconditional == 344
    assert notes == []


def test_percentile_within_bands() -> None:
    """Percentile rank is reported for both the conditional and all pools."""
    current = CurrentWeekPath(
        points=[0.0] * 62 + [0.6], elapsed_h=62, awr14_current=0.02, week_open=100.0
    )
    state, _ = build_weekly_state(cone=_bundle(), current=current)
    assert state is not None
    assert 0 <= state.pct_conditional <= 100
    assert 0 <= state.pct_unconditional <= 100
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_weekly.py -v`

Expected: `ModuleNotFoundError: No module named 'analytics.brief.weekly'`.

- [ ] **Step 3: Add the dataclass**

In `analytics/brief/types.py`, add near the other state dataclasses:

```python
@dataclass
class WeeklyState:
    """Where the forming week sits inside the weekly cone (M5, conditional-on-outcome)."""

    path_direction: str  # "bull" | "bear" | "flat" — the week SO FAR, not a forecast
    elapsed_h: int
    total_bars: int
    norm_now: float  # current normalized position (×AWR)
    pct_conditional: float  # percentile within same-direction weeks at this hour
    pct_unconditional: float  # percentile within all weeks at this hour
    n_conditional: int
    n_unconditional: int
    low_hour: int | None  # hour the week's low has been set so far
    high_hour: int | None
    low_in_by_now: float  # fraction of same-direction weeks that had set their low by now
```

and add to `SymbolPanel`, after `external`:

```python
    weekly: WeeklyState | None = None
```

Update `error_panel` only if it constructs `SymbolPanel` positionally — the new field has
a default, so a keyword construction needs no change.

- [ ] **Step 4: Write the adapter**

Create `analytics/brief/weekly.py`:

```python
"""Forming-week state for the brief panel (M5 adapter).

Conn-free: consumes a precomputed WeeklyConeBundle and CurrentWeekPath (the
bundle owns the DB calls). Returns (state, notes) with UNPREFIXED notes; a
failure degrades to a note and a None state, never an exception.

The cone is conditional on OUTCOME. `path_direction` describes what the week
has done so far — it is NOT a claim about how the week will close.
"""

from __future__ import annotations

from analytics.brief.types import WeeklyState
from analytics.stats.weekly_cone import CurrentWeekPath, WeeklyConeBundle


def _percentile_of(bands_at_hour: list[float], value: float) -> float:
    """Approximate percentile of `value` against the p10/25/50/75/90 ladder.

    Linear interpolation between adjacent band edges; clamped to [0, 100].
    """
    ladder = [10.0, 25.0, 50.0, 75.0, 90.0]
    if value <= bands_at_hour[0]:
        return 0.0 if value < bands_at_hour[0] else ladder[0]
    if value >= bands_at_hour[-1]:
        return 100.0 if value > bands_at_hour[-1] else ladder[-1]
    for i in range(len(ladder) - 1):
        lo, hi = bands_at_hour[i], bands_at_hour[i + 1]
        if lo <= value <= hi:
            if hi == lo:
                return ladder[i]
            frac = (value - lo) / (hi - lo)
            return ladder[i] + frac * (ladder[i + 1] - ladder[i])
    return ladder[-1]


def build_weekly_state(
    *,
    cone: WeeklyConeBundle,
    current: CurrentWeekPath | None,
) -> tuple[WeeklyState | None, list[str]]:
    """(state, notes). None state whenever the week or the cone is unusable."""
    if current is None or not current.points:
        return None, ["weekly cone: no forming-week path (short history?)"]
    if cone.total_weeks == 0 or "all" not in cone.combos:
        return None, ["weekly cone: no complete weeks in population"]

    norm_now = current.points[-1]
    if norm_now > 0:
        direction = "bull"
    elif norm_now < 0:
        direction = "bear"
    else:
        direction = "flat"

    all_combo = cone.combos["all"]
    cond_combo = cone.combos.get(direction, all_combo)
    if not all_combo.bands:
        return None, ["weekly cone: population has no bands"]

    idx = min(max(current.elapsed_h - 1, 0), len(all_combo.bands) - 1)
    pct_uncond = _percentile_of(all_combo.bands[idx], norm_now)
    pct_cond = (
        _percentile_of(cond_combo.bands[idx], norm_now)
        if cond_combo.bands
        else pct_uncond
    )

    low_hour = (
        current.points.index(min(current.points)) + 1 if current.points else None
    )
    high_hour = (
        current.points.index(max(current.points)) + 1 if current.points else None
    )
    low_in_by_now = (
        cond_combo.low_in_by[idx] if cond_combo.low_in_by else all_combo.low_in_by[idx]
    )

    return (
        WeeklyState(
            path_direction=direction,
            elapsed_h=current.elapsed_h,
            total_bars=len(all_combo.bands),
            norm_now=norm_now,
            pct_conditional=pct_cond,
            pct_unconditional=pct_uncond,
            n_conditional=cond_combo.n,
            n_unconditional=all_combo.n,
            low_hour=low_hour,
            high_hour=high_hour,
            low_in_by_now=low_in_by_now,
        ),
        [],
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_weekly.py -v`

Expected: 4 passed.

- [ ] **Step 6: Wire into the brief bundle**

In `analytics/brief/bundle.py`, inside `_compute_panel`, after the `external` block and
before the `SymbolPanel(...)` construction:

```python
    try:
        weekly_cone = compute_weekly_cone(conn, symbol, now_ms=as_of)
        current_week = compute_current_week_path(conn, symbol, now_ms=as_of)
        weekly, wk_notes = build_weekly_state(cone=weekly_cone, current=current_week)
    except Exception as exc:  # weekly block is optional
        weekly, wk_notes = None, [f"weekly cone failed ({exc})"]
    notes.extend(f"{symbol}: {n}" for n in wk_notes)
```

Add `weekly=weekly,` to the `SymbolPanel(...)` construction, and the imports at the top of
the file.

- [ ] **Step 7: Render it**

In `analytics/brief/render.py`, add alongside the other block renderers:

```python
def _weekly_lines(state: WeeklyState | None) -> list[str]:
    """Forming-week position inside the weekly cone. Conditional on outcome."""
    if state is None:
        return []
    day = _DOW[min((state.elapsed_h - 1) // 24, 6)] if state.elapsed_h > 0 else _DOW[0]
    hod = (state.elapsed_h - 1) % 24 if state.elapsed_h > 0 else 0
    head = (
        f"Week  {state.path_direction} path so far · "
        f"h{state.elapsed_h}/{state.total_bars} ({day} {hod:02d}:00 UTC) · "
        f"{state.norm_now:+.2f}×AWR"
    )
    ranks = (
        f"      p{state.pct_conditional:.0f} of weeks that closed "
        f"{state.path_direction} (n={state.n_conditional}) · "
        f"p{state.pct_unconditional:.0f} unconditional (n={state.n_unconditional})"
    )
    timing = (
        f"      low so far h{state.low_hour} · high so far h{state.high_hour} · "
        f"{state.low_in_by_now:.0%} of those weeks had set their low by now"
    )
    return [head, ranks, timing]
```

Call it from `_panel_lines` where the weekly block should appear — after the session
lines, before the external lines. Follow the existing `lines.extend(...)` idiom.

- [ ] **Step 8: Run the full gate**

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

State each result. `tests/test_brief_render.py` and `tests/test_brief_bundle.py` must
still pass — if a snapshot-style assertion there breaks because a new line appeared, that
is expected and the fixture should be updated; say so explicitly rather than silently
editing it.

- [ ] **Step 9: Commit**

```bash
git add analytics/brief/weekly.py analytics/brief/types.py analytics/brief/bundle.py analytics/brief/render.py tests/test_brief_weekly.py
git commit -m "feat(brief): forming-week state block from the weekly cone"
```

---

### Task 5: Brief monthly context line

**Files:**

- Create: `analytics/brief/monthly.py`
- Modify: `analytics/brief/types.py`
- Modify: `analytics/brief/bundle.py`
- Modify: `analytics/brief/render.py`
- Test: `tests/test_brief_monthly.py` (create)

**Interfaces:**

- Consumes: the `completed_1d` DataFrame the brief bundle already fetches, plus the
  existing `SeasonalityStrip` on the panel.
- Produces: `SymbolPanel.monthly: MonthlyContext | None`.

**This is deliberately not a cone** (spec §2.1): ~90 months split to ~45 per direction is
too thin for percentile bands. Three descriptive numbers only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_brief_monthly.py`:

```python
"""Tests for analytics/brief/monthly.py (conn-free adapter)."""

from datetime import UTC, datetime

import pandas as pd

from analytics.brief.monthly import build_monthly_context


def _daily(n_days: int, start: datetime, step: float) -> pd.DataFrame:
    rows = []
    for i in range(n_days):
        ts = int((start.timestamp() + i * 86_400) * 1000)
        close = 100.0 + i * step
        rows.append(
            {
                "open_time": ts,
                "open": close - step,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
            }
        )
    return pd.DataFrame(rows)


def test_none_on_empty_frame() -> None:
    """No daily bars -> no context, one note."""
    ctx, notes = build_monthly_context(
        completed_1d=pd.DataFrame(),
        as_of_ms=int(datetime(2026, 7, 21, tzinfo=UTC).timestamp() * 1000),
    )
    assert ctx is None
    assert len(notes) == 1


def test_month_to_date_return_and_range_position() -> None:
    """A rising month reports a positive MTD return and a high range position."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _daily(400, start, 0.5)
    as_of = int(datetime(2026, 2, 5, tzinfo=UTC).timestamp() * 1000)
    ctx, notes = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    assert ctx.mtd_return_pct > 0
    assert 0.0 <= ctx.range_position <= 1.0
    assert ctx.n_months > 0
    assert notes == []


def test_range_position_none_when_flat() -> None:
    """A month whose high equals its low reports range_position None."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    df = _daily(400, start, 0.0)
    df["high"] = df["close"]
    df["low"] = df["close"]
    as_of = int(datetime(2026, 2, 5, tzinfo=UTC).timestamp() * 1000)
    ctx, _ = build_monthly_context(completed_1d=df, as_of_ms=as_of)
    assert ctx is not None
    assert ctx.range_position is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_monthly.py -v`

Expected: `ModuleNotFoundError: No module named 'analytics.brief.monthly'`.

- [ ] **Step 3: Add the dataclass**

In `analytics/brief/types.py`:

```python
@dataclass
class MonthlyContext:
    """Descriptive monthly context — three numbers, deliberately NOT a cone."""

    mtd_return_pct: float
    mtd_elapsed_frac: float  # 0–1, how much of the month has elapsed
    pct_of_months: float  # rank of MTD return among COMPLETED prior months
    n_months: int
    range_position: float | None  # (price − low) / (high − low), None when flat
```

and on `SymbolPanel`, after `weekly`:

```python
    monthly: MonthlyContext | None = None
```

- [ ] **Step 4: Write the adapter**

Create `analytics/brief/monthly.py`:

```python
"""Monthly context for the brief panel (M5) — descriptive, NOT a cone.

Conn-free: consumes the completed-1d frame the bundle already fetched.
Deliberately three numbers: ~90 months of history split by direction leaves
~45 per cell, too thin for percentile bands (spec §2.1).

The month-to-date return is compared against COMPLETED prior months, which is
an apples-to-oranges comparison by construction; `mtd_elapsed_frac` is carried
so the renderer can disclose it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from analytics.brief.types import MonthlyContext


def build_monthly_context(
    *,
    completed_1d: pd.DataFrame,
    as_of_ms: int,
) -> tuple[MonthlyContext | None, list[str]]:
    """(context, notes). None whenever there is nothing meaningful to say."""
    if completed_1d.empty or "open_time" not in completed_1d:
        return None, ["monthly context: no completed 1d bars"]

    df = completed_1d.copy()
    ts = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["ym"] = ts.dt.strftime("%Y-%m")
    as_of = datetime.fromtimestamp(as_of_ms / 1000, tz=UTC)
    current_ym = as_of.strftime("%Y-%m")

    current = df[df["ym"] == current_ym]
    if current.empty:
        return None, ["monthly context: no bars in the current month"]

    month_open = float(current.iloc[0]["open"])
    if month_open <= 0:
        return None, ["monthly context: non-positive month open"]
    price = float(current.iloc[-1]["close"])
    mtd_return_pct = (price - month_open) / month_open * 100.0

    month_high = float(current["high"].max())
    month_low = float(current["low"].min())
    range_position: float | None = None
    if month_high > month_low:
        range_position = (price - month_low) / (month_high - month_low)

    prior_returns: list[float] = []
    for ym, grp in df[df["ym"] != current_ym].groupby("ym", sort=True):
        o = float(grp.iloc[0]["open"])
        if o > 0:
            prior_returns.append((float(grp.iloc[-1]["close"]) - o) / o * 100.0)

    n_months = len(prior_returns)
    if n_months == 0:
        pct_of_months = 50.0
    else:
        below = sum(1 for r in prior_returns if r < mtd_return_pct)
        pct_of_months = below / n_months * 100.0

    days_in_month = pd.Period(current_ym, freq="M").days_in_month
    elapsed_frac = min(as_of.day / days_in_month, 1.0)

    return (
        MonthlyContext(
            mtd_return_pct=mtd_return_pct,
            mtd_elapsed_frac=elapsed_frac,
            pct_of_months=pct_of_months,
            n_months=n_months,
            range_position=range_position,
        ),
        [],
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONPATH=. poetry run pytest tests/test_brief_monthly.py -v`

Expected: 3 passed.

- [ ] **Step 6: Wire into the bundle**

In `analytics/brief/bundle.py`, after the weekly block from Task 4:

```python
    try:
        monthly, mo_notes = build_monthly_context(
            completed_1d=completed_1d, as_of_ms=as_of
        )
    except Exception as exc:  # monthly block is optional
        monthly, mo_notes = None, [f"monthly context failed ({exc})"]
    notes.extend(f"{symbol}: {n}" for n in mo_notes)
```

Add `monthly=monthly,` to the `SymbolPanel(...)` construction plus the import.

- [ ] **Step 7: Render it**

In `analytics/brief/render.py`:

```python
def _monthly_lines(
    ctx: MonthlyContext | None, strip: SeasonalityStrip | None
) -> list[str]:
    """Descriptive monthly context. Not a distribution, not a forecast."""
    if ctx is None:
        return []
    rng = "—" if ctx.range_position is None else f"{ctx.range_position:.2f}"
    head = (
        f"Month {ctx.mtd_return_pct:+.1f}% · p{ctx.pct_of_months:.0f} of "
        f"{ctx.n_months} completed months · range position {rng} "
        f"({ctx.mtd_elapsed_frac:.0%} elapsed)"
    )
    return [head]
```

Call it from `_panel_lines` immediately **before** the weekly lines, so the panel reads
month → week → day. Pass the panel's existing `seasonality` strip; if the seasonality
strip already renders its own line elsewhere in the panel, do **not** duplicate it here —
pass it and ignore it, or drop the parameter. Decide by reading `_strip_lines` and its
call site, and say which you chose and why.

**Copy constraint:** wherever calendar-month seasonality is displayed, its `n` must be
visible. Seven years of history is seven observations per calendar month, and it must read
as the anecdote it is (spec §6.3).

- [ ] **Step 8: Run the full gate**

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

State each result.

- [ ] **Step 9: Commit**

```bash
git add analytics/brief/monthly.py analytics/brief/types.py analytics/brief/bundle.py analytics/brief/render.py tests/test_brief_monthly.py
git commit -m "feat(brief): monthly context line (descriptive, not a cone)"
```

---

## Out of scope (do not build)

- **No monthly cone** — spec §2.1.
- **No F2 trade-card wiring** — gated on H10; a card rubric change is a `card-v3`
  `PROMPT_VERSION` bump that breaks comparability with logged cards (spec §2.2).
- **No H10 research** — the predictive question is a separate audit-tool study
  (spec §8).
- **No 4h/42-step fallback** — the coverage pre-check passed at 100% (spec §5.2).
- **No changes to `analytics/stats/path_cone.py`.**
- **No new detector.** This is a context layer; the frozen-detector list is untouched.
