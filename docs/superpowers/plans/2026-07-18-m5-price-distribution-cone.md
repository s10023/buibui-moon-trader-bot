# M5 Daily Price-Distribution Cone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Daily Distance card with a conditional daily
path-distribution cone (direction × weekday percentile bands over
ADR-normalized hourly paths) + timing/excursion distributions + projection
pivots + live today-overlay, as a hero section on the Stats page.

**Spec:** `docs/superpowers/specs/2026-07-18-m5-price-distribution-cone-design.md`
(user-approved). Read it before starting any task.

**Architecture:** New pure compute module `analytics/stats/path_cone.py`
(all methodology lives here, unit-tested against synthetic OHLCV) joins the
cached `StatsBundle`; the live today-path is injected via the router's
existing `_inject_live_fields()` never-cached path; UI is a new SVG Svelte
component `PathCone.svelte` hosted in a hero card at the top of
`Stats.svelte`. `daily_distance` (compute + API model + card) is deleted in
the same arc.

**Tech Stack:** Python 3.11 / DuckDB / numpy / FastAPI + Pydantic /
Svelte 5 runes + plain SVG (no chart lib).

## Global Constraints

- mypy strict: every function fully annotated (`-> None` for test methods).
- ruff format + lint must pass: `make lint-py`.
- Tests: pytest, in-memory DuckDB only (`duckdb.connect(":memory:")`),
  never the real `analytics.db`, no network.
- **Run all tests in the FOREGROUND** (no background test invocations).
- Timezone: period boundary = UTC day (Binance daily candle); MYT (+8, no
  DST) is display-only in the UI axis labels.
- Percentiles: numpy default linear interpolation.
- Conventional commits (`feat:` / `test:` / `docs:`); execution branch:
  `feat/m5-path-cone` off `main`.
- Definition of Done for the arc: `make lint-py` ✓ · `make typecheck` ✓ ·
  `make test` green · `make test-regression` goldens unmoved ·
  `make web-build` clean.

---

### Task 1: `analytics/stats/path_cone.py` — pure compute module

**Files:**

- Create: `analytics/stats/path_cone.py`
- Create: `tests/test_path_cone.py`

**Interfaces:**

- Consumes: `ohlcv` table (`symbol TEXT, timeframe TEXT, open_time BIGINT
  ms, open/high/low/close DOUBLE, volume DOUBLE, taker_buy_volume DOUBLE`),
  `analytics.data_store.init_schema` (tests only).
- Produces (Tasks 2–3 rely on these exact names):
  - `compute_path_cone(conn: duckdb.DuckDBPyConnection, symbol: str, *, now_ms: int | None = None) -> PathConeBundle`
  - `compute_today_path(conn: duckdb.DuckDBPyConnection, symbol: str, *, now_ms: int | None = None) -> TodayPath | None`
  - Dataclasses `ConeCombo`, `PathConeBundle`, `TodayPath` (fields below).
  - Constants `DIRECTIONS = ("all", "bull", "bear")`,
    `WEEKDAY_KEYS = ("all", "mon", "tue", "wed", "thu", "fri", "sat", "sun")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_path_cone.py` with exactly this content:

