# H10 Partial-Path Predictiveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, de-biased audit answering whether the week's partial price path at hour `h` predicts the return from `h` to the week's close, beyond drift.

**Architecture:** One pure library (`analytics/weekly_path.py`) holding statistic construction, the gate, and diagnostics — no DB, no I/O, no network. One driver (`tools/weekly_path_audit.py`) that opens a read-only DuckDB connection, loads week records via a newly-public wrapper on `analytics/stats/weekly_cone.py`, and renders the report. The statistical guards (`analytics/audit_guard.py`, `analytics/research_guards/`) are consumed unchanged.

**Tech Stack:** Python 3.11+, numpy, duckdb (read-only), pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md` — read it before starting. Section references below (§2, §5, §6…) point into it.

## Global Constraints

- **Read-only.** No writes to `analytics.db`, no schema change, no migration. The driver opens `duckdb.connect(path, read_only=True)`.
- **No test may touch the real `analytics.db` or `signal_state.json`.** Analytics tests use `duckdb.connect(":memory:")`. Library tests in Tasks 1–4 need no DB at all.
- **Pure library.** `analytics/weekly_path.py` imports no `duckdb` and performs no I/O. It receives already-loaded data as plain dataclasses.
- **Type annotations on every function**, including `-> None` on tests. mypy runs strict (`disallow_untyped_defs = true`).
- **Additive only.** `analytics/stats/weekly_cone.py` behaviour must stay byte-identical; Task 1 only exposes what it already builds.
- **Pre-committed constants, never tuned against a result:** `hours = (24, 48, 72, 96, 120)`, `bar = 0.05` (AWR/week), `alpha = 0.05`, `min_n = 52` (weeks), `seed = 12345`.
- **Units are AWR, not R.** Paths are normalized by the week's own trailing AWR14. Every rendered label must say AWR.
- **Definition of Done for the branch:** `make lint-py`, `make typecheck`, `make test` all green, and `make test-regression` goldens **unmoved** — this work is additive and read-only, so any golden movement is a defect, not an intentional behavioural change.

## The two contaminants this audit exists to avoid

Both are §2 of the spec. Each has a dedicated failing-first test. If you find yourself removing one of these tests to make something pass, stop — you have reintroduced the defect the audit was built to detect.

1. **Arithmetic tautology.** `terminal = path_at_h + remaining`, so a partial path always correlates with the terminal value at `sqrt(h/168)` under a pure random walk (~0.85 by hour 120). We therefore test the **remaining-path return**, whose expectation is exactly zero. Never reintroduce a terminal-direction-vs-base-rate comparison.
2. **Drift.** Signing by `sign(path)` in an upward-drifting market makes `s = +1` almost always, so the raw remaining return is positive on average and reads as skill. We therefore sign the **causally demeaned** remaining return.

## ⚠ The inversion trap (read this before Task 3)

`analytics/audit_guard.py::evaluate_audit_cells` was written for gate auditing, where `supp_r` is a *suppressed* slice. Its decisions are therefore **inverted relative to the sign of the mean** (verified at `analytics/audit_guard.py:196-207`):

```python
if ci_hi <= -bar and significant:   # reliably NEGATIVE mean
    decision = DECISION_ENABLE
elif ci_lo >= bar and significant:  # reliably POSITIVE mean
    decision = DECISION_DISABLE
```

For H10 the mean is `mean(v)`, so:

| `audit_guard` decision | What it means here | H10 verdict |
| --- | --- | --- |
| `DISABLE` | `mean(v)` reliably **positive** | **PREDICTIVE** (continuation) |
| `ENABLE` | `mean(v)` reliably **negative** | **REVERTING** (reversal) |
| `INSUFFICIENT` | neither branch cleared | `NO-EDGE` if `n >= min_n`, else `INSUFFICIENT` |

Mapping `ENABLE → PREDICTIVE` reports the exact opposite of the truth and would still pass a careless test suite. Task 3 Step 1 pins this with an explicit test.

## File Structure

| File | Responsibility |
| --- | --- |
| `analytics/stats/weekly_cone.py` | **Modify.** Rename `_WeekRecord` → public `WeekRecord` (private alias retained); add public `week_records()` wrapper. Nothing else changes. |
| `analytics/weekly_path.py` | **Create.** Pure library: config, statistic construction (Task 2), gate + verdicts (Task 3), diagnostics + family stamps (Task 4). |
| `tools/weekly_path_audit.py` | **Create.** DB front door + report rendering. |
| `tests/test_weekly_cone.py` | **Modify.** Add wrapper tests (Task 1). |
| `tests/test_weekly_path.py` | **Create.** All pure-library tests (Tasks 2–4). |
| `tests/test_weekly_path_audit.py` | **Create.** Driver tests over an in-memory DuckDB (Task 5). |
| `Makefile` | **Modify.** Add `buibui-weekly-path-audit`. |
| `README.md`, `CLAUDE.md` | **Modify.** Document the new module + tool (Task 5). |

---

### Task 1: Expose the cone's week population

Gives the audit the exact same AWR normalization and week-population rules the Brief uses, so the two cannot drift apart (spec §3.1 — the invariant `sl_horizon.py` established by delegating ATR to the engine's `_compute_atr14`).

**Files:**

- Modify: `analytics/stats/weekly_cone.py` (dataclass at lines 70-81; new function after `compute_weekly_cone`, ~line 232)
- Test: `tests/test_weekly_cone.py`

**Interfaces:**

- Consumes: nothing (first task).
- Produces:
  - `WeekRecord` — public dataclass, fields unchanged: `week: date`, `direction: str`, `norm_path: list[float]`, `low_hour: int`, `high_hour: int`, `mae: float`, `mfe: float`, `high_mag: float`, `low_mag: float`
  - `week_records(conn: duckdb.DuckDBPyConnection, symbol: str, *, now_ms: int | None = None) -> list[WeekRecord]`

- [ ] **Step 1: Write the failing test**

First extend the existing import block at `tests/test_weekly_cone.py:18-23` — the file uses a from-import, and the alias test needs the module object too:

```python
from analytics.stats import weekly_cone
from analytics.stats.weekly_cone import (
    WEEK_BARS,
    WeeklyConeBundle,
    WeekRecord,
    compute_current_week_path,
    compute_weekly_cone,
    week_records,
)
```

Then append these tests. They reuse the file's existing `conn` fixture (line 77), `_seed_warmup` (line 69), `_insert_week` (line 32), `_SYMBOL`, `_CURRENT_WEEK`, and `_NOW_MS` — do not invent new helpers.

```python
def test_week_records_matches_cone_population(conn: duckdb.DuckDBPyConnection) -> None:
    """The public wrapper returns exactly the population the cone counts."""
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=17), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=3), k=0.4)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=2), k=-0.4)

    records = week_records(conn, _SYMBOL, now_ms=_NOW_MS)
    bundle = compute_weekly_cone(conn, _SYMBOL, now_ms=_NOW_MS)

    assert len(records) == bundle.total_weeks == 2
    assert all(len(r.norm_path) == WEEK_BARS for r in records)
    assert {r.direction for r in records} == {"bull", "bear"}