```python
"""Tests for analytics/stats/path_cone.py using in-memory DuckDB.

Synthetic-day construction: every bar opens at 100 and closes at 100 + k.
Bar 1 carries the day low (99); bar 2 carries the day high (101). With
k ∈ [−0.5, +0.5] every other bar stays inside [99.5, 100.5], so every
complete day's range is exactly (101 − 99) / 100 = 0.02 and the trailing
ADR14 of any day with 14 complete predecessors is exactly 0.02. A day's
normalized close path is then constant at k / 2:
((100 + k − 100) / 100) / 0.02 = k / 2.
"""

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from analytics.data_store import init_schema
from analytics.stats.path_cone import (
    PathConeBundle,
    compute_path_cone,
    compute_today_path,
)

_SYMBOL = "CONEUSDT"
# Fixed clock: Monday 2026-03-02 12:30 UTC — all tests pass now_ms explicitly.
_NOW = datetime(2026, 3, 2, 12, 30, tzinfo=UTC)
_NOW_MS = int(_NOW.timestamp() * 1000)


def _insert_day(
    conn: duckdb.DuckDBPyConnection,
    day: date,
    *,
    k: float = 0.0,
    n_bars: int = 24,
    wide_high: float | None = None,
) -> None:
    """Insert one synthetic UTC day of 1h bars (see module docstring)."""
    base_ms = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
    close = 100.0 + k
    for h in range(n_bars):
        if h == 0:
            high, low = 100.5, 99.0
        elif h == 1:
            high = wide_high if wide_high is not None else 101.0
            low = 99.5
        else:
            high, low = max(100.5, close), min(99.5, close)
        conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol, timeframe, open_time, open, high, low, close, volume, "
            "taker_buy_volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                _SYMBOL,
                "1h",
                base_ms + h * 3_600_000,
                100.0,
                high,
                low,
                close,
                100.0,
                50.0,
            ],
        )


def _seed_warmup(conn: duckdb.DuckDBPyConnection, start: date, n: int = 14) -> None:
    """n consecutive complete doji (k=0) days starting at `start`."""
    for i in range(n):
        _insert_day(conn, start + timedelta(days=i))


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_cone_bands_hand_computed(conn: duckdb.DuckDBPyConnection) -> None:
    """Median band equals the hand-computed median of the normalized closes."""
    start = date(2026, 1, 5)  # Monday
    _seed_warmup(conn, start)  # Jan 5 … Jan 18 complete, all range 2%
    _insert_day(conn, date(2026, 1, 19), k=0.5)  # Monday, bull, norm +0.25
    _insert_day(conn, date(2026, 1, 20), k=0.25)  # Tuesday, bull, norm +0.125
    _insert_day(conn, date(2026, 1, 21), k=-0.5)  # Wednesday, bear, norm −0.25
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert isinstance(cone, PathConeBundle)
    assert cone.total_days == 3
    combo = cone.combos["all|all"]
    assert combo.n == 3
    assert len(combo.bands) == 24
    for step in range(24):
        assert combo.bands[step][2] == pytest.approx(0.125)  # p50
    assert cone.combos["bull|all"].n == 2
    assert cone.combos["bear|all"].n == 1
    assert cone.combos["bull|mon"].n == 1
    assert cone.combos["bear|wed"].n == 1
    empty = cone.combos["bear|mon"]
    assert empty.n == 0
    assert empty.bands == []
    assert empty.low_in_by == []
    assert len(cone.combos) == 24  # 3 directions × 8 weekday keys


def test_timing_distributions(conn: duckdb.DuckDBPyConnection) -> None:
    """Bar 1 always carries the day low, bar 2 the day high (earliest-tie)."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 19), k=0.5)
    _insert_day(conn, date(2026, 1, 20), k=-0.5)
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    combo = cone.combos["all|all"]
    assert combo.low_in_by[0] == pytest.approx(1.0)  # low set by hour 1
    assert combo.high_in_by[0] == pytest.approx(0.0)  # high not yet at hour 1
    assert combo.high_in_by[1] == pytest.approx(1.0)  # high set by hour 2
    assert combo.low_in_by[23] == pytest.approx(1.0)  # cumulative → 1.0


def test_pivots_and_excursions(conn: duckdb.DuckDBPyConnection) -> None:
    """Pivot magnitudes and excursion percentiles match hand computation."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 19), k=0.5)  # bull, norm +0.25
    _insert_day(conn, date(2026, 1, 20), k=0.25)  # bull, norm +0.125
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    combo = cone.combos["bull|all"]
    # high magnitude = (101−100)/100/0.02 = 0.5 on every day; same for low.
    assert combo.high_piv[0] == pytest.approx(0.5)  # p50
    assert combo.high_piv[1] == pytest.approx(0.5)  # p80
    assert combo.low_piv[0] == pytest.approx(0.5)
    # constant paths → MFE = norm; np.percentile([0.125, 0.25], 50) = 0.1875
    assert combo.mfe_p[1] == pytest.approx(0.1875)
    assert combo.mae_p[1] == pytest.approx(0.1875)


def test_causality_perturbation(conn: duckdb.DuckDBPyConnection) -> None:
    """Inflating day d's range must not change day d's own normalization —
    only later days' (their ADR windows contain d)."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)  # Jan 5 … Jan 18
    _insert_day(conn, date(2026, 1, 20), k=0.5)  # Tuesday — to be perturbed
    _insert_day(conn, date(2026, 1, 21), k=0.25)  # Wednesday — downstream
    before = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    # Blow up Tuesday's range via a huge bar-2 wick: same open, same closes.
    _insert_day(conn, date(2026, 1, 20), k=0.5, wide_high=110.0)
    after = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert after.combos["all|tue"].bands == before.combos["all|tue"].bands
    assert after.combos["all|wed"].bands != before.combos["all|wed"].bands


def test_exclusions(conn: duckdb.DuckDBPyConnection) -> None:
    """Incomplete days drop; doji counts only in 'all'; today never counts."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 19), k=0.5)  # bull
    _insert_day(conn, date(2026, 1, 20), k=0.25, n_bars=23)  # incomplete
    _insert_day(conn, date(2026, 1, 21))  # doji (close == open)
    _insert_day(conn, date(2026, 3, 2), k=0.5, n_bars=13)  # "today"
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert cone.total_days == 2  # Jan 19 + Jan 21 (warmup has no ADR window)
    assert cone.combos["all|all"].n == 2
    assert cone.combos["bull|all"].n == 1  # doji excluded from bull/bear
    assert cone.combos["bear|all"].n == 0


def test_today_path(conn: duckdb.DuckDBPyConnection) -> None:
    """Today's partial path normalizes by trailing ADR14 and counts only
    completed bars in elapsed_h (forming bar still contributes a point)."""
    start = date(2026, 2, 16)  # Monday; Feb 16 … Mar 1 = 14 complete days
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 3, 2), k=0.5, n_bars=13)  # today, 13 bars
    tp = compute_today_path(conn, _SYMBOL, now_ms=_NOW_MS)
    assert tp is not None
    assert tp.adr14_today == pytest.approx(0.02)
    assert tp.today_open == pytest.approx(100.0)
    # now = 12:30 UTC → bars 00:00–11:00 are complete (12); 12:00 is forming.
    assert tp.elapsed_h == 12
    assert len(tp.points) == 13
    assert tp.points[-1] == pytest.approx(0.25)  # (100.5−100)/(100×0.02)


def test_today_path_insufficient_history(conn: duckdb.DuckDBPyConnection) -> None:
    """Fewer than 14 complete prior days → overlay unavailable (None)."""
    _insert_day(conn, date(2026, 3, 1))
    _insert_day(conn, date(2026, 3, 2), k=0.5, n_bars=13)
    assert compute_today_path(conn, _SYMBOL, now_ms=_NOW_MS) is None


def test_no_data_returns_empty_bundle(conn: duckdb.DuckDBPyConnection) -> None:
    """A symbol with zero OHLCV yields an all-empty bundle, no exception."""
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert cone.total_days == 0
    assert all(c.n == 0 for c in cone.combos.values())


def test_extreme_tie_earliest_hour_wins(conn: duckdb.DuckDBPyConnection) -> None:
    """Two bars sharing the day low → the EARLIEST hour is the timing hour."""
    start = date(2026, 1, 5)
    _seed_warmup(conn, start)
    _insert_day(conn, date(2026, 1, 19), k=0.5)
    # Give hour 6 (bar index 5) the same 99.0 low as bar 1 — a tie.
    base_ms = int(datetime(2026, 1, 19, tzinfo=UTC).timestamp() * 1000)
    conn.execute(
        "UPDATE ohlcv SET low = 99.0 "
        "WHERE symbol = ? AND timeframe = '1h' AND open_time = ?",
        [_SYMBOL, base_ms + 5 * 3_600_000],
    )
    cone = compute_path_cone(conn, _SYMBOL, now_ms=_NOW_MS)
    assert cone.combos["all|all"].low_in_by[0] == pytest.approx(1.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (foreground): `poetry run pytest tests/test_path_cone.py -v`
Expected: FAIL at import — `ModuleNotFoundError`/`ImportError` for
`analytics.stats.path_cone`.

- [ ] **Step 3: Write the implementation**

Create `analytics/stats/path_cone.py` with exactly this content:

```python
"""Daily price-distribution cone — conditional path percentiles over 1h history.

Spec: docs/superpowers/specs/2026-07-18-m5-price-distribution-cone-design.md

- Period = UTC day (matches the Binance daily candle; MYT is display-only).
- Each complete historical day's hourly-close path is normalized by that
  day's OWN trailing ADR14 (mean (high−low)/open over the last 14 complete
  UTC days strictly before it) → ×ADR units comparable across vol regimes.
- Per direction × weekday combo (3 × 8 = 24): per-step percentile bands,
  low/high timing distributions, excursion percentiles, and pivot-magnitude
  percentiles. All combos are computed in one pass and cached via the
  StatsBundle; the today-path overlay is computed fresh (never cached).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime

import duckdb
import numpy as np

BAND_PCTS = (10.0, 25.0, 50.0, 75.0, 90.0)  # bands row order p10…p90
EXCURSION_PCTS = (10.0, 50.0, 90.0)
PIVOT_PCTS = (50.0, 80.0)
DIRECTIONS = ("all", "bull", "bear")
WEEKDAY_KEYS = ("all", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WD = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_ADR_WINDOW = 14
_HOUR_MS = 3_600_000
_TODAY_FETCH_DAYS = 45  # covers 14 complete prior days with gap slack


@dataclass
class ConeCombo:
    """One direction × weekday slice of the cone. Empty lists when n == 0."""

    direction: str  # "all" | "bull" | "bear"
    weekday: str  # "all" | "mon" … "sun"
    n: int
    bands: list[list[float]]  # 24 steps × 5 percentiles (×ADR), steps 1…24
    low_in_by: list[float]  # 24 cumulative fractions P(day low set by hour h)
    high_in_by: list[float]
    mae_p: list[float]  # [p10, p50, p90] of per-day path minimum (×ADR)
    mfe_p: list[float]  # [p10, p50, p90] of per-day path maximum (×ADR)
    high_piv: list[float]  # [p50, p80] of (day_high − open)/open/adr14, ≥ 0
    low_piv: list[float]  # [p50, p80] of (open − day_low)/open/adr14, ≥ 0


@dataclass
class PathConeBundle:
    """All 24 combos + population size. Deterministic per (symbol, UTC date)."""

    combos: dict[str, ConeCombo]  # keyed "direction|weekday", e.g. "bull|mon"
    total_days: int


@dataclass
class TodayPath:
    """Today's normalized partial path — live, never cached."""

    points: list[float]  # normalized hourly closes (forming bar last)
    elapsed_h: int  # completed hourly bars today (0–24)
    adr14_today: float
    today_open: float


@dataclass
class _DayRecord:
    day: date
    weekday_key: str
    direction: str  # "bull" | "bear" | "doji"
    norm_path: list[float]
    low_hour: int  # 1–24, earliest bar whose low equals the day low
    high_hour: int
    mae: float
    mfe: float
    high_mag: float
    low_mag: float


def _fetch_hourly(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int | None = None,
) -> dict[date, list[tuple[int, float, float, float, float]]]:
    """1h bars grouped by UTC date; each day's bars sorted by open_time."""
    sql = (
        "SELECT open_time, open, high, low, close FROM ohlcv "
        "WHERE symbol = ? AND timeframe = '1h' "
    )
    params: list[object] = [symbol]
    if start_ms is not None:
        sql += "AND open_time >= ? "
        params.append(start_ms)
    sql += "ORDER BY open_time"
    by_day: dict[date, list[tuple[int, float, float, float, float]]] = defaultdict(list)
    for open_time, o, h, lo, c in conn.execute(sql, params).fetchall():
        d = datetime.fromtimestamp(int(open_time) / 1000, tz=UTC).date()
        by_day[d].append((int(open_time), float(o), float(h), float(lo), float(c)))
    return by_day


def _build_records(
    by_day: dict[date, list[tuple[int, float, float, float, float]]],
    today: date,
) -> tuple[list[_DayRecord], float | None]:
    """Population records + ADR14 for `today` (None if < 14 complete priors).

    A day enters the population only if it is complete (24 bars), lies
    strictly before `today`, has a positive open, and has 14 complete prior
    days to form its trailing ADR window. ADR14 for day d is the mean range
    fraction over the last 14 complete days strictly before d.
    """
    ranges: dict[date, float] = {}
    for d in sorted(by_day):
        if d >= today:
            continue
        bars = by_day[d]
        if len(bars) != 24:
            continue
        day_open = bars[0][1]
        if day_open <= 0:
            continue
        day_high = max(b[2] for b in bars)
        day_low = min(b[3] for b in bars)
        ranges[d] = (day_high - day_low) / day_open

    ordered = sorted(ranges)
    adr14: dict[date, float] = {}
    for i, d in enumerate(ordered):
        if i < _ADR_WINDOW:
            continue
        window = ordered[i - _ADR_WINDOW : i]
        val = sum(ranges[w] for w in window) / _ADR_WINDOW
        if val > 0:
            adr14[d] = val

    adr14_today: float | None = None
    if len(ordered) >= _ADR_WINDOW:
        val = sum(ranges[w] for w in ordered[-_ADR_WINDOW:]) / _ADR_WINDOW
        if val > 0:
            adr14_today = val

    records: list[_DayRecord] = []
    for d in ordered:
        if d not in adr14:
            continue
        bars = by_day[d]
        day_open = bars[0][1]
        day_high = max(b[2] for b in bars)
        day_low = min(b[3] for b in bars)
        denom = day_open * adr14[d]
        norm_path = [(b[4] - day_open) / denom for b in bars]
        close_last = bars[-1][4]
        if close_last > day_open:
            direction = "bull"
        elif close_last < day_open:
            direction = "bear"
        else:
            direction = "doji"
        low_hour = next(i + 1 for i, b in enumerate(bars) if b[3] == day_low)
        high_hour = next(i + 1 for i, b in enumerate(bars) if b[2] == day_high)
        records.append(
            _DayRecord(
                day=d,
                weekday_key=_WD[d.weekday()],
                direction=direction,
                norm_path=norm_path,
                low_hour=low_hour,
                high_hour=high_hour,
                mae=min(norm_path),
                mfe=max(norm_path),
                high_mag=(day_high - day_open) / denom,
                low_mag=(day_open - day_low) / denom,
            )
        )
    return records, adr14_today