def test_week_records_are_chronological(conn: duckdb.DuckDBPyConnection) -> None:
    _seed_warmup(conn, _CURRENT_WEEK - timedelta(weeks=17), n=14)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=3), k=0.4)
    _insert_week(conn, _CURRENT_WEEK - timedelta(weeks=2), k=-0.4)

    records = week_records(conn, _SYMBOL, now_ms=_NOW_MS)
    assert [r.week for r in records] == sorted(r.week for r in records)


def test_week_records_thin_data_returns_empty(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """Matches compute_weekly_cone: never raises on a short history."""
    assert week_records(conn, _SYMBOL, now_ms=_NOW_MS) == []


def test_private_alias_still_resolves() -> None:
    """The rename must not break internal references inside the module."""
    assert weekly_cone._WeekRecord is WeekRecord
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_weekly_cone.py -k "week_records or private_alias" -v`

Note the import added in Step 1 will make the whole file fail to collect until Step 3 lands — that is the expected failure, not a separate problem.

Expected: FAIL with `AttributeError: module 'analytics.stats.weekly_cone' has no attribute 'week_records'`.

- [ ] **Step 3: Implement**

In `analytics/stats/weekly_cone.py`, rename the dataclass and add the alias:

```python
@dataclass
class WeekRecord:
    week: date  # the Monday
    direction: str  # "bull" | "bear" | "doji"
    norm_path: list[float]
    low_hour: int  # 1–168, earliest bar whose low equals the week low
    high_hour: int
    mae: float
    mfe: float
    high_mag: float
    low_mag: float


_WeekRecord = WeekRecord  # back-compat alias for internal references
```

Then add the wrapper immediately after `compute_weekly_cone`:

```python
def week_records(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> list[WeekRecord]:
    """The cone's own completed-week population, chronological.

    Shared with the H10 partial-path audit so the audit and the Brief cannot
    disagree on AWR normalization or week-population rules. Returns [] on thin
    data rather than raising, matching compute_weekly_cone.
    """
    now = now_ms if now_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    current_week = _week_key(datetime.fromtimestamp(now / 1000, tz=UTC))
    records, _ = _build_records(_fetch_hourly(conn, symbol), current_week)
    return records
```

Leave the `-> tuple[list[_WeekRecord], float | None]` annotation on `_build_records` as-is; the alias makes it valid.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_weekly_cone.py -v`

Expected: PASS, including every pre-existing test in the file (the rename must not disturb them).

- [ ] **Step 5: Verify nothing downstream broke**

Run: `poetry run pytest tests/test_web_stats_weekly_cone.py tests/test_brief_weekly.py -v`

Expected: PASS. These exercise the Brief and the stats router, the two live consumers of this module.

- [ ] **Step 6: Commit**

```bash
git add analytics/stats/weekly_cone.py tests/test_weekly_cone.py
git commit -m "refactor(weekly-cone): expose WeekRecord + week_records() for the H10 audit"
```

---

### Task 2: The statistic

Builds the causally-demeaned, sign-weighted, cross-sectionally-collapsed observation series (spec §5). This is where both contaminants are neutralized.

**Files:**

- Create: `analytics/weekly_path.py`
- Test: `tests/test_weekly_path.py`

**Interfaces:**

- Consumes: `WeekRecord` from Task 1 — but note the library stays DB-free, so it defines its own lightweight `SymbolWeek` input rather than importing the cone's dataclass. The driver (Task 5) does the conversion.
- Produces:
  - `PathConfig` — frozen dataclass: `hours: tuple[int, ...] = (24, 48, 72, 96, 120)`, `bar: float = 0.05`, `alpha: float = 0.05`, `min_n: int = 52`, `min_prior_obs: int = 52`, `n_boot: int = 10_000`, `seed: int | None = 12345`
  - `SymbolWeek` — frozen dataclass: `symbol: str`, `week: date`, `norm_path: tuple[float, ...]`
  - `WeekObservation` — frozen dataclass: `week: date`, `value: float`, `n_symbols: int`, `mean_abs_signal: float`
  - `signal_sign(norm_path: Sequence[float], hour: int) -> float`
  - `remaining_return(norm_path: Sequence[float], hour: int) -> float`
  - `build_observations(weeks: Sequence[SymbolWeek], hour: int, cfg: PathConfig) -> list[WeekObservation]`

- [ ] **Step 1: Write the failing tests — index convention and primitives**

Create `tests/test_weekly_path.py`:

```python
"""H10 partial-path predictiveness — pure-library tests (no DB)."""

from datetime import date, timedelta

import numpy as np
import pytest

from analytics import weekly_path as wp

WEEK_BARS = 168


def _week(i: int) -> date:
    """Monday i weeks after 2020-01-06 (a Monday)."""
    return date(2020, 1, 6) + timedelta(weeks=i)


def _linear_path(start: float, end: float) -> tuple[float, ...]:
    """A 168-point path rising linearly from `start` to `end`."""
    return tuple(np.linspace(start, end, WEEK_BARS))


def test_signal_sign_reads_the_bar_before_hour() -> None:
    """Hour h means index h-1 — the close of the h-th completed bar."""
    path = [0.0] * WEEK_BARS
    path[23] = -5.0  # index 23 == hour 24
    path[24] = +5.0
    assert wp.signal_sign(path, 24) == -1.0
    assert wp.signal_sign(path, 25) == +1.0


def test_signal_sign_flat_week_is_zero() -> None:
    assert wp.signal_sign([0.0] * WEEK_BARS, 24) == 0.0


def test_remaining_return_spans_hour_to_close() -> None:
    path = [0.0] * WEEK_BARS
    path[23] = 2.0
    path[167] = 5.0
    assert wp.remaining_return(path, 24) == pytest.approx(3.0)


def test_remaining_return_at_last_hour_is_zero() -> None:
    path = list(_linear_path(0.0, 4.0))
    assert wp.remaining_return(path, 168) == pytest.approx(0.0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_weekly_path.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.weekly_path'`.

- [ ] **Step 3: Implement the primitives**

Create `analytics/weekly_path.py`:

```python
"""H10 — partial-path predictiveness (pure library).

Spec: docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md

Does the week's normalized path observable at hour `h` predict the return from
`h` to the week's close, beyond drift?

Two contaminants are neutralized by construction and each is unit-tested:

1. Arithmetic tautology — `terminal = path_at_h + remaining`, so the partial
   path correlates with the terminal value at sqrt(h/168) under a pure random
   walk. We test the REMAINING return, whose null is exactly zero.
2. Drift — `sign(path)` is +1 almost always in an up-drifting market, so a raw
   signed remaining return reads as skill. We sign the CAUSALLY DEMEANED
   remaining return (expanding mean over weeks strictly earlier).

Pure: no DB, no I/O, no network. The driver is tools/weekly_path_audit.py.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

WEEK_BARS = 168
GATED_HOURS = (24, 48, 72, 96, 120)  # end of Mon/Tue/Wed/Thu/Fri, UTC


@dataclass(frozen=True)
class PathConfig:
    """Pre-committed audit parameters (spec §6). Never tuned against a result."""

    hours: tuple[int, ...] = GATED_HOURS
    bar: float = 0.05  # AWR per week; ~2.5x round-trip cost
    alpha: float = 0.05
    min_n: int = 52  # weeks — one year of observations
    min_prior_obs: int = 52  # symbol-weeks required before the expanding mean is usable
    n_boot: int = 10_000
    seed: int | None = 12345


@dataclass(frozen=True)
class SymbolWeek:
    """One completed symbol-week: the AWR14-normalized 168-point close path."""

    symbol: str
    week: date
    norm_path: tuple[float, ...]


@dataclass(frozen=True)
class WeekObservation:
    """One calendar week, cross-section already collapsed (spec §5.1)."""

    week: date
    value: float  # mean of v across symbols live that week
    n_symbols: int
    mean_abs_signal: float  # mean |path[h-1]| — feeds the magnitude terciles


def _index_for(hour: int) -> int:
    """Hour `h` is index `h-1` — the close of the h-th completed bar."""
    if not 1 <= hour <= WEEK_BARS:
        raise ValueError(f"hour must be in 1..{WEEK_BARS}, got {hour}")
    return hour - 1


def signal_sign(norm_path: Sequence[float], hour: int) -> float:
    """sign(path at `hour`); 0.0 when exactly flat, which drops the week."""
    value = norm_path[_index_for(hour)]
    if value > 0.0:
        return 1.0
    if value < 0.0:
        return -1.0
    return 0.0


def remaining_return(norm_path: Sequence[float], hour: int) -> float:
    """Return from `hour` to the week's close, in AWR units."""
    return float(norm_path[WEEK_BARS - 1] - norm_path[_index_for(hour)])
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_weekly_path.py -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add analytics/weekly_path.py tests/test_weekly_path.py
git commit -m "feat(h10): partial-path signal + remaining-return primitives"
```

- [ ] **Step 6: Write the failing tests — the two contaminant guards**

These are the tests the audit exists for. Append to `tests/test_weekly_path.py`:

```python
def _population(paths_by_week: dict[int, list[tuple[str, tuple[float, ...]]]]) -> list[wp.SymbolWeek]:
    out: list[wp.SymbolWeek] = []
    for i, entries in sorted(paths_by_week.items()):
        for symbol, path in entries:
            out.append(wp.SymbolWeek(symbol=symbol, week=_week(i), norm_path=path))
    return out


def test_pure_drift_does_not_read_as_skill() -> None:
    """THE DRIFT GUARD (spec §2.1).

    Every week rises identically. sign(path) is always +1 and the remaining
    return is always positive, so a NON-demeaned statistic would read strongly
    positive. The causal demean must collapse it toward zero.
    """
    pop = _population({i: [("BTCUSDT", _linear_path(0.0, 1.0))] for i in range(200)})
    obs = wp.build_observations(pop, 24, wp.PathConfig())
    values = [o.value for o in obs]
    assert values, "population must produce observations"
    assert abs(float(np.mean(values))) < 0.01


def test_driftless_random_walk_reads_near_zero_at_every_hour() -> None:
    """THE TAUTOLOGY GUARD (spec §2).

    A random walk carries no path information. It must read ~0 at EVERY hour,
    including 120 — where a terminal-direction-vs-base-rate test would have
    reported strong predictiveness from arithmetic alone.
    """
    rng = np.random.default_rng(0)
    pop: list[wp.SymbolWeek] = []
    for i in range(600):
        # Step sigma is deliberately small: the remaining return's sampling
        # error must sit well under the 0.05 bar, or this test flakes on a
        # correct implementation rather than catching a broken one.
        steps = rng.normal(0.0, 0.02, WEEK_BARS)
        pop.append(wp.SymbolWeek("BTCUSDT", _week(i), tuple(np.cumsum(steps))))
    for hour in wp.GATED_HOURS:
        obs = wp.build_observations(pop, hour, wp.PathConfig())
        mean_v = float(np.mean([o.value for o in obs]))
        assert abs(mean_v) < 0.05, f"hour {hour} drifted to {mean_v}"


def test_constructed_continuation_reads_positive() -> None:
    """A genuine continuation effect must survive the demean."""
    rng = np.random.default_rng(1)
    pop: list[wp.SymbolWeek] = []
    for i in range(300):
        direction = 1.0 if rng.random() < 0.5 else -1.0
        # Same sign in both halves => the partial path predicts the remainder.
        first = np.linspace(0.0, direction * 1.0, 24)
        rest = np.linspace(direction * 1.0, direction * 3.0, WEEK_BARS - 24)
        pop.append(wp.SymbolWeek("BTCUSDT", _week(i), tuple(np.concatenate([first, rest]))))
    obs = wp.build_observations(pop, 24, wp.PathConfig())
    assert float(np.mean([o.value for o in obs])) > 0.5


def test_expanding_mean_is_causal() -> None:
    """CAUSALITY GUARD: perturbing week k must not move any earlier observation."""
    base = [
        wp.SymbolWeek("BTCUSDT", _week(i), _linear_path(0.0, 1.0)) for i in range(120)
    ]
    bumped = list(base)
    bumped[100] = wp.SymbolWeek("BTCUSDT", _week(100), _linear_path(0.0, 50.0))

    cfg = wp.PathConfig()
    before = {o.week: o.value for o in wp.build_observations(base, 24, cfg)}
    after = {o.week: o.value for o in wp.build_observations(bumped, 24, cfg)}

    for i in range(100):
        w = _week(i)
        if w in before:
            assert before[w] == pytest.approx(after[w]), f"week {i} moved"


def test_baseline_excludes_the_week_it_prices() -> None:
    """SELF-INCLUSION GUARD — the case the perturbation test structurally cannot see.

    `test_expanding_mean_is_causal` above catches whole-history look-ahead (a
    global mean leaking the future into the past), but it CANNOT catch a week
    pricing against a baseline that includes itself: that contaminates only the
    week's own value, identically in the base and perturbed runs, so the
    comparison cancels it out. Verified by mutation — moving the baseline
    advance above the emit block leaves that test green.

    52 flat prior weeks (remaining 0.0), then one extreme week (remaining 10.0).
    The extreme week is the FIRST to clear the min_prior_obs warm-up, so its
    baseline must be the mean of the 52 priors (0.0) and its value exactly 10.0.
    Under self-inclusion it would be 10 - 10/53 = 9.811..., and this fails.
    """
    cfg = wp.PathConfig()

    def _path(remaining: float) -> tuple[float, ...]:
        path = [0.0] * WEEK_BARS
        for j in range(23, WEEK_BARS):
            path[j] = 1.0
        path[WEEK_BARS - 1] = 1.0 + remaining
        return tuple(path)

    pop = [wp.SymbolWeek("A", _week(i), _path(0.0)) for i in range(52)]
    pop.append(wp.SymbolWeek("A", _week(52), _path(10.0)))

    obs = wp.build_observations(pop, 24, cfg)
    assert len(obs) == 1
    assert obs[0].week == _week(52)
    assert obs[0].value == pytest.approx(10.0)


def test_cross_section_is_averaged_not_counted() -> None:
    """25 identical symbols must give the same observation as 1 (spec §5.1)."""
    cfg = wp.PathConfig()
    one = [
        wp.SymbolWeek("A", _week(i), _linear_path(0.0, float(i % 3) - 1.0))
        for i in range(200)
    ]
    many: list[wp.SymbolWeek] = []
    for i in range(200):
        for s in range(25):
            many.append(
                wp.SymbolWeek(f"S{s}", _week(i), _linear_path(0.0, float(i % 3) - 1.0))
            )

    # Compared over the weeks COMMON to both runs, not position-by-position:
    # min_prior_obs counts symbol-weeks, so the 25-symbol population finishes
    # its baseline warm-up ~25x sooner and legitimately emits more weeks.
    obs_one = {o.week: o for o in wp.build_observations(one, 24, cfg)}
    obs_many = {o.week: o for o in wp.build_observations(many, 24, cfg)}
    common = set(obs_one) & set(obs_many)
    assert common, "the two runs must overlap on some weeks"
    for w in common:
        # If the collapse summed instead of averaging, these would differ 25x.
        assert obs_one[w].value == pytest.approx(obs_many[w].value)
        assert obs_one[w].n_symbols == 1
        assert obs_many[w].n_symbols == 25


def test_flat_weeks_are_dropped() -> None:
    cfg = wp.PathConfig()
    pop = [wp.SymbolWeek("A", _week(i), tuple([0.0] * WEEK_BARS)) for i in range(120)]
    assert wp.build_observations(pop, 24, cfg) == []


def test_mean_abs_signal_measures_the_signal_not_the_remainder() -> None:
    """It feeds the magnitude terciles, so it must be |signal|, not |remainder|."""
    cfg = wp.PathConfig()
    pop: list[wp.SymbolWeek] = []
    for i in range(120):
        path = [0.0] * WEEK_BARS
        path[23] = 2.0  # signal magnitude 2.0 at hour 24
        path[167] = 9.0  # remainder 7.0 — must NOT be what is recorded
        pop.append(wp.SymbolWeek("A", _week(i), tuple(path)))
    obs = wp.build_observations(pop, 24, cfg)
    assert obs
    assert obs[0].mean_abs_signal == pytest.approx(2.0)


def test_malformed_paths_are_excluded() -> None:
    """Population rules (spec §4): a path that is not 168 points cannot enter.

    The cone's own rules already exclude short weeks, zero-open weeks, and weeks
    without 14 priors upstream; this is the library's own defensive guard for a
    caller that hands it something malformed.
    """
    cfg = wp.PathConfig()
    good = [wp.SymbolWeek("A", _week(i), _linear_path(0.0, 1.0)) for i in range(120)]
    bad = [wp.SymbolWeek("B", _week(i), tuple([1.0] * 167)) for i in range(120)]
    obs = wp.build_observations(good + bad, 24, cfg)
    assert obs
    assert all(o.n_symbols == 1 for o in obs), "the 167-bar symbol must not contribute"
```

- [ ] **Step 7: Run to verify they fail**

Run: `poetry run pytest tests/test_weekly_path.py -v`

Expected: FAIL with `AttributeError: module 'analytics.weekly_path' has no attribute 'build_observations'`.

- [ ] **Step 8: Implement `build_observations`**

Append to `analytics/weekly_path.py`:

```python
def build_observations(
    weeks: Sequence[SymbolWeek],
    hour: int,
    cfg: PathConfig,
) -> list[WeekObservation]:
    """One observation per calendar week: mean over symbols of the signed,
    causally demeaned remaining return.

    The expanding baseline `mu_t` is the mean raw remaining return over ALL
    symbol-weeks strictly EARLIER than week `t` — pooled across symbols, which
    keeps it stable while staying causal. A week is emitted only once at least
    `cfg.min_prior_obs` prior symbol-weeks exist, so the earliest weeks do not
    ride a one-sample baseline.

    Weeks whose signal is exactly flat contribute nothing (their sign is
    undefined); a calendar week with no contributing symbol is omitted.
    """
    # (sign, raw remaining return, |signal| magnitude) per contributing symbol.
    by_week: dict[date, list[tuple[float, float, float]]] = defaultdict(list)
    for sw in weeks:
        if len(sw.norm_path) != WEEK_BARS:
            continue
        sign = signal_sign(sw.norm_path, hour)
        if sign == 0.0:
            continue
        by_week[sw.week].append(
            (
                sign,
                remaining_return(sw.norm_path, hour),
                abs(sw.norm_path[_index_for(hour)]),
            )
        )

    out: list[WeekObservation] = []
    prior_sum = 0.0
    prior_count = 0
    for week in sorted(by_week):
        entries = by_week[week]
        if prior_count >= cfg.min_prior_obs:
            mu = prior_sum / prior_count
            values = [sign * (rem - mu) for sign, rem, _ in entries]
            magnitudes = [mag for _, _, mag in entries]
            out.append(
                WeekObservation(
                    week=week,
                    value=float(np.mean(values)),
                    n_symbols=len(entries),
                    mean_abs_signal=float(np.mean(magnitudes)),
                )
            )
        # Advance the baseline only AFTER emitting — week t must never price
        # against a mean that includes itself. This ordering IS the causality
        # guarantee; test_baseline_excludes_the_week_it_prices is the guard that
        # actually detects a move above the emit block (the broader
        # test_expanding_mean_is_causal does NOT — self-inclusion cancels out of
        # its base-vs-perturbed comparison).
        for _, rem, _ in entries:
            prior_sum += rem
            prior_count += 1
    return out
```

- [ ] **Step 9: Run to verify they pass**

Run: `poetry run pytest tests/test_weekly_path.py -v`

Expected: all passed (13 tests).

If `test_pure_drift_does_not_read_as_skill` fails, the demean is broken — do **not** loosen the tolerance. If `test_baseline_excludes_the_week_it_prices` fails, the baseline is being advanced before the emit block instead of after.

- [ ] **Step 10: Run, lint, typecheck**

```bash
poetry run pytest tests/test_weekly_path.py -v
make lint-py
make typecheck
```

Expected: all tests pass; ruff and mypy clean.

- [ ] **Step 11: Commit**

```bash
git add analytics/weekly_path.py tests/test_weekly_path.py
git commit -m "feat(h10): causal demeaned observation series with drift + tautology guards"
```

---

### Task 3: The gate and verdicts

Wraps `audit_guard` with the correct — inverted — decision mapping and adds the early/late sign condition (spec §6, §6.1).

**Files:**

- Modify: `analytics/weekly_path.py`
- Test: `tests/test_weekly_path.py`

**Interfaces:**

- Consumes: `PathConfig`, `SymbolWeek`, `WeekObservation`, `build_observations` (Task 2).
- Produces:
  - Constants `VERDICT_PREDICTIVE = "PREDICTIVE"`, `VERDICT_REVERTING = "REVERTING"`, `VERDICT_NO_EDGE = "NO-EDGE"`, `VERDICT_INSUFFICIENT = "INSUFFICIENT"`
  - `HourVerdict` — frozen dataclass: `hour: int`, `verdict: str`, `n_weeks: int`, `mean_v: float | None`, `ci_lo: float | None`, `ci_hi: float | None`, `adj_pvalue: float | None`, `early_mean: float | None`, `late_mean: float | None`, `reasons: list[str]`
  - `evaluate_hours(weeks: Sequence[SymbolWeek], cfg: PathConfig = PathConfig()) -> list[HourVerdict]`

- [ ] **Step 1: Write the failing test — the inversion trap first**

Append to `tests/test_weekly_path.py`:

```python
def _synthetic(effect: float, n_weeks: int = 400, seed: int = 7) -> list[wp.SymbolWeek]:
    """A population whose signed, demeaned remaining return has mean ~= `effect`."""
    rng = np.random.default_rng(seed)
    pop: list[wp.SymbolWeek] = []
    for i in range(n_weeks):
        sign = 1.0 if rng.random() < 0.5 else -1.0
        at_h = sign * 1.0
        rest = sign * effect + rng.normal(0.0, 0.05)
        path = [0.0] * WEEK_BARS
        for j in range(23, WEEK_BARS):
            path[j] = at_h
        path[WEEK_BARS - 1] = at_h + rest
        pop.append(wp.SymbolWeek("A", _week(i), tuple(path)))
    return pop


def test_positive_effect_maps_to_PREDICTIVE_not_REVERTING() -> None:
    """THE INVERSION GUARD.

    audit_guard returns DISABLE for a reliably POSITIVE mean (it was written for
    gate auditing, where supp_r is a suppressed slice). Mapping ENABLE ->
    PREDICTIVE would invert every verdict in this audit while still looking
    plausible. See analytics/audit_guard.py:196-207.
    """
    verdicts = {v.hour: v for v in wp.evaluate_hours(_synthetic(+0.60), wp.PathConfig())}
    v24 = verdicts[24]
    assert v24.mean_v is not None and v24.mean_v > 0
    assert v24.verdict == wp.VERDICT_PREDICTIVE


def test_negative_effect_maps_to_REVERTING() -> None:
    verdicts = {v.hour: v for v in wp.evaluate_hours(_synthetic(-0.60), wp.PathConfig())}
    v24 = verdicts[24]
    assert v24.mean_v is not None and v24.mean_v < 0
    assert v24.verdict == wp.VERDICT_REVERTING


def test_no_effect_reads_NO_EDGE_when_well_powered() -> None:
    verdicts = {v.hour: v for v in wp.evaluate_hours(_synthetic(0.0), wp.PathConfig())}
    assert verdicts[24].verdict == wp.VERDICT_NO_EDGE


def test_thin_population_reads_INSUFFICIENT() -> None:
    verdicts = {v.hour: v for v in wp.evaluate_hours(_synthetic(+0.60, n_weeks=90), wp.PathConfig())}
    # 90 weeks minus the 52-observation baseline warm-up leaves < min_n.
    assert verdicts[24].verdict == wp.VERDICT_INSUFFICIENT


def test_time_split_disagreement_demotes_to_INSUFFICIENT() -> None:
    """A strong first half and an opposite second half is not an edge."""
    first = _synthetic(+1.2, n_weeks=250, seed=3)
    second_raw = _synthetic(-1.2, n_weeks=250, seed=4)
    second = [
        wp.SymbolWeek(sw.symbol, _week(250 + i), sw.norm_path)
        for i, sw in enumerate(second_raw)
    ]
    verdicts = {v.hour: v for v in wp.evaluate_hours(first + second, wp.PathConfig())}
    v24 = verdicts[24]
    assert v24.verdict == wp.VERDICT_INSUFFICIENT
    assert any("time-split" in r for r in v24.reasons)


def test_all_gated_hours_are_reported() -> None:
    verdicts = wp.evaluate_hours(_synthetic(0.0), wp.PathConfig())
    assert [v.hour for v in verdicts] == list(wp.GATED_HOURS)
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_weekly_path.py -k "verdict or PREDICTIVE or REVERTING or NO_EDGE or INSUFFICIENT or gated_hours" -v`

Expected: FAIL with `AttributeError: module 'analytics.weekly_path' has no attribute 'evaluate_hours'`.

- [ ] **Step 3: Implement the gate**

Append to `analytics/weekly_path.py`. First extend the imports at the top of the file — `field` is newly needed for `HourVerdict.reasons` and was deliberately not imported in Task 2, where it would have been unused and tripped ruff:

```python
from dataclasses import dataclass, field

from analytics.audit_guard import (
    DECISION_DISABLE,
    DECISION_ENABLE,
    AuditCell,
    evaluate_audit_cells,
)
```

```python
VERDICT_PREDICTIVE = "PREDICTIVE"
VERDICT_REVERTING = "REVERTING"
VERDICT_NO_EDGE = "NO-EDGE"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class HourVerdict:
    hour: int
    verdict: str
    n_weeks: int
    mean_v: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    early_mean: float | None
    late_mean: float | None
    reasons: list[str] = field(default_factory=list)


def _halves(values: list[float]) -> tuple[float | None, float | None]:
    """Means of the early and late halves, split at the median week."""
    if len(values) < 4:
        return None, None
    mid = len(values) // 2
    return float(np.mean(values[:mid])), float(np.mean(values[mid:]))


def evaluate_hours(
    weeks: Sequence[SymbolWeek],
    cfg: PathConfig = PathConfig(),
) -> list[HourVerdict]:
    """Pre-committed verdict per gated hour, sharing one Holm family (spec §6).

    IMPORTANT — audit_guard's decisions are INVERTED relative to the sign of the
    mean, because it was written for gate auditing where `supp_r` is a
    suppressed slice (analytics/audit_guard.py:196-207):

        DISABLE  <=> mean reliably POSITIVE  => PREDICTIVE here
        ENABLE   <=> mean reliably NEGATIVE  => REVERTING here

    Mapping ENABLE -> PREDICTIVE would invert every verdict.
    """
    series: dict[int, list[float]] = {}
    for hour in cfg.hours:
        series[hour] = [o.value for o in build_observations(weeks, hour, cfg)]

    cells = [AuditCell(label=f"h{h}", supp_r=series[h]) for h in cfg.hours]
    results = evaluate_audit_cells(
        cells,
        bar=cfg.bar,
        alpha=cfg.alpha,
        min_n=cfg.min_n,
        n_boot=cfg.n_boot,
        boot_method="circular",
        seed=cfg.seed,
        enable_concentrate=False,
    )

    out: list[HourVerdict] = []
    for hour, cell in zip(cfg.hours, results):
        values = series[hour]
        n = len(values)
        early, late = _halves(values)
        reasons = list(cell.reasons)

        if cell.decision == DECISION_DISABLE:
            verdict = VERDICT_PREDICTIVE
        elif cell.decision == DECISION_ENABLE:
            verdict = VERDICT_REVERTING
        elif n >= cfg.min_n:
            verdict = VERDICT_NO_EDGE
        else:
            verdict = VERDICT_INSUFFICIENT

        # The early/late split is a verdict CONDITION, not a footnote (spec §6.1).
        if verdict in (VERDICT_PREDICTIVE, VERDICT_REVERTING):
            headline = cell.supp_avg or 0.0
            if (
                early is None
                or late is None
                or np.sign(early) != np.sign(headline)
                or np.sign(late) != np.sign(headline)
            ):
                verdict = VERDICT_INSUFFICIENT
                reasons.append(
                    f"time-split sign disagreement (early {early}, late {late}, "
                    f"headline {headline})"
                )

        out.append(
            HourVerdict(
                hour=hour,
                verdict=verdict,
                n_weeks=n,
                mean_v=cell.supp_avg,
                ci_lo=cell.ci_lo,
                ci_hi=cell.ci_hi,
                adj_pvalue=cell.adj_pvalue,
                early_mean=early,
                late_mean=late,
                reasons=reasons,
            )
        )
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_weekly_path.py -v`

Expected: all passed. If `test_thin_population_reads_INSUFFICIENT` fails because 90 weeks still clears `min_n`, adjust the test's `n_weeks` down until fewer than 52 observations survive the warm-up — do **not** lower `cfg.min_n`, which is pre-committed.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add analytics/weekly_path.py tests/test_weekly_path.py
git commit -m "feat(h10): gated verdicts with the audit_guard inversion pinned"
```

---

### Task 4: Diagnostics and family stamps

Everything reported but non-gating (spec §7) plus the DSR/PBO/MinTRL stamps (spec §6).

**Files:**

- Modify: `analytics/weekly_path.py`
- Test: `tests/test_weekly_path.py`

**Interfaces:**

- Consumes: everything from Tasks 2–3.
- Produces:
  - `MagnitudeRow` — frozen dataclass: `hour: int`, `tercile: int`, `n_weeks: int`, `mean_v: float`
  - `CurvePoint` — frozen dataclass: `hour: int`, `n_weeks: int`, `mean_v: float`
  - `FamilyStamps` — frozen dataclass: `n_trials: int`, `best_hour: int | None`, `best_sharpe: float | None`, `dsr: float | None`, `pbo: float | None`, `min_trl: float | None`
  - `magnitude_breakdown(weeks: Sequence[SymbolWeek], hour: int, cfg: PathConfig) -> list[MagnitudeRow]`
  - `hour_curve(weeks: Sequence[SymbolWeek], cfg: PathConfig) -> list[CurvePoint]`
  - `family_stamps(weeks: Sequence[SymbolWeek], cfg: PathConfig) -> FamilyStamps`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_weekly_path.py`:

```python
def test_magnitude_breakdown_has_three_terciles() -> None:
    rows = wp.magnitude_breakdown(_synthetic(+0.5), 24, wp.PathConfig())
    assert [r.tercile for r in rows] == [1, 2, 3]
    assert all(r.hour == 24 for r in rows)
    assert all(r.n_weeks > 0 for r in rows)


def test_hour_curve_covers_every_hour() -> None:
    points = wp.hour_curve(_synthetic(0.0, n_weeks=200), wp.PathConfig())
    assert [p.hour for p in points] == list(range(1, wp.WEEK_BARS + 1))


def test_family_stamps_report_five_trials() -> None:
    stamps = wp.family_stamps(_synthetic(+0.6), wp.PathConfig())
    assert stamps.n_trials == len(wp.GATED_HOURS)
    assert stamps.best_hour in wp.GATED_HOURS
    assert stamps.dsr is not None and 0.0 <= stamps.dsr <= 1.0
    assert stamps.pbo is not None and 0.0 <= stamps.pbo <= 1.0


def test_family_stamps_degrade_on_thin_data() -> None:
    """Thin input returns None rather than raising — the driver still renders.

    60 weeks leaves ~8 observations after the 52-symbol-week warm-up, far under
    the 28 rows cscv_pbo needs, so PBO specifically must come back None.
    """
    stamps = wp.family_stamps(_synthetic(0.0, n_weeks=60), wp.PathConfig())
    assert stamps.n_trials == len(wp.GATED_HOURS)
    assert stamps.pbo is None


def test_family_stamps_on_empty_population() -> None:
    """No weeks at all must not raise — every stamp is None."""
    stamps = wp.family_stamps([], wp.PathConfig())
    assert stamps.n_trials == len(wp.GATED_HOURS)
    assert stamps.best_hour is None
    assert stamps.dsr is None
    assert stamps.pbo is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_weekly_path.py -k "magnitude or curve or stamps" -v`

Expected: FAIL with `AttributeError: ... has no attribute 'magnitude_breakdown'`.

- [ ] **Step 3: Implement**

Add the import at the top of `analytics/weekly_path.py`:

```python
from analytics.research_guards import (
    cscv_pbo,
    deflated_sharpe_ratio,
    min_track_record_length,
)
```

Append:

```python
@dataclass(frozen=True)
class MagnitudeRow:
    hour: int
    tercile: int  # 1 = smallest |signal|, 3 = largest
    n_weeks: int
    mean_v: float


@dataclass(frozen=True)
class CurvePoint:
    hour: int
    n_weeks: int
    mean_v: float


@dataclass(frozen=True)
class FamilyStamps:
    n_trials: int
    best_hour: int | None
    best_sharpe: float | None
    dsr: float | None
    pbo: float | None
    min_trl: float | None


def magnitude_breakdown(
    weeks: Sequence[SymbolWeek],
    hour: int,
    cfg: PathConfig,
) -> list[MagnitudeRow]:
    """Mean v within terciles of |signal| (spec §7 — reported, never gating).

    Terciles are cut on the pooled symbol-week |path[h-1]| distribution, then
    each tercile is collapsed per calendar week exactly as the headline is.
    """
    magnitudes = [
        abs(sw.norm_path[_index_for(hour)])
        for sw in weeks
        if len(sw.norm_path) == WEEK_BARS and signal_sign(sw.norm_path, hour) != 0.0
    ]
    if not magnitudes:
        return []
    cuts = np.quantile(magnitudes, [1 / 3, 2 / 3])

    rows: list[MagnitudeRow] = []
    for tercile in (1, 2, 3):
        lo = -np.inf if tercile == 1 else cuts[tercile - 2]
        hi = np.inf if tercile == 3 else cuts[tercile - 1]
        subset = [
            sw
            for sw in weeks
            if len(sw.norm_path) == WEEK_BARS and lo <= abs(sw.norm_path[hour - 1]) < hi
        ]
        obs = build_observations(subset, hour, cfg)
        values = [o.value for o in obs]
        rows.append(
            MagnitudeRow(
                hour=hour,
                tercile=tercile,
                n_weeks=len(values),
                mean_v=float(np.mean(values)) if values else 0.0,
            )
        )
    return rows


def hour_curve(weeks: Sequence[SymbolWeek], cfg: PathConfig) -> list[CurvePoint]:
    """mean(v) at every hour 1..168 — a descriptive shape, never gating."""
    points: list[CurvePoint] = []
    for hour in range(1, WEEK_BARS + 1):
        values = [o.value for o in build_observations(weeks, hour, cfg)]
        points.append(
            CurvePoint(
                hour=hour,
                n_weeks=len(values),
                mean_v=float(np.mean(values)) if values else 0.0,
            )
        )
    return points


def _sharpe(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    arr = np.asarray(values, dtype=np.float64)
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return None
    return float(np.mean(arr) / sd)


def family_stamps(weeks: Sequence[SymbolWeek], cfg: PathConfig) -> FamilyStamps:
    """DSR / PBO / MinTRL over the gated-hour family (spec §6).

    The hours are reported together and none is selected, but the stamps make
    the family size visible. PBO needs equal-length columns, so it runs over the
    intersection of weeks present at every hour. Degrades to None rather than
    raising, so a thin run still renders a report.
    """
    series = {h: build_observations(weeks, h, cfg) for h in cfg.hours}
    by_hour = {h: [o.value for o in obs] for h, obs in series.items()}

    # Explicit annotation: mypy strict will not narrow `float | None` -> `float`
    # through a dict comprehension's `if` clause.
    usable: dict[int, float] = {}
    for h, values in by_hour.items():
        sr = _sharpe(values)
        if sr is not None:
            usable[h] = sr
    if not usable:
        return FamilyStamps(len(cfg.hours), None, None, None, None, None)

    best_hour = max(usable, key=lambda h: usable[h])
    best_sr = usable[best_hour]

    dsr: float | None = None
    n_obs = len(by_hour[best_hour])
    if n_obs >= 2 and len(usable) >= 2:
        dsr = deflated_sharpe_ratio(
            best_sr, n_obs, trial_srs=[usable[h] for h in sorted(usable)]
        )

    min_trl: float | None = None
    if best_sr > 0.0:
        value = min_track_record_length(best_sr, confidence=0.95)
        min_trl = None if value == float("inf") else value

    pbo: float | None = None
    common = set.intersection(*({o.week for o in series[h]} for h in cfg.hours))
    # cscv_pbo's default n_splits=14 needs >= 2 rows per block, so >= 28 rows.
    if len(common) >= 28:
        order = sorted(common)
        matrix = np.array(
            [
                [
                    next(o.value for o in series[h] if o.week == w)
                    for h in cfg.hours
                ]
                for w in order
            ],
            dtype=np.float64,
        )
        try:
            pbo = cscv_pbo(matrix).pbo
        except ValueError:
            pbo = None

    return FamilyStamps(len(cfg.hours), best_hour, best_sr, dsr, pbo, min_trl)
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_weekly_path.py -v`

Expected: all passed.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add analytics/weekly_path.py tests/test_weekly_path.py
git commit -m "feat(h10): magnitude terciles, hour curve, DSR/PBO/MinTRL family stamps"
```

---

### Task 5: Driver, Makefile target, docs

The DB front door and the operator-facing report (spec §3).

**Files:**

- Create: `tools/weekly_path_audit.py`
- Create: `tests/test_weekly_path_audit.py`
- Modify: `Makefile` (`.PHONY` list at line 14; new target near `buibui-sl-horizon-audit` at line 332)
- Modify: `README.md`, `CLAUDE.md`

**Interfaces:**

- Consumes: everything from Tasks 1–4.
- Produces:
  - `load_symbol_weeks(conn: duckdb.DuckDBPyConnection, symbols: Sequence[str], *, now_ms: int | None = None) -> list[wp.SymbolWeek]`
  - `render_report(verdicts, stamps, magnitude_rows, curve, *, label: str) -> str`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing driver test**

Create `tests/test_weekly_path_audit.py`:

```python
"""H10 driver tests — in-memory DuckDB only, never the real analytics.db."""

from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from analytics.store.schema import init_schema
from tools import weekly_path_audit as wpa

_HOUR_MS = 3_600_000


def _seed(conn: duckdb.DuckDBPyConnection, symbol: str, n_weeks: int) -> None:
    """n_weeks complete Monday-anchored weeks of synthetic 1h bars."""
    start = datetime(2021, 1, 4, tzinfo=UTC)  # a Monday
    rows = []
    price = 100.0
    for w in range(n_weeks):
        for b in range(168):
            ts = int((start + timedelta(weeks=w, hours=b)).timestamp() * 1000)
            price *= 1.0005 if (w + b) % 3 else 0.9995
            rows.append((symbol, "1h", ts, price, price * 1.01, price * 0.99, price, 1.0, 0.5))
    conn.executemany(
        "INSERT INTO ohlcv (symbol, timeframe, open_time, open, high, low, close, "
        "volume, taker_buy_volume) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    init_schema(c)
    return c


def test_load_symbol_weeks_returns_normalized_paths(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _seed(conn, "BTCUSDT", 40)
    now_ms = int(datetime(2021, 11, 1, tzinfo=UTC).timestamp() * 1000)
    weeks = wpa.load_symbol_weeks(conn, ["BTCUSDT"], now_ms=now_ms)
    assert weeks
    assert all(len(w.norm_path) == 168 for w in weeks)
    assert all(w.symbol == "BTCUSDT" for w in weeks)


def test_load_symbol_weeks_skips_unknown_symbol(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _seed(conn, "BTCUSDT", 40)
    now_ms = int(datetime(2021, 11, 1, tzinfo=UTC).timestamp() * 1000)
    weeks = wpa.load_symbol_weeks(conn, ["BTCUSDT", "NOPEUSDT"], now_ms=now_ms)
    assert {w.symbol for w in weeks} == {"BTCUSDT"}


def test_render_report_states_AWR_units_and_every_hour() -> None:
    from analytics import weekly_path as wp

    verdicts = [
        wp.HourVerdict(h, wp.VERDICT_NO_EDGE, 100, 0.0, -0.1, 0.1, 0.9, 0.0, 0.0, [])
        for h in wp.GATED_HOURS
    ]
    stamps = wp.FamilyStamps(5, 24, 0.1, 0.5, 0.4, 100.0)
    text = wpa.render_report(verdicts, stamps, [], [], label="universe")
    assert "AWR" in text
    for h in wp.GATED_HOURS:
        assert f"h{h}" in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `poetry run pytest tests/test_weekly_path_audit.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'tools.weekly_path_audit'`.

- [ ] **Step 3: Implement the driver**

Create `tools/weekly_path_audit.py`:

```python
"""H10 — partial-path predictiveness audit (read-only driver).

Spec: docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md

Does the week's normalized path at hour `h` predict the return from `h` to the
week's close, beyond drift? Gates ST6 ("is this a bullish week?" framing) and
ST7 (idea generation + invalidation).

Read-only: opens DuckDB with read_only=True, writes nothing.

    PYTHONPATH=. poetry run python tools/weekly_path_audit.py
    make buibui-weekly-path-audit
"""

import argparse
from datetime import UTC, datetime
from typing import Sequence

import duckdb

from analytics import weekly_path as wp
from analytics.stats.weekly_cone import week_records
from analytics.store.schema import DEFAULT_DB_PATH
from analytics.universe import load_universe

_MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def load_symbol_weeks(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    *,
    now_ms: int | None = None,
) -> list[wp.SymbolWeek]:
    """Every symbol's completed-week population, via the cone's own rules.

    A symbol with no qualifying weeks contributes nothing rather than raising —
    new listings legitimately have no 14-week AWR warm-up yet.
    """
    out: list[wp.SymbolWeek] = []
    for symbol in symbols:
        for record in week_records(conn, symbol, now_ms=now_ms):
            out.append(
                wp.SymbolWeek(
                    symbol=symbol,
                    week=record.week,
                    norm_path=tuple(record.norm_path),
                )
            )
    return out


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def render_report(
    verdicts: Sequence[wp.HourVerdict],
    stamps: wp.FamilyStamps,
    magnitude_rows: Sequence[wp.MagnitudeRow],
    curve: Sequence[wp.CurvePoint],
    *,
    label: str,
) -> str:
    lines: list[str] = []
    lines.append(f"## {label}")
    lines.append("")
    lines.append("Units are AWR per week (the week's own trailing AWR14).")
    lines.append("")
    lines.append("| hour | verdict | n weeks | mean v | CI lo | CI hi | Holm p | early | late |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for v in verdicts:
        lines.append(
            f"| h{v.hour} | {v.verdict} | {v.n_weeks} | {_fmt(v.mean_v)} | "
            f"{_fmt(v.ci_lo)} | {_fmt(v.ci_hi)} | {_fmt(v.adj_pvalue)} | "
            f"{_fmt(v.early_mean)} | {_fmt(v.late_mean)} |"
        )
    lines.append("")
    lines.append(
        f"Family stamps ({stamps.n_trials} trials): best h"
        f"{stamps.best_hour} · Sharpe {_fmt(stamps.best_sharpe)} · "
        f"DSR {_fmt(stamps.dsr)} · PBO {_fmt(stamps.pbo)} · "
        f"MinTRL {_fmt(stamps.min_trl, 1)}"
    )
    lines.append("")

    if magnitude_rows:
        lines.append("### Magnitude terciles (reported, non-gating)")
        lines.append("")
        lines.append("| hour | tercile | n weeks | mean v |")
        lines.append("| --- | --- | --- | --- |")
        for r in magnitude_rows:
            lines.append(f"| h{r.hour} | {r.tercile} | {r.n_weeks} | {_fmt(r.mean_v)} |")
        lines.append("")

    if curve:
        lines.append("### Hour curve (reported, non-gating) — every 12th hour")
        lines.append("")
        lines.append("| hour | n weeks | mean v |")
        lines.append("| --- | --- | --- |")
        for p in curve:
            if p.hour % 12 == 0:
                lines.append(f"| h{p.hour} | {p.n_weeks} | {_fmt(p.mean_v)} |")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="H10 partial-path predictiveness audit (read-only)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--majors", default=",".join(_MAJORS), help="comma-separated breadth contrast"
    )
    parser.add_argument(
        "--skip-curve",
        action="store_true",
        help="skip the 168-hour descriptive curve (it is the slow part)",
    )
    args = parser.parse_args(argv)

    cfg = wp.PathConfig()
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    majors = [s.strip().upper() for s in args.majors.split(",") if s.strip()]

    conn = duckdb.connect(args.db, read_only=True)
    try:
        universe = load_universe()
        cohorts: list[tuple[str, list[str]]] = [
            ("universe", universe),
            ("majors", majors),
            ("BTC only", ["BTCUSDT"]),
        ]
        sections: list[str] = []
        for label, symbols in cohorts:
            weeks = load_symbol_weeks(conn, symbols, now_ms=now_ms)
            if not weeks:
                sections.append(f"## {label}\n\nNo qualifying weeks.\n")
                continue
            verdicts = wp.evaluate_hours(weeks, cfg)
            stamps = wp.family_stamps(weeks, cfg)
            magnitude = [
                row for h in cfg.hours for row in wp.magnitude_breakdown(weeks, h, cfg)
            ]
            curve = [] if args.skip_curve else wp.hour_curve(weeks, cfg)
            sections.append(
                render_report(verdicts, stamps, magnitude, curve, label=label)
            )
    finally:
        conn.close()

    print("# H10 — partial-path predictiveness\n")
    print("\n".join(sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `poetry run pytest tests/test_weekly_path_audit.py -v`

Expected: 3 passed.

- [ ] **Step 5: Add the Makefile target**

Append `buibui-weekly-path-audit` to the `.PHONY` list on line 14, then add near line 335 (after `buibui-sl-horizon-audit`):

The recipe line **must** begin with a literal tab, not spaces — `make` rejects spaces.

<!-- markdownlint-disable MD010 -->

```makefile
.PHONY: buibui-weekly-path-audit
buibui-weekly-path-audit:  ## H10: read-only partial-path predictiveness audit (gates ST6/ST7)
	PYTHONPATH=. poetry run python tools/weekly_path_audit.py
```

<!-- markdownlint-enable MD010 -->

- [ ] **Step 6: Run the real audit**

Run: `make buibui-weekly-path-audit`

Expected: three sections (universe / majors / BTC only), each with five hour rows carrying a verdict, and family stamps. **Do not tune any constant based on what you see** — the parameters are pre-committed in spec §6. Record the output; it is the audit result.

- [ ] **Step 7: Full Definition of Done**

```bash
make lint-py
make typecheck
make test
make test-regression
```

Expected: all green, and **regression goldens unmoved**. This work is additive and read-only; any golden movement is a defect to investigate, not a change to regenerate.

- [ ] **Step 8: Update the docs**

In `CLAUDE.md`, add to the `analytics/` bullet list:

> - `weekly_path.py` — H10 partial-path predictiveness audit, pure library (spec `docs/superpowers/specs/2026-07-23-h10-partial-path-predictiveness-design.md`). Tests whether the week's AWR-normalized path at hour `h` predicts the return from `h` to the week's close, **beyond drift** — the non-tautological version of the cone's conditional-on-outcome framing. Signs the **causally demeaned** remaining return (expanding mean over strictly earlier weeks) and collapses the cross-section to **one observation per calendar week**, since 6,000 symbol-weeks are nowhere near independent. Pre-committed gate at `h ∈ {24,48,72,96,120}`, `bar = 0.05` AWR, Holm over the 5-hour family, plus an early/late sign-agreement condition. **Note:** `audit_guard`'s decisions are inverted relative to the sign of the mean (`DISABLE` ⇔ reliably positive), so `DISABLE → PREDICTIVE` and `ENABLE → REVERTING`. Consumed by `tools/weekly_path_audit.py`.

and to the `tools/` bullet list:

> - `weekly_path_audit.py` — H10 driver (`make buibui-weekly-path-audit`); read-only (`duckdb.connect(..., read_only=True)`). Loads each symbol's completed-week population through `weekly_cone.week_records()` — the shared-normalization invariant, so the audit and the Brief cannot disagree on AWR — then prints per-hour verdicts (PREDICTIVE / REVERTING / NO-EDGE / INSUFFICIENT) with DSR/PBO/MinTRL family stamps, magnitude terciles and the 168-hour curve, across universe / majors / BTC-only cohorts. Gates ST6 and ST7.

In `README.md`, add `make buibui-weekly-path-audit` to the audit-command list, matching the surrounding entries' format.

- [ ] **Step 9: Commit**

```bash
git add tools/weekly_path_audit.py tests/test_weekly_path_audit.py Makefile README.md CLAUDE.md
git commit -m "feat(h10): partial-path audit driver, make target, docs"
```

- [ ] **Step 10: Write the verdict document**

Create `docs/audits/2026-07-23-h10-partial-path-predictiveness.md` recording the actual output from Step 6: the per-cohort verdict tables, the family stamps, and — most importantly — **which row of spec §8 fires**. If every gated hour reads NO-EDGE, state plainly that ST6 and ST7 are now permanently closed and that the Brief's weekly language stays descriptive. A null is the valuable outcome here; report it without hedging.

Then update the SoT (`project_todo_master.md` H10 row) and `MEMORY.md` Current State with the verdict.

```bash
git add docs/audits/2026-07-23-h10-partial-path-predictiveness.md
git commit -m "docs(h10): partial-path predictiveness verdict"
```

---

## Notes for the implementer

- **`min_prior_obs = 52` is an addition to the spec**, not in §6's original table. It exists so the expanding baseline is not a one-sample mean in the earliest weeks. It is pre-committed here before any result; if you change it, you are tuning against an outcome. Flag it to the operator rather than adjusting it.
- **`audit_guard`'s INSUFFICIENT reason strings say "R"** (e.g. `does not clear ±0.05R`) because that module was written for trade R-multiples. Our units are AWR. The strings pass through into `HourVerdict.reasons` verbatim; leave them alone in the library and let the report header state the units, rather than string-patching another module's messages.
- **`hour_curve` calls `build_observations` 168 times** and is the slow part of a full run. `--skip-curve` exists for iteration. It is descriptive only — never let it influence a verdict.
- **Do not add hours to `cfg.hours` to "see more".** The 5-hour family is what makes Holm survivable; 168 tests would be near-guaranteed INSUFFICIENT regardless of truth (spec §6).