def _combo_from(direction: str, weekday: str, pop: list[_DayRecord]) -> ConeCombo:
    n = len(pop)
    if n == 0:
        return ConeCombo(direction, weekday, 0, [], [], [], [], [], [], [])
    paths = np.array([r.norm_path for r in pop])
    bands = [
        [float(v) for v in row] for row in np.percentile(paths, BAND_PCTS, axis=0).T
    ]
    low_in_by = [sum(1 for r in pop if r.low_hour <= h) / n for h in range(1, 25)]
    high_in_by = [sum(1 for r in pop if r.high_hour <= h) / n for h in range(1, 25)]
    mae_p = [float(v) for v in np.percentile([r.mae for r in pop], EXCURSION_PCTS)]
    mfe_p = [float(v) for v in np.percentile([r.mfe for r in pop], EXCURSION_PCTS)]
    high_piv = [float(v) for v in np.percentile([r.high_mag for r in pop], PIVOT_PCTS)]
    low_piv = [float(v) for v in np.percentile([r.low_mag for r in pop], PIVOT_PCTS)]
    return ConeCombo(
        direction=direction,
        weekday=weekday,
        n=n,
        bands=bands,
        low_in_by=low_in_by,
        high_in_by=high_in_by,
        mae_p=mae_p,
        mfe_p=mfe_p,
        high_piv=high_piv,
        low_piv=low_piv,
    )


def compute_path_cone(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> PathConeBundle:
    """All-history conditional cone. Never raises on thin data (empty combos)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    today = datetime.fromtimestamp(now / 1000, tz=UTC).date()
    records, _ = _build_records(_fetch_hourly(conn, symbol), today)
    combos: dict[str, ConeCombo] = {}
    for direction in DIRECTIONS:
        for wd in WEEKDAY_KEYS:
            pop = [
                r
                for r in records
                if (direction == "all" or r.direction == direction)
                and (wd == "all" or r.weekday_key == wd)
            ]
            combos[f"{direction}|{wd}"] = _combo_from(direction, wd, pop)
    return PathConeBundle(combos=combos, total_days=len(records))


def compute_today_path(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> TodayPath | None:
    """Today's normalized partial path. None when today has no bars or the
    trailing ADR14 is unavailable (new listing / short history)."""
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    today = datetime.fromtimestamp(now / 1000, tz=UTC).date()
    start_ms = now - _TODAY_FETCH_DAYS * 24 * _HOUR_MS
    by_day = _fetch_hourly(conn, symbol, start_ms=start_ms)
    _, adr14_today = _build_records(by_day, today)
    if adr14_today is None:
        return None
    bars = by_day.get(today, [])
    if not bars:
        return None
    today_open = bars[0][1]
    if today_open <= 0:
        return None
    denom = today_open * adr14_today
    points = [(b[4] - today_open) / denom for b in bars]
    elapsed = sum(1 for b in bars if b[0] + _HOUR_MS <= now)
    return TodayPath(
        points=points,
        elapsed_h=min(elapsed, 24),
        adr14_today=adr14_today,
        today_open=today_open,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (foreground): `poetry run pytest tests/test_path_cone.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Lint + typecheck**

Run: `make lint-py && make typecheck`
Expected: both clean. (numpy returns `np.float64`; the explicit
`float(v)` conversions above are required for mypy strict — keep them.)

- [ ] **Step 6: Commit**

```bash
git add analytics/stats/path_cone.py tests/test_path_cone.py
git commit -m "feat(stats): path_cone compute — conditional daily path-distribution cone"
```

---

### Task 2: Bundle wiring + retire `daily_distance` compute

**Files:**

- Modify: `analytics/stats/bundle.py`
- Modify: `analytics/stats/__init__.py`
- Delete: `analytics/stats/daily_distance.py`
- Modify: `tests/test_stats_lib.py`

**Interfaces:**

- Consumes: `PathConeBundle`, `compute_path_cone` from Task 1.
- Produces: `StatsBundle.path_cone: PathConeBundle` (new field, computed in
  `compute_all`); package exports `ConeCombo`, `PathConeBundle`,
  `TodayPath`, `compute_path_cone`, `compute_today_path`;
  `DailyDistanceResult` / `compute_daily_distance` no longer exist anywhere.

- [ ] **Step 1: Write the failing test**

In `tests/test_stats_lib.py`, replace the three `compute_daily_distance`
tests (the `# ── F3b: compute_daily_distance tests ──…` section:
`test_compute_daily_distance_basic`, `test_compute_daily_distance_zero_adr`,
`test_compute_daily_distance_exceedance_vs_p80`) with:

```python
# ── F3b: path_cone bundle wiring ─────────────────────────────────────────────


def test_compute_all_includes_path_cone(conn: duckdb.DuckDBPyConnection) -> None:
    """compute_all carries a PathConeBundle with all 24 combo keys.

    The shared fixture has only 14 complete days — all consumed by the ADR
    warmup — so the population is empty but the structure is complete.
    """
    bundle = compute_all(conn, _SYMBOL, days=30)
    assert isinstance(bundle.path_cone, PathConeBundle)
    assert len(bundle.path_cone.combos) == 24
    assert bundle.path_cone.total_days == 0
```

Also in `tests/test_stats_lib.py`: remove `DailyDistanceResult` and
`compute_daily_distance` from the `from analytics.stats_lib import (…)`
block and add `PathConeBundle` to it.

- [ ] **Step 2: Run to verify it fails**

Run (foreground): `poetry run pytest tests/test_stats_lib.py -v`
Expected: FAIL at import — `PathConeBundle` not exported by
`analytics.stats_lib` yet.

- [ ] **Step 3: Wire the package**

In `analytics/stats/bundle.py`:

- Add import: `from analytics.stats.path_cone import PathConeBundle, compute_path_cone`
- Add field to `StatsBundle` (last field): `path_cone: PathConeBundle`
- In `compute_all`, before the `return`: `path_cone = compute_path_cone(conn, symbol)`
  and pass `path_cone=path_cone` in the `StatsBundle(...)` constructor.

In `analytics/stats/__init__.py`:

- Delete the line
  `from analytics.stats.daily_distance import DailyDistanceResult, compute_daily_distance`
- Add:

```python
from analytics.stats.path_cone import (
    ConeCombo,
    PathConeBundle,
    TodayPath,
    compute_path_cone,
    compute_today_path,
)
```

- In `__all__`: remove `"DailyDistanceResult"` and
  `"compute_daily_distance"`; add (keeping alphabetical order)
  `"ConeCombo"`, `"PathConeBundle"`, `"TodayPath"`, `"compute_path_cone"`,
  `"compute_today_path"`.

Delete the module: `git rm analytics/stats/daily_distance.py`

- [ ] **Step 4: Run the stats tests**

Run (foreground): `poetry run pytest tests/test_path_cone.py tests/test_stats_lib.py -v`
Expected: all PASS. (NOTE: `web/api/routers/stats.py` still imports
`compute_daily_distance` at this point — the full suite would fail. That is
fixed in Task 3; only run the two files above here.)

- [ ] **Step 5: Commit**

```bash
# (the git rm in Step 3 already staged the deletion)
git add analytics/stats/bundle.py analytics/stats/__init__.py tests/test_stats_lib.py
git commit -m "feat(stats): path_cone joins StatsBundle; retire daily_distance compute"
```

---

### Task 3: API models + router (cached block, live overlay, DD removal)

**Files:**

- Modify: `web/api/models/stats.py`
- Modify: `web/api/routers/stats.py`
- Modify: `tests/test_path_cone.py` (one serialization test appended)

**Interfaces:**

- Consumes: `StatsBundle.path_cone`, `compute_today_path`, `TodayPath` from
  Tasks 1–2.
- Produces: Pydantic models `ConeComboResponse`, `PathConeResponse`,
  `TodayPathResponse`; `StatsResponse.path_cone: PathConeResponse`
  (required) and `StatsResponse.today_path: TodayPathResponse | None = None`;
  `DailyDistanceResponse` deleted. Task 4's TS types mirror these exactly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_path_cone.py`:

```python
def test_stats_response_requires_path_cone() -> None:
    """Pre-M5 cached JSON (no path_cone) must fail validation so the router's
    corrupted-cache fallback recomputes — the cache self-heals on deploy day."""
    import pydantic
    import pytest as _pytest

    from web.api.models.stats import StatsResponse

    with _pytest.raises(pydantic.ValidationError):
        StatsResponse.model_validate_json('{"symbol": "X", "days": 180}')
    assert "path_cone" in StatsResponse.model_fields
    assert "today_path" in StatsResponse.model_fields
    assert "daily_distance" not in StatsResponse.model_fields


def test_path_cone_response_round_trip() -> None:
    """The cached block survives a JSON dump → validate cycle unchanged."""
    from web.api.models.stats import ConeComboResponse, PathConeResponse

    combo = ConeComboResponse(
        direction="bull",
        weekday="mon",
        n=2,
        bands=[[-0.1, 0.0, 0.125, 0.2, 0.3]] * 24,
        low_in_by=[1.0] * 24,
        high_in_by=[0.0] + [1.0] * 23,
        mae_p=[0.1, 0.19, 0.24],
        mfe_p=[0.1, 0.19, 0.24],
        high_piv=[0.5, 0.5],
        low_piv=[0.5, 0.5],
    )
    original = PathConeResponse(combos={"bull|mon": combo}, total_days=2)
    restored = PathConeResponse.model_validate_json(original.model_dump_json())
    assert restored == original
```

- [ ] **Step 2: Run to verify it fails**

Run (foreground): `poetry run pytest tests/test_path_cone.py::test_stats_response_requires_path_cone -v`
Expected: FAIL — `path_cone` not in `model_fields` /
`daily_distance` still present.

- [ ] **Step 3: Update the Pydantic models**

In `web/api/models/stats.py`, delete the `DailyDistanceResponse` class and
add in its place:

```python
class ConeComboResponse(BaseModel):
    direction: str
    weekday: str
    n: int
    bands: list[list[float]]
    low_in_by: list[float]
    high_in_by: list[float]
    mae_p: list[float]
    mfe_p: list[float]
    high_piv: list[float]
    low_piv: list[float]


class PathConeResponse(BaseModel):
    combos: dict[str, ConeComboResponse]
    total_days: int


class TodayPathResponse(BaseModel):
    points: list[float]
    elapsed_h: int
    adr14_today: float
    today_open: float
```

In `StatsResponse`: replace the line
`daily_distance: DailyDistanceResponse | None = None` with:

```python
    path_cone: PathConeResponse
    today_path: TodayPathResponse | None = None
```

(`path_cone` is deliberately REQUIRED: same-day pre-deploy cache entries
fail `model_validate_json` and fall through the router's existing
corrupted-cache `except` to a recompute.)

- [ ] **Step 4: Update the router**

In `web/api/routers/stats.py`:

- Imports — in the `from analytics.stats_lib import (…)` block replace
  `compute_daily_distance` with `compute_today_path`; in the
  `from web.api.models.stats import (…)` block replace
  `DailyDistanceResponse` with `ConeComboResponse, PathConeResponse,
  TodayPathResponse` (keep alphabetical order).
- In `_bundle_to_response`, before the final `return`, add:

```python
    # Path cone — all 24 direction × weekday combos (cached with the bundle)
    path_cone_resp = PathConeResponse(
        combos={
            key: ConeComboResponse(
                direction=c.direction,
                weekday=c.weekday,
                n=c.n,
                bands=c.bands,
                low_in_by=c.low_in_by,
                high_in_by=c.high_in_by,
                mae_p=c.mae_p,
                mfe_p=c.mfe_p,
                high_piv=c.high_piv,
                low_piv=c.low_piv,
            )
            for key, c in bundle.path_cone.combos.items()
        },
        total_days=bundle.path_cone.total_days,
    )
```

and add `path_cone=path_cone_resp,` to the `StatsResponse(...)` constructor.

- In `_inject_live_fields`, replace the entire
  `# Daily distance — …` try/except block with:

```python
    # Today path overlay — today's normalized hourly closes (never cached)
    try:
        tp = compute_today_path(db, symbol)
        if tp is not None:
            response.today_path = TodayPathResponse(
                points=tp.points,
                elapsed_h=tp.elapsed_h,
                adr14_today=tp.adr14_today,
                today_open=tp.today_open,
            )
    except Exception:
        pass
```

- [ ] **Step 5: Run the full gate**

Run (foreground): `make lint-py && make typecheck && make test`
Expected: all green — no `daily_distance` references remain in Python.
Verify:
`grep -rn "daily_distance\|DailyDistance" --include='*.py' --exclude-dir=.venv --exclude-dir=.git .`
Expected output: no matches (empty).

- [ ] **Step 6: Commit**

```bash
git add web/api/models/stats.py web/api/routers/stats.py tests/test_path_cone.py
git commit -m "feat(api): path_cone cached block + today_path live overlay; drop daily_distance"
```

---

### Task 4: TS types + PathCone.svelte + Stats hero (DD card removal)

**Files:**

- Modify: `web/ui/src/api.ts`
- Create: `web/ui/src/components/PathCone.svelte`
- Modify: `web/ui/src/pages/Stats.svelte`

**Interfaces:**

- Consumes: the `path_cone` / `today_path` fields of `GET /api/stats/{symbol}`
  exactly as shaped in Task 3.
- Produces: `PathCone` component with props
  `{ pathCone: PathConeResponse; todayPath: TodayPathResponse | null }`.

**Before starting: load the `/frontend-design` skill (house rule for any
Svelte/CSS work), then follow the steps below.**

- [ ] **Step 1: Update the TS types**

In `web/ui/src/api.ts`, delete the `DailyDistanceResponse` interface and add
in its place:

```typescript
export interface ConeComboResponse {
  direction: string;
  weekday: string;
  n: number;
  bands: number[][];
  low_in_by: number[];
  high_in_by: number[];
  mae_p: number[];
  mfe_p: number[];
  high_piv: number[];
  low_piv: number[];
}

export interface PathConeResponse {
  combos: Record<string, ConeComboResponse>;
  total_days: number;
}

export interface TodayPathResponse {
  points: number[];
  elapsed_h: number;
  adr14_today: number;
  today_open: number;
}
```

In the `StatsResponse` interface, replace
`daily_distance: DailyDistanceResponse | null;` with:

```typescript
  path_cone: PathConeResponse;
  today_path: TodayPathResponse | null;
```

- [ ] **Step 2: Create the component**

Create `web/ui/src/components/PathCone.svelte` with exactly this content:

```svelte
<script lang="ts">
  import type {
    ConeComboResponse,
    PathConeResponse,
    TodayPathResponse,
  } from "../api";

  let {
    pathCone,
    todayPath,
  }: {
    pathCone: PathConeResponse;
    todayPath: TodayPathResponse | null;
  } = $props();

  const DIRS = [
    { key: "all", label: "All" },
    { key: "bull", label: "Bull" },
    { key: "bear", label: "Bear" },
  ];
  const DAYS = [
    { key: "all", label: "All days" },
    { key: "mon", label: "Mon" },
    { key: "tue", label: "Tue" },
    { key: "wed", label: "Wed" },
    { key: "thu", label: "Thu" },
    { key: "fri", label: "Fri" },
    { key: "sat", label: "Sat" },
    { key: "sun", label: "Sun" },
  ];

  let dir = $state("all");
  let wday = $state("all");

  const combo = $derived<ConeComboResponse | null>(
    pathCone.combos[`${dir}|${wday}`] ?? null
  );
  const hasData = $derived(!!combo && combo.n > 0);

  // ── SVG geometry (viewBox units) ──
  const W = 760;
  const H = 280;
  const PL = 46;
  const PR = 10;
  const PT = 12;
  const PB = 26;

  const yDomain = $derived.by(() => {
    const vals: number[] = [0];
    if (combo && combo.n > 0)
      for (const row of combo.bands) vals.push(row[0], row[4]);
    if (todayPath) vals.push(...todayPath.points);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.08, 0.1);
    return { min: lo - pad, max: hi + pad };
  });

  const x = (step: number) => PL + (step / 24) * (W - PL - PR);
  const y = (v: number) =>
    PT + ((yDomain.max - v) / (yDomain.max - yDomain.min)) * (H - PT - PB);

  // Filled band polygon between percentile columns loIdx/hiIdx (0=p10 … 4=p90).
  function bandD(loIdx: number, hiIdx: number): string {
    if (!combo || combo.n === 0) return "";
    const pts = [`M ${x(0)} ${y(0)}`];
    combo.bands.forEach((row, i) => pts.push(`L ${x(i + 1)} ${y(row[hiIdx])}`));
    for (let i = combo.bands.length - 1; i >= 0; i--)
      pts.push(`L ${x(i + 1)} ${y(combo.bands[i][loIdx])}`);
    pts.push("Z");
    return pts.join(" ");
  }

  function lineD(idx: number): string {
    if (!combo || combo.n === 0) return "";
    return [
      `M ${x(0)} ${y(0)}`,
      ...combo.bands.map((row, i) => `L ${x(i + 1)} ${y(row[idx])}`),
    ].join(" ");
  }

  const todayD = $derived.by(() => {
    if (!todayPath || todayPath.points.length === 0) return "";
    return [
      `M ${x(0)} ${y(0)}`,
      ...todayPath.points.map((v, i) => `L ${x(i + 1)} ${y(v)}`),
    ].join(" ");
  });

  // Axis: elapsed UTC hour → MYT label (UTC+8, no DST)
  const ticks = [0, 4, 8, 12, 16, 20, 24].map((s) => ({
    step: s,
    label: String((s + 8) % 24).padStart(2, "0") + ":00",
  }));

  const fmtAdr = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(2);
  const px = (mag: number, side: 1 | -1) =>
    todayPath
      ? todayPath.today_open * (1 + side * mag * todayPath.adr14_today)
      : null;
  const fmtPx = (p: number | null) =>
    p === null ? "—" : p >= 1000 ? p.toFixed(0) : p.toFixed(4);

  const elapsed = $derived(todayPath?.elapsed_h ?? 0);
  const lowInNow = $derived(
    hasData && elapsed >= 1 ? combo!.low_in_by[Math.min(elapsed, 24) - 1] : null
  );
  const highInNow = $derived(
    hasData && elapsed >= 1 ? combo!.high_in_by[Math.min(elapsed, 24) - 1] : null
  );
</script>

<div class="cone">
  <div class="cone-controls">
    <div class="chip-group">
      {#each DIRS as d}
        <button
          class="chip"
          class:active={dir === d.key}
          onclick={() => (dir = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    <div class="chip-group">
      {#each DAYS as d}
        <button
          class="chip"
          class:active={wday === d.key}
          onclick={() => (wday = d.key)}>{d.label}</button
        >
      {/each}
    </div>
    {#if combo}
      <span class="n-badge" class:thin={combo.n < 30}
        >n={combo.n}{combo.n < 30 ? " ⚠ thin sample" : ""}</span
      >
    {/if}
  </div>

  {#if hasData && combo}
    <svg viewBox="0 0 {W} {H}" class="cone-svg" role="img" aria-label="Daily path cone">
      <path d={bandD(0, 4)} class="band-outer" />
      <path d={bandD(1, 3)} class="band-inner" />
      <line x1={x(0)} y1={y(0)} x2={x(24)} y2={y(0)} class="zero-line" />
      <path d={lineD(2)} class="median-line" />
      {#if todayD && todayPath}
        <path d={todayD} class="today-line" />
        <circle
          cx={x(todayPath.points.length)}
          cy={y(todayPath.points[todayPath.points.length - 1])}
          r="3.5"
          class="today-dot"
        />
      {/if}
      {#each ticks as t}
        <text x={x(t.step)} y={H - 8} class="axis-label" text-anchor="middle"
          >{t.label}</text
        >
      {/each}
      <text x={PL - 6} y={PT + 8} class="axis-label" text-anchor="end"
        >{fmtAdr(yDomain.max)}×</text
      >
      <text x={PL - 6} y={y(0) + 3} class="axis-label" text-anchor="end">0</text>
      <text x={PL - 6} y={H - PB} class="axis-label" text-anchor="end"
        >{fmtAdr(yDomain.min)}×</text
      >
    </svg>

    <div class="cone-footer">
      {#if lowInNow !== null && highInNow !== null}
        <span class="cone-stat"
          >by now: low in {(lowInNow * 100).toFixed(0)}% · high in {(
            highInNow * 100
          ).toFixed(0)}% of matching days</span
        >
      {/if}
      {#if todayPath}
        <span class="cone-stat"
          >pivots: H p50 {fmtPx(px(combo.high_piv[0], 1))} · H p80 {fmtPx(
            px(combo.high_piv[1], 1)
          )} · L p50 {fmtPx(px(combo.low_piv[0], -1))} · L p80 {fmtPx(
            px(combo.low_piv[1], -1)
          )}</span
        >
      {/if}
      <span class="cone-muted"
        >median day: dip {fmtAdr(combo.mae_p[1])}× / peak {fmtAdr(
          combo.mfe_p[1]
        )}× ADR</span
      >
      {#if todayPath}
        <span class="cone-muted"
          >1× ADR ≈ {(todayPath.adr14_today * 100).toFixed(2)}%</span
        >
      {/if}
    </div>
  {:else}
    <div class="cone-empty cone-muted">insufficient data for this filter</div>
  {/if}
</div>

<style>
  .cone {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .cone-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    align-items: center;
  }
  .chip-group {
    display: flex;
    gap: 0.25rem;
    flex-wrap: wrap;
  }
  .chip {
    background: transparent;
    border: 1px solid #333;
    color: #aaa;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .chip.active {
    border-color: #60a5fa;
    color: #e5e7eb;
  }
  .n-badge {
    font-size: 0.75rem;
    color: #888;
  }
  .n-badge.thin {
    color: #f59e0b;
  }
  .cone-svg {
    width: 100%;
    height: auto;
  }
  .band-outer {
    fill: rgba(96, 165, 250, 0.1);
  }
  .band-inner {
    fill: rgba(96, 165, 250, 0.18);
  }
  .zero-line {
    stroke: #444;
    stroke-dasharray: 2 3;
  }
  .median-line {
    fill: none;
    stroke: #60a5fa;
    stroke-width: 1.5;
  }
  .today-line {
    fill: none;
    stroke: #f59e0b;
    stroke-width: 1.5;
    stroke-dasharray: 5 3;
  }
  .today-dot {
    fill: #f59e0b;
  }
  .axis-label {
    fill: #777;
    font-size: 10px;
  }
  .cone-footer {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1.2rem;
    font-size: 0.8rem;
  }
  .cone-stat {
    color: #cbd5e1;
  }
  .cone-muted {
    color: #777;
  }
  .cone-empty {
    padding: 2rem 0;
    text-align: center;
  }
</style>
```

- [ ] **Step 3: Wire the hero into Stats.svelte and remove the DD card**

In `web/ui/src/pages/Stats.svelte`:

- Add to the component imports (next to `LoadingSpinner`/`ErrorBanner`):
  `import PathCone from "../components/PathCone.svelte";`
- In `CARD_HELP`, delete the `dailyDistance:` entry and add:

```typescript
    pathCone: {
      what: "Historical intraday paths — hourly closes as ×ADR14 from the UTC day open — pooled into percentile bands: p10–p90 outer, p25–p75 inner, median line. Filter by day direction (bull/bear at close) and weekday; the dotted amber line is today so far on the same scale. 'By now' row: fraction of matching days whose eventual daily low/high was already set by the current hour. Pivots: typical (p50) and extended (p80) high/low excursions mapped to today's prices.",
      value: "Read where today sits inside the historical envelope: hugging p90 = extended vs the template — chasing here is late; near the median = nothing unusual yet. The 'by now' row says whether the daily extreme is statistically already in. Pivots give price targets/invalidation for the day. Thin-sample combos (n<30, amber ⚠) are directional hints, not statistics.",
      example: "Bull + Tue at 14:00 MYT: today riding p75, low-in-by 81% → the dip is likely in; H p80 pivot 1.6% above → remaining upside bounded. Fade extension, don't chase.",
    },
```

- Insert the hero card between `{:else if stats}` and `<div class="grid">`:

```svelte
    <!-- Daily Path Cone — hero (M5) -->
    <div class="card hero-card">
      <div class="card-header">
        <span class="card-title">Daily Path Cone</span>
        <button class="help-btn" class:active={openHelp === "pathCone"} onclick={() => toggleHelp("pathCone")} aria-label="Help">?</button>
      </div>
      {#if openHelp === "pathCone"}
        <div class="help-panel">
          <div class="help-section"><span class="help-label">What</span>{CARD_HELP.pathCone.what}</div>
          <div class="help-section"><span class="help-label">Use</span>{CARD_HELP.pathCone.value}</div>
          <div class="help-section help-example"><span class="help-label">e.g.</span>{CARD_HELP.pathCone.example}</div>
        </div>
      {/if}
      <PathCone pathCone={stats.path_cone} todayPath={stats.today_path} />
    </div>
```

- Delete the entire Daily Distance card block — from the comment
  `<!-- Daily Distance — empirical CDF for today's move vs history -->`
  through its closing `{/if}` (the `{#if stats.daily_distance}` block).
- Delete the now-unused Daily Distance CSS rules from the `<style>`
  section — every rule whose selector starts with `.dist-`
  (`.dist-main-row`, `.dist-exceedance`, `.dist-exceedance-label`,
  `.dist-bar-wrap`, `.dist-bar-track`, `.dist-bar-fill`, `.dist-bar-p80`,
  `.dist-bar-labels`, `.dist-bar-label-now`, `.dist-bar-label-p80`,
  `.dist-gap`, `.dist-note`). Verify none remain:
  `grep -n "dist-" web/ui/src/pages/Stats.svelte` → no matches.
- Add one rule to the `<style>` section:

```css
  .hero-card {
    margin-bottom: 1rem;
  }
```

- [ ] **Step 4: Build to verify**

Run: `make web-build`
Expected: tsc + vite build clean, no type errors, no unused-symbol
warnings for the deleted DD types.

- [ ] **Step 5: Visual smoke (only if a synced analytics.db is available)**

Run `make buibui-web`, open the Stats page, confirm: hero cone renders for
BTCUSDT, chips switch instantly, dotted today-line present, thin combos
show the amber ⚠. Skip silently if no local DB — the build gate in Step 4
is the hard requirement.

- [ ] **Step 6: Commit**

```bash
git add web/ui/src/api.ts web/ui/src/components/PathCone.svelte web/ui/src/pages/Stats.svelte
git commit -m "feat(ui): Daily Path Cone hero on Stats; remove Daily Distance card"
```

---

### Task 5: Full gate + docs sync

**Files:**

- Modify: `CLAUDE.md` (the `stats/` bullet in Project Structure)
- Modify: `.claude/skills/stats-dashboard/SKILL.md` (card inventory)
- Modify: `README.md` (only if it mentions Daily Distance — check first)

**Interfaces:** none produced — this task closes the arc.

- [ ] **Step 1: Run the full Definition-of-Done gate**

Run (foreground, in order):

```bash
make lint-py
make typecheck
make test
make test-regression
make web-build
```

Expected: all green; regression goldens UNMOVED (stats are not part of the
backtest goldens — any golden diff means something unrelated broke: stop
and investigate, do not regenerate).

- [ ] **Step 2: Update the docs**

- `CLAUDE.md` — in the `stats/` bullet of Project Structure: add
   `path_cone.py` to the per-dimension module list with a half-line
   description (`path_cone.py` — conditional daily path-distribution cone:
   ADR-normalized hourly paths × (direction × weekday) percentile bands +
   timing/excursion/pivot percentiles, cached; `compute_today_path` live
   overlay never cached), and remove `daily_distance.py` from the same
   list. Do not restructure anything else.
- `.claude/skills/stats-dashboard/SKILL.md` — in the card tables: remove
  the Daily Distance row from the Live table; add "Daily Path Cone"
  to the Cached table (`compute_path_cone(conn, symbol)`, all-history,
  ignores `days`) and "Today Path overlay" to the Live table
  (`compute_today_path(conn, symbol)` via `_inject_live_fields()`).
  Update the "The 10 Cards" heading count if it now reads wrong.
- `README.md` — run `grep -n -i "daily distance" README.md`; if it
  matches, replace the mention with the Daily Path Cone description;
  if no matches, skip.
- Run `make lint-md`. Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md .claude/skills/stats-dashboard/SKILL.md README.md
git commit -m "docs: stats docs sync — Daily Path Cone replaces Daily Distance"
```

---

## Post-plan notes for the executing session

- Branch `feat/m5-path-cone` off `main` (after the `docs/m5-stats-cone`
  branch merges) — or cherry-pick the spec/plan commits if executing before
  that merge.
- After the arc: `/pr-summary` then `/post-branch` per house workflow.
- Deploy note (no action in this plan): on deploy day, same-day
  `stats_cache` entries lack `path_cone` and self-heal via the router's
  validation-failure fallback (one extra recompute per (symbol, days) —
  harmless).
- Out of scope (spec §10): Live Outcomes UX, External-block presentation,
  weekly re-parametrization + P1 Wick Rank redesign, cone-derived research
  tags.
