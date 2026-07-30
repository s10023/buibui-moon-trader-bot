# Daily Market Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, read-only daily market brief per the approved spec
`docs/superpowers/specs/2026-07-04-daily-market-brief-design.md` — `analytics/brief/`
pure lib + `buibui brief` CLI + `GET /api/brief` + Svelte Brief page.

**Architecture:** Pure `analytics/brief/` package (one module per section: levels,
zones, seasonality, pundit, health; `bundle.py` orchestrator; `render.py` markdown).
Thin surfaces on top: CLI subcommand, FastAPI router, Svelte page. Pundit inputs are a
FILE contract (`docs/plans/pundit-calls.jsonl` + `docs/plans/pundit-priors.json`) —
never import scorer code. One additive change outside the package: keyword-only
`end_ms` on four `analytics/stats` computes (default `None` = unchanged behaviour).

**Tech Stack:** Python 3.11+, pandas, DuckDB, pytest (in-memory DB, no network),
FastAPI + pydantic, Svelte 5 + Vite.

## Global Constraints

- Branch: `feat/market-brief` (worktree `.claude/worktrees/feat+market-brief`). Run
  everything from the worktree root. Do NOT touch `feat/pundit-score`.
- Read-only over `analytics.db`: `duckdb.connect(..., read_only=True)` in the CLI;
  the API uses the existing `get_db` dependency. **No DB writes, no schema change.**
- Determinism: no wall-clock anywhere in `analytics/brief/` computation or rendering —
  `cfg.as_of_ms` is the only timestamp. Fixed as_of + fixed DB + fixed files ⇒
  byte-identical markdown and JSON.
- A bar is **completed** iff `open_time + tf_ms <= as_of_ms` (boundary = completed).
- mypy strict: every function annotated (incl. `-> None` for tests). ruff formatted.
- Tests: `duckdb.connect(":memory:")` + `init_schema`; no network; no golden-fixture
  movement (`make test-regression` must stay green untouched).
- After every Python task: `make lint-py && make typecheck` then the task's pytest
  command. Full `make test` at Task 13.
- Commit after every task (conventional commits). Commit messages end with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- If a step's stated anchor (function/line/pattern) does not exist as described,
  STOP and report instead of improvising.

---

### Task 1: Package skeleton — `_common.py`, `config.py`, `types.py`, test fixtures

**Files:**

- Create: `analytics/brief/__init__.py`
- Create: `analytics/brief/_common.py`
- Create: `analytics/brief/config.py`
- Create: `analytics/brief/types.py`
- Create: `tests/_brief_fixtures.py`
- Test: `tests/test_brief_types.py`

**Interfaces:**

- Produces: `BriefConfig` (frozen dataclass, fields below), `FALLBACK_SYMBOLS`,
  `default_symbols() -> tuple[tuple[str, ...], list[str]]`, `TF_MS`, `DAY_MS`,
  `BriefDataError`, `completed_bars(df, timeframe, as_of_ms) -> pd.DataFrame`,
  `parse_as_of_ms(value: str) -> int`, `day_ahead_dow(as_of_ms) -> str`,
  `day_ahead_label(as_of_ms) -> str`, all dataclasses in `types.py`,
  `error_panel(symbol, message) -> SymbolPanel`,
  `bundle_to_dict(bundle) -> dict[str, Any]`, and the test helpers
  `make_conn()` / `seed_symbol(conn, symbol, start_ms, n_days, base=100.0)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_types.py
"""Brief package skeleton: config defaults, completed-bar boundary, serialisation."""

import pandas as pd

from analytics.brief._common import (
    DAY_MS,
    completed_bars,
    day_ahead_dow,
    day_ahead_label,
    parse_as_of_ms,
)
from analytics.brief.config import FALLBACK_SYMBOLS, BriefConfig
from analytics.brief.types import (
    BriefBundle,
    HealthReport,
    PunditBoard,
    bundle_to_dict,
    error_panel,
)

AS_OF = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC (a Monday)


def test_config_defaults() -> None:
    cfg = BriefConfig(symbols=("BTCUSDT",), as_of_ms=AS_OF)
    assert cfg.stats_days == 180
    assert cfg.zone_tfs == ("4h", "1d")
    assert cfg.max_levels_per_side == 4
    assert cfg.max_zones_per_side == 2
    assert cfg.recent_call_days == 14
    assert cfg.max_recent_calls == 10
    assert FALLBACK_SYMBOLS == ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def test_completed_bars_boundary() -> None:
    df = pd.DataFrame(
        {
            "open_time": [AS_OF - 2 * DAY_MS, AS_OF - DAY_MS, AS_OF],
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
        }
    )
    out = completed_bars(df, "1d", AS_OF)
    # bar opening at AS_OF - DAY_MS closes exactly at AS_OF -> completed
    assert list(out["open_time"]) == [AS_OF - 2 * DAY_MS, AS_OF - DAY_MS]


def test_parse_as_of_ms_variants() -> None:
    assert parse_as_of_ms("2024-01-01T00:00:00Z") == AS_OF
    assert parse_as_of_ms("2024-01-01T00:00:00+00:00") == AS_OF
    assert parse_as_of_ms("2024-01-01T00:00:00") == AS_OF  # naive = UTC


def test_day_ahead_helpers() -> None:
    assert day_ahead_dow(AS_OF) == "Mon"
    assert day_ahead_label(AS_OF) == "Mon 2024-01-01"


def test_bundle_to_dict_json_safe() -> None:
    bundle = BriefBundle(
        as_of_ms=AS_OF,
        day_ahead="Mon 2024-01-01",
        panels=[error_panel("BTCUSDT", "boom")],
        pundit=PunditBoard(
            priors_status="absent",
            priors_age_days=None,
            min_n_marker=None,
            ledger_status="absent",
            ledger_total=0,
            ledger_skipped=0,
            recent_calls=[],
            authors=[],
            families=[],
        ),
        health=HealthReport(rows=[], notes=[], data_ok=True),
    )
    d = bundle_to_dict(bundle)
    assert d["panels"][0]["error"] == "boom"
    assert d["pundit"]["priors_status"] == "absent"
    import json

    json.dumps(d)  # must be JSON-serialisable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analytics.brief'`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/_common.py
"""Shared brief helpers: timeframe math, completed-bar filter, as-of parsing."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

TF_MS: dict[str, int] = {
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

DAY_MS = 86_400_000

_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class BriefDataError(Exception):
    """A symbol cannot produce a panel (missing / insufficient OHLCV)."""


def completed_bars(df: pd.DataFrame, timeframe: str, as_of_ms: int) -> pd.DataFrame:
    """Rows whose bar fully closed at or before ``as_of_ms``.

    A bar is completed iff ``open_time + tf_ms <= as_of_ms`` (the boundary
    counts as completed). The local sync keeps the still-forming candle in
    ``ohlcv``; every brief consumer must go through this filter except
    ``reference_levels.compute_levels`` (which needs the forming bar's open).
    """
    if df.empty:
        return df
    tf_ms = TF_MS[timeframe]
    out = df[df["open_time"] + tf_ms <= as_of_ms]
    return out.reset_index(drop=True)


def parse_as_of_ms(value: str) -> int:
    """ISO8601 string to Unix ms; naive datetimes are UTC; trailing 'Z' ok."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def day_ahead_dow(as_of_ms: int) -> str:
    """Short weekday name of the UTC day containing as_of (locale-safe)."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    return _DOW[int(ts.weekday())]


def day_ahead_label(as_of_ms: int) -> str:
    """e.g. "Fri 2026-07-04" — the day being prepped."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    return f"{_DOW[int(ts.weekday())]} {ts.strftime('%Y-%m-%d')}"
```

```python
# analytics/brief/config.py
"""BriefConfig — frozen configuration for the daily market brief."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FALLBACK_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@dataclass(frozen=True)
class BriefConfig:
    """All knobs for one brief computation. ``as_of_ms`` drives everything."""

    symbols: tuple[str, ...]
    as_of_ms: int
    stats_days: int = 180
    zone_tfs: tuple[str, ...] = ("4h", "1d")
    max_levels_per_side: int = 4
    max_zones_per_side: int = 2
    recent_call_days: int = 14
    max_recent_calls: int = 10
    ledger_path: Path = Path("docs/plans/pundit-calls.jsonl")
    priors_path: Path = Path("docs/plans/pundit-priors.json")


def default_symbols() -> tuple[tuple[str, ...], list[str]]:
    """coins.json keys (sorted), or FALLBACK_SYMBOLS plus a health note."""
    try:
        from utils.binance_client import load_coins_config

        symbols = tuple(sorted(load_coins_config()))
    except Exception as exc:  # coins.json is gitignored — absence is normal
        return FALLBACK_SYMBOLS, [
            f"coins.json unavailable ({exc}); using fallback symbols"
        ]
    if not symbols:
        return FALLBACK_SYMBOLS, ["coins.json empty; using fallback symbols"]
    return symbols, []
```

```python
# analytics/brief/types.py
"""Brief dataclasses + JSON-safe serialisation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class LevelRow:
    name: str
    price: float
    dist_atr: float
    swept: bool


@dataclass(frozen=True)
class ZoneRow:
    tf: str
    zone_type: str
    direction: str
    zone_low: float
    zone_high: float
    dist_atr: float
    inside: bool


@dataclass(frozen=True)
class SeasonalityStrip:
    dow: str
    bull_pct: float | None
    avg_range_pct: float | None
    sample_days: int | None
    high_session: str | None
    high_session_pct: float | None
    low_session: str | None
    low_session_pct: float | None
    weekly_low_still_ahead: float | None
    weekly_high_still_ahead: float | None
    typical_low_day: str | None
    typical_high_day: str | None


@dataclass(frozen=True)
class SymbolPanel:
    symbol: str
    ref_close: float
    ref_close_ts_ms: int
    atr14: float
    adr_pct: float | None
    regime_1d: str
    regime_4h: str
    levels_above: list[LevelRow]
    levels_below: list[LevelRow]
    zones_above: list[ZoneRow]
    zones_below: list[ZoneRow]
    seasonality: SeasonalityStrip | None
    error: str | None


def error_panel(symbol: str, message: str) -> SymbolPanel:
    """Stub panel for a symbol that failed to compute (renders the error)."""
    return SymbolPanel(
        symbol=symbol,
        ref_close=0.0,
        ref_close_ts_ms=0,
        atr14=0.0,
        adr_pct=None,
        regime_1d="unknown",
        regime_4h="unknown",
        levels_above=[],
        levels_below=[],
        zones_above=[],
        zones_below=[],
        seasonality=None,
        error=message,
    )


@dataclass(frozen=True)
class PunditAuthorPrior:
    author: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool


@dataclass(frozen=True)
class PunditFamilyPrior:
    family: str
    direction: str
    n: int
    hit_rate: float | None
    avg_atr_r: float | None
    flagged: bool


@dataclass(frozen=True)
class PunditCallRow:
    author: str
    symbol: str
    direction: str
    entry: str
    target: str
    horizon: str
    age_days: int
    on_panel: bool
    prior: PunditAuthorPrior | None


@dataclass(frozen=True)
class PunditBoard:
    priors_status: str  # "ok" | "absent" | "unreadable"
    priors_age_days: int | None
    min_n_marker: int | None
    ledger_status: str  # "ok" | "absent"
    ledger_total: int
    ledger_skipped: int
    recent_calls: list[PunditCallRow]
    authors: list[PunditAuthorPrior]
    families: list[PunditFamilyPrior]


@dataclass(frozen=True)
class HealthRow:
    symbol: str
    tf: str
    status: str  # "ok" | "stale" | "missing"
    bars_behind: int


@dataclass(frozen=True)
class HealthReport:
    rows: list[HealthRow]
    notes: list[str]
    data_ok: bool


@dataclass(frozen=True)
class BriefBundle:
    as_of_ms: int
    day_ahead: str  # e.g. "Fri 2026-07-04"
    panels: list[SymbolPanel]
    pundit: PunditBoard
    health: HealthReport


def bundle_to_dict(bundle: BriefBundle) -> dict[str, Any]:
    """JSON-safe dict for the API response and ``--json`` output."""
    return asdict(bundle)
```

```python
# analytics/brief/__init__.py
"""Daily market brief — pure, read-only, deterministic.

Spec: docs/superpowers/specs/2026-07-04-daily-market-brief-design.md
"""

from analytics.brief.config import FALLBACK_SYMBOLS, BriefConfig, default_symbols
from analytics.brief.types import BriefBundle, bundle_to_dict

__all__ = [
    "FALLBACK_SYMBOLS",
    "BriefBundle",
    "BriefConfig",
    "bundle_to_dict",
    "default_symbols",
]
```

```python
# tests/_brief_fixtures.py
"""Deterministic OHLCV seeding for the brief test suite (not collected by pytest)."""

from __future__ import annotations

import math

import duckdb
import pandas as pd

from analytics.data_store import init_schema
from analytics.store.market_data import upsert_ohlcv

DAY_MS = 86_400_000
H4_MS = 14_400_000
H1_MS = 3_600_000

# 2024-01-01 00:00:00 UTC — a Monday, exactly on a day boundary.
START_MS = 1_704_067_200_000


def make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def seed_symbol(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    start_ms: int = START_MS,
    n_days: int = 60,
    base: float = 100.0,
) -> None:
    """Insert deterministic 1d + 4h + 1h bars for ``n_days`` from ``start_ms``.

    Prices follow a smooth sine walk so ATR/levels/zones are all non-degenerate
    and reproducible. Intraday bars linearly interpolate the daily open->close.
    """
    rows: list[dict[str, object]] = []
    for i in range(n_days):
        d_open_ms = start_ms + i * DAY_MS
        o = base + 10.0 * math.sin(i / 7.0)
        c = o * (1.0 + 0.01 * math.sin(i / 3.0))
        hi = max(o, c) * 1.01
        lo = min(o, c) * 0.99
        rows.append(_row(symbol, "1d", d_open_ms, o, hi, lo, c))
        for j in range(6):
            bo = o + (c - o) * (j / 6.0)
            bc = o + (c - o) * ((j + 1) / 6.0)
            rows.append(
                _row(
                    symbol,
                    "4h",
                    d_open_ms + j * H4_MS,
                    bo,
                    max(bo, bc) * 1.002,
                    min(bo, bc) * 0.998,
                    bc,
                )
            )
        for j in range(24):
            bo = o + (c - o) * (j / 24.0)
            bc = o + (c - o) * ((j + 1) / 24.0)
            rows.append(
                _row(
                    symbol,
                    "1h",
                    d_open_ms + j * H1_MS,
                    bo,
                    max(bo, bc) * 1.001,
                    min(bo, bc) * 0.999,
                    bc,
                )
            )
    upsert_ohlcv(conn, pd.DataFrame(rows))


def _row(
    symbol: str,
    timeframe: str,
    open_time: int,
    o: float,
    h: float,
    lo: float,
    c: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": 1000.0,
        "taker_buy_volume": 500.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_types.py -v`
Expected: 5 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief tests/_brief_fixtures.py tests/test_brief_types.py
git commit -m "feat(brief): package skeleton — config, types, completed-bar helpers"
```

---

### Task 2: Additive `end_ms` on four stats computes (determinism prerequisite)

**Files:**

- Modify: `analytics/stats/_common.py`
- Modify: `analytics/stats/dow.py`
- Modify: `analytics/stats/session.py`
- Modify: `analytics/stats/weekly_p1p2.py`
- Modify: `analytics/stats/weekly_p2_timing.py`
- Test: `tests/test_stats_end_ms.py`

**Interfaces:**

- Consumes: `tests/_brief_fixtures.py` helpers from Task 1.
- Produces: `compute_dow_patterns(conn, symbol, days=180, *, end_ms=None)`,
  `compute_session_breakdown(conn, symbol, days=180, *, end_ms=None)`,
  `compute_weekly_p1p2(conn, symbol, days=180, *, end_ms=None)`,
  `compute_weekly_p2_timing(conn, symbol, days=180, *, end_ms=None)`, plus
  `analytics.stats._common._window_ms(days, end_ms) -> tuple[int, int]`.
  `end_ms=None` must be behaviourally identical to today (all existing callers,
  incl. the stats cache/API, unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stats_end_ms.py
"""Additive end_ms windowing on the four brief-consumed stats computes."""

from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

from analytics.stats.dow import compute_dow_patterns
from analytics.stats.session import compute_session_breakdown
from analytics.stats.weekly_p1p2 import compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import compute_weekly_p2_timing

SYM = "BTCUSDT"


def test_dow_end_ms_cuts_window() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    full = compute_dow_patterns(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    half = compute_dow_patterns(conn, SYM, 3650, end_ms=START_MS + 30 * DAY_MS)
    assert sum(r.sample_days for r in half.rows) < sum(r.sample_days for r in full.rows)


def test_session_end_ms_accepted() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    res = compute_session_breakdown(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    assert len(res.rows) == 3


def test_weekly_p1p2_end_ms_cuts_window() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    full = compute_weekly_p1p2(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    half = compute_weekly_p1p2(conn, SYM, 3650, end_ms=START_MS + 30 * DAY_MS)
    assert half.sample_weeks < full.sample_weeks


def test_weekly_p2_timing_end_ms_accepted() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    res = compute_weekly_p2_timing(conn, SYM, 3650, end_ms=START_MS + 60 * DAY_MS)
    assert "Mon" in res.low_still_ahead_by_dow


def test_default_end_ms_still_works() -> None:
    """end_ms=None (default) keeps the current now-anchored behaviour."""
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    # Seeded data is in 2024 — a now-anchored 180d window sees none of it,
    # so the compute raises (dow) or returns empty rows; either is 'unchanged'.
    try:
        res = compute_dow_patterns(conn, SYM, 180)
        assert sum(r.sample_days for r in res.rows) == 0
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_stats_end_ms.py -v`
Expected: FAIL with `TypeError: ... got an unexpected keyword argument 'end_ms'`

- [ ] **Step 3: Add `_window_ms` to `analytics/stats/_common.py`**

Append after the existing `_start_ms`:

```python
def _window_ms(days: int, end_ms: int | None) -> tuple[int, int]:
    """(start, end) Unix-ms window ending at ``end_ms`` (None = now).

    ``end_ms=None`` reproduces ``_start_ms(days)`` exactly, so callers that do
    not pass it are byte-identical to the pre-end_ms behaviour.
    """
    end = end_ms if end_ms is not None else int(datetime.now(tz=UTC).timestamp() * 1000)
    return end - days * 86_400_000, end
```

- [ ] **Step 4: Apply the transformation to `dow.py` (worked example)**

In `compute_dow_patterns`:

- Signature becomes:

```python
def compute_dow_patterns(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    days: int = 180,
    *,
    end_ms: int | None = None,
) -> DOWResult:
```

- Import `_window_ms` (keep `_DOW_SHORT`):
  `from analytics.stats._common import _DOW_SHORT, _window_ms`
- Replace `start = _start_ms(days)` with `start, end = _window_ms(days, end_ms)`.
- In the SQL, every `AND open_time >= $start_ms` gains a trailing
  `AND open_time <= $end_ms` (space-separated) immediately after it.
- The params dict gains `"end_ms": end`:
  `{"symbol": symbol, "start_ms": start, "end_ms": end}`.
- Docstring gains one line:
  `end_ms: window end (Unix ms); None = now (default, unchanged behaviour).`

- [ ] **Step 5: Apply the SAME six-part transformation to the other three modules**

Identically in `analytics/stats/session.py` (`compute_session_breakdown` — two SQL
queries, both get the `<= $end_ms` clause and the param),
`analytics/stats/weekly_p1p2.py` (`compute_weekly_p1p2` — note some clauses are
alias-prefixed: `h.open_time >= $start_ms` becomes
`h.open_time >= $start_ms AND h.open_time <= $end_ms`), and
`analytics/stats/weekly_p2_timing.py` (`compute_weekly_p2_timing` — same alias
note). Every `open_time >= $start_ms` occurrence in those functions must gain its
paired `<= $end_ms`. If a query in these functions does NOT use `$start_ms`, STOP
and report.

- [ ] **Step 6: Run tests**

Run: `poetry run pytest tests/test_stats_end_ms.py tests/test_stats_lib.py -v`
(if `tests/test_stats_lib.py` does not exist, run
`poetry run pytest tests/ -k "stats" -v` instead)
Expected: all PASS — new tests green, existing stats tests untouched.

- [ ] **Step 7: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/stats tests/test_stats_end_ms.py
git commit -m "feat(stats): additive keyword-only end_ms window on dow/session/weekly computes"
```

---

### Task 3: `levels.py` — reference-level gauge

**Files:**

- Create: `analytics/brief/levels.py`
- Test: `tests/test_brief_levels.py`

**Interfaces:**

- Consumes: `LevelRow` (Task 1); `analytics.reference_levels.compute_levels`,
  `sweep_flag` (existing).
- Produces: `atr14_wilder(df, period=14) -> float`,
  `adr_pct_14(completed_1d) -> float | None`,
  `build_level_rows(daily_df, completed_1d, as_of_ms, ref_close, atr14,
  max_per_side) -> tuple[list[LevelRow], list[LevelRow]]` (above, below —
  nearest-first, capped; `dist_atr <= 0` goes below).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_levels.py
"""Level gauge: ATR, ADR, distance/side split, sweep flags."""

import pandas as pd

from analytics.brief._common import DAY_MS
from analytics.brief.levels import adr_pct_14, atr14_wilder, build_level_rows

AS_OF = 1_704_067_200_000 + 30 * DAY_MS  # 2024-01-31 00:00 UTC (Wednesday)


def _daily(n: int, base: float = 100.0) -> pd.DataFrame:
    start = AS_OF - n * DAY_MS
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": start + i * DAY_MS,
                "open": base,
                "high": base + 2.0,
                "low": base - 2.0,
                "close": base + 1.0,
            }
        )
    return pd.DataFrame(rows)


def test_atr14_wilder_flat_series() -> None:
    df = _daily(30)
    atr = atr14_wilder(df)
    assert 3.9 < atr <= 4.0  # TR is constant 4.0 (high-low dominates)


def test_atr14_insufficient_rows() -> None:
    assert atr14_wilder(_daily(1)) == 0.0


def test_adr_pct_14() -> None:
    adr = adr_pct_14(_daily(30))
    assert adr is not None
    assert abs(adr - 0.04) < 1e-9  # (102-98)/100


def test_build_level_rows_sides_and_cap() -> None:
    df = _daily(30)
    above, below = build_level_rows(
        daily_df=df,
        completed_1d=df,
        as_of_ms=AS_OF,
        ref_close=101.0,
        atr14=4.0,
        max_per_side=4,
    )
    assert len(above) <= 4 and len(below) <= 4
    # nearest-first ordering on both sides
    assert [r.dist_atr for r in above] == sorted(r.dist_atr for r in above)
    dists_below = [r.dist_atr for r in below]
    assert dists_below == sorted(dists_below, reverse=True)
    # PDH exists (prev day high = 102) and sits above ref_close=101
    assert any(r.name == "PDH" for r in above)
    for r in above:
        assert r.dist_atr > 0
    for r in below:
        assert r.dist_atr <= 0


def test_build_level_rows_zero_atr_returns_empty() -> None:
    df = _daily(30)
    above, below = build_level_rows(df, df, AS_OF, 101.0, 0.0, 4)
    assert above == [] and below == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_levels.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.levels`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/levels.py
"""Reference-level gauge: ATR-normalised distances + sweep flags."""

from __future__ import annotations

import pandas as pd

from analytics.brief.types import LevelRow
from analytics.reference_levels import compute_levels, sweep_flag

# Extreme levels get sweep flags; sweep of a LOW is a long-side reclaim,
# sweep of a HIGH is a short-side rejection (reference_levels convention).
_SWEEP_DIRECTION: dict[str, str] = {
    "PDH": "short",
    "PWH": "short",
    "MonH": "short",
    "PDL": "long",
    "PWL": "long",
    "MonL": "long",
}


def atr14_wilder(df: pd.DataFrame, period: int = 14) -> float:
    """Last Wilder ATR value over the frame (0.0 when fewer than 2 rows).

    Same TR + ``ewm(alpha=1/period)`` formula as ``analytics/regime.py`` (that
    helper is private, so the brief carries its own copy).
    """
    if len(df) < 2:
        return 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return float(atr.iloc[-1])


def adr_pct_14(completed_1d: pd.DataFrame) -> float | None:
    """Mean (high-low)/open over the last 14 completed 1d bars."""
    if completed_1d.empty:
        return None
    tail = completed_1d.tail(14)
    opens = tail["open"].astype(float)
    valid = opens > 0
    if not bool(valid.any()):
        return None
    highs = tail["high"].astype(float)
    lows = tail["low"].astype(float)
    ranges = (highs - lows)[valid] / opens[valid]
    return float(ranges.mean())


def build_level_rows(
    daily_df: pd.DataFrame,
    completed_1d: pd.DataFrame,
    as_of_ms: int,
    ref_close: float,
    atr14: float,
    max_per_side: int,
) -> tuple[list[LevelRow], list[LevelRow]]:
    """(above, below) LevelRows — nearest-first, capped per side.

    ``daily_df`` must include the (possibly forming) bar containing as_of so
    the current-period opens (DO/WO/MO) resolve; ``completed_1d`` drives the
    sweep flags (no look-ahead). ``dist_atr <= 0`` lands on the below side.
    """
    if atr14 <= 0:
        return [], []
    levels = compute_levels(daily_df, as_of_ms)
    entry_idx = len(completed_1d) - 1
    rows: list[LevelRow] = []
    for name, price in levels.items():
        if price is None:
            continue
        dist = (float(price) - ref_close) / atr14
        swept = False
        direction = _SWEEP_DIRECTION.get(name)
        if direction is not None and entry_idx >= 0:
            swept = sweep_flag(completed_1d, entry_idx, float(price), direction)
        rows.append(LevelRow(name=name, price=float(price), dist_atr=dist, swept=swept))
    above = sorted((r for r in rows if r.dist_atr > 0), key=lambda r: r.dist_atr)
    below = sorted((r for r in rows if r.dist_atr <= 0), key=lambda r: -r.dist_atr)
    return above[:max_per_side], below[:max_per_side]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_levels.py -v`
Expected: 5 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/levels.py tests/test_brief_levels.py
git commit -m "feat(brief): reference-level gauge (ATR distances + sweep flags)"
```

---

### Task 4: `zones.py` — nearest active structural zones

**Files:**

- Create: `analytics/brief/zones.py`
- Test: `tests/test_brief_zones.py`

**Interfaces:**

- Consumes: `ZoneRow` (Task 1); `zones_lib` extractors (existing; note
  fvg/ob dicts carry `zone_low`/`zone_high`, eqh_eql/bos carry a single
  `price` key — normalise to `(lo, hi)`).
- Produces: `build_zone_rows(frames_by_tf, ref_close, atr14, max_per_side)
  -> tuple[list[ZoneRow], list[ZoneRow]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_zones.py
"""Zone selection: active filter, price-key normalisation, signed distance."""

from typing import Any
from unittest.mock import patch

import pandas as pd

from analytics.brief.zones import build_zone_rows


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [0, 1, 2],
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0],
        }
    )


def _fake_zones(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return [
        {
            "zone_type": "fvg",
            "direction": "bull",
            "zone_low": 90.0,
            "zone_high": 92.0,
            "active": True,
        },
        {
            "zone_type": "fvg",
            "direction": "bear",
            "zone_low": 110.0,
            "zone_high": 112.0,
            "active": True,
        },
        {
            "zone_type": "fvg",
            "direction": "bull",
            "zone_low": 95.0,
            "zone_high": 96.0,
            "active": False,
        },  # inactive -> dropped
        {
            "zone_type": "bos",
            "direction": "bull",
            "price": 98.0,
            "active": True,
        },  # single-price zone
        {
            "zone_type": "eqh",
            "direction": "bear",
            "price": 99.5,
            "active": True,
        },  # ref inside? no: 99.5 < 100 -> below
    ]


def _empty(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    return []


def test_build_zone_rows_normalises_and_signs() -> None:
    with (
        patch("analytics.brief.zones.extract_fvg_zones", _fake_zones),
        patch("analytics.brief.zones.extract_order_block_zones", _empty),
        patch("analytics.brief.zones.extract_eqh_eql_zones", _empty),
        patch("analytics.brief.zones.extract_bos_zones", _empty),
    ):
        above, below = build_zone_rows({"4h": _frame()}, 100.0, 4.0, 2)
    # above: only the 110-112 bear fvg -> dist (110-100)/4 = +2.5
    assert len(above) == 1 and abs(above[0].dist_atr - 2.5) < 1e-9
    # below: nearest-first -> eqh @99.5 (-0.125) then bos @98 (-0.5); cap=2
    assert [z.zone_type for z in below] == ["eqh", "bos"]
    assert below[0].zone_low == below[0].zone_high == 99.5
    # the inactive zone never appears
    assert all(not (z.zone_low == 95.0) for z in above + below)


def test_build_zone_rows_inside_marker() -> None:
    def one_zone(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "zone_type": "ob",
                "direction": "bull",
                "zone_low": 99.0,
                "zone_high": 101.0,
                "active": True,
            }
        ]

    with (
        patch("analytics.brief.zones.extract_fvg_zones", _empty),
        patch("analytics.brief.zones.extract_order_block_zones", one_zone),
        patch("analytics.brief.zones.extract_eqh_eql_zones", _empty),
        patch("analytics.brief.zones.extract_bos_zones", _empty),
    ):
        above, below = build_zone_rows({"1d": _frame()}, 100.0, 4.0, 2)
    assert above == []
    assert len(below) == 1 and below[0].inside and below[0].dist_atr == 0.0


def test_build_zone_rows_zero_atr() -> None:
    above, below = build_zone_rows({"4h": _frame()}, 100.0, 0.0, 2)
    assert above == [] and below == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_zones.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.zones`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/zones.py
"""Nearest active structural zones (fvg / ob / eqh_eql / bos)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from analytics.brief.types import ZoneRow
from analytics.zones_lib import (
    extract_bos_zones,
    extract_eqh_eql_zones,
    extract_fvg_zones,
    extract_order_block_zones,
)


def _zone_bounds(zone: dict[str, Any]) -> tuple[float, float] | None:
    """(low, high) — eqh_eql/bos zones carry a single ``price`` line."""
    if "zone_low" in zone and "zone_high" in zone:
        return float(zone["zone_low"]), float(zone["zone_high"])
    if "price" in zone:
        p = float(zone["price"])
        return p, p
    return None


def build_zone_rows(
    frames_by_tf: dict[str, pd.DataFrame],
    ref_close: float,
    atr14: float,
    max_per_side: int,
) -> tuple[list[ZoneRow], list[ZoneRow]]:
    """(above, below) nearest ACTIVE zones, nearest-first, capped per tf/side.

    ``frames_by_tf`` maps tf -> COMPLETED-bars OHLCV. Distances are signed
    ``(nearest_edge - ref_close) / atr14`` in ATR14(1d) units; price inside a
    zone -> ``dist_atr=0.0`` + ``inside`` marker, listed on the below side
    (consistent with the levels rule ``dist <= 0`` -> below).
    """
    if atr14 <= 0:
        return [], []
    above: list[ZoneRow] = []
    below: list[ZoneRow] = []
    for tf, df in frames_by_tf.items():
        rows: list[ZoneRow] = []
        zones: list[dict[str, Any]] = []
        zones.extend(extract_fvg_zones(df))
        zones.extend(extract_order_block_zones(df))
        zones.extend(extract_eqh_eql_zones(df))
        zones.extend(extract_bos_zones(df))
        for zone in zones:
            if not zone.get("active", False):
                continue
            bounds = _zone_bounds(zone)
            if bounds is None:
                continue
            lo, hi = bounds
            if ref_close < lo:
                dist = (lo - ref_close) / atr14
                inside = False
            elif ref_close > hi:
                dist = (hi - ref_close) / atr14
                inside = False
            else:
                dist = 0.0
                inside = True
            rows.append(
                ZoneRow(
                    tf=tf,
                    zone_type=str(zone["zone_type"]),
                    direction=str(zone["direction"]),
                    zone_low=lo,
                    zone_high=hi,
                    dist_atr=dist,
                    inside=inside,
                )
            )
        tf_above = sorted((r for r in rows if r.dist_atr > 0), key=lambda r: r.dist_atr)
        tf_below = sorted(
            (r for r in rows if r.dist_atr <= 0), key=lambda r: -r.dist_atr
        )
        above.extend(tf_above[:max_per_side])
        below.extend(tf_below[:max_per_side])
    return above, below
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_zones.py -v`
Expected: 3 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/zones.py tests/test_brief_zones.py
git commit -m "feat(brief): nearest active structural zones per timeframe"
```

---

### Task 5: `seasonality.py` — day-ahead strip

**Files:**

- Create: `analytics/brief/seasonality.py`
- Test: `tests/test_brief_seasonality.py`

**Interfaces:**

- Consumes: `SeasonalityStrip`, `day_ahead_dow` (Task 1); the four `end_ms`-capable
  stats computes (Task 2).
- Produces: `build_strip(conn, symbol, as_of_ms, stats_days)
  -> SeasonalityStrip | None` (None when every source failed).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_seasonality.py
"""Day-ahead seasonality strip distillation."""

import duckdb

from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

from analytics.brief.seasonality import build_strip

SYM = "BTCUSDT"
AS_OF = START_MS + 60 * DAY_MS


def test_build_strip_happy_path() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    strip = build_strip(conn, SYM, AS_OF, 60)
    assert strip is not None
    assert strip.dow in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    assert strip.bull_pct is not None and 0.0 <= strip.bull_pct <= 1.0
    assert strip.sample_days is not None and strip.sample_days > 0
    assert strip.high_session in ("Asia", "London", "NY")
    assert strip.low_session in ("Asia", "London", "NY")


def test_build_strip_no_data_returns_none() -> None:
    conn: duckdb.DuckDBPyConnection = make_conn()
    strip = build_strip(conn, "NODATAUSDT", AS_OF, 60)
    assert strip is None


def test_build_strip_deterministic() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    assert build_strip(conn, SYM, AS_OF, 60) == build_strip(conn, SYM, AS_OF, 60)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_seasonality.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.seasonality`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/seasonality.py
"""Day-ahead seasonality strip distilled from analytics/stats computes.

All four sources window to ``end_ms=as_of_ms`` (Task 2) so the strip is
--as-of reproducible. Each source is independent: a ValueError (no data)
drops that line, and only when ALL sources fail does the strip become None.
"""

from __future__ import annotations

import duckdb

from analytics.brief._common import day_ahead_dow
from analytics.brief.types import SeasonalityStrip
from analytics.stats.dow import compute_dow_patterns
from analytics.stats.session import compute_session_breakdown
from analytics.stats.weekly_p1p2 import compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import compute_weekly_p2_timing


def build_strip(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    as_of_ms: int,
    stats_days: int,
) -> SeasonalityStrip | None:
    dow = day_ahead_dow(as_of_ms)

    bull_pct: float | None = None
    avg_range_pct: float | None = None
    sample_days: int | None = None
    try:
        dow_res = compute_dow_patterns(conn, symbol, stats_days, end_ms=as_of_ms)
        row = next((r for r in dow_res.rows if r.dow == dow), None)
        if row is not None:
            bull_pct = row.bull_pct
            avg_range_pct = row.avg_range_pct
            sample_days = row.sample_days
    except ValueError:
        pass

    high_session: str | None = None
    high_session_pct: float | None = None
    low_session: str | None = None
    low_session_pct: float | None = None
    try:
        ses = compute_session_breakdown(conn, symbol, stats_days, end_ms=as_of_ms)
        if ses.rows:
            hi = max(ses.rows, key=lambda r: r.high_pct)
            lo = max(ses.rows, key=lambda r: r.low_pct)
            high_session, high_session_pct = hi.session, hi.high_pct
            low_session, low_session_pct = lo.session, lo.low_pct
    except ValueError:
        pass

    weekly_low_still_ahead: float | None = None
    weekly_high_still_ahead: float | None = None
    try:
        timing = compute_weekly_p2_timing(conn, symbol, stats_days, end_ms=as_of_ms)
        weekly_low_still_ahead = timing.low_still_ahead_by_dow.get(dow)
        weekly_high_still_ahead = timing.high_still_ahead_by_dow.get(dow)
    except ValueError:
        pass

    typical_low_day: str | None = None
    typical_high_day: str | None = None
    try:
        p1p2 = compute_weekly_p1p2(conn, symbol, stats_days, end_ms=as_of_ms)
        typical_low_day = p1p2.low_day
        typical_high_day = p1p2.high_day
    except ValueError:
        pass

    if (
        bull_pct is None
        and high_session is None
        and weekly_low_still_ahead is None
        and typical_low_day is None
    ):
        return None
    return SeasonalityStrip(
        dow=dow,
        bull_pct=bull_pct,
        avg_range_pct=avg_range_pct,
        sample_days=sample_days,
        high_session=high_session,
        high_session_pct=high_session_pct,
        low_session=low_session,
        low_session_pct=low_session_pct,
        weekly_low_still_ahead=weekly_low_still_ahead,
        weekly_high_still_ahead=weekly_high_still_ahead,
        typical_low_day=typical_low_day,
        typical_high_day=typical_high_day,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_seasonality.py -v`
Expected: 3 PASS. (If `test_build_strip_no_data_returns_none` fails because a
compute returns empty instead of raising, adjust the implementation guard — the
final `if ... is None` chain — not the test.)

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/seasonality.py tests/test_brief_seasonality.py
git commit -m "feat(brief): day-ahead seasonality strip (as-of-windowed stats)"
```

---

### Task 6: `pundit.py` — file-contract pundit board

**Files:**

- Create: `analytics/brief/pundit.py`
- Test: `tests/test_brief_pundit.py`

**Interfaces:**

- Consumes: `BriefConfig` (Task 1); `PunditAuthorPrior`, `PunditFamilyPrior`,
  `PunditCallRow`, `PunditBoard` (Task 1).
- Produces: `build_board(cfg: BriefConfig) -> PunditBoard` (pure file I/O; no conn).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_pundit.py
"""Pundit board: priors parsing, recent-call filtering, graceful degradation."""

import json
from pathlib import Path

from analytics.brief.config import BriefConfig
from analytics.brief.pundit import build_board

AS_OF = 1_704_067_200_000  # 2024-01-01 00:00 UTC
DAY_MS = 86_400_000


def _cfg(tmp_path: Path, **kwargs: object) -> BriefConfig:
    return BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=AS_OF,
        ledger_path=tmp_path / "calls.jsonl",
        priors_path=tmp_path / "priors.json",
        **kwargs,  # type: ignore[arg-type]
    )


def _call(author: str, symbol: str, days_before: int) -> str:
    ts_ms = AS_OF - days_before * DAY_MS
    iso = f"{__import__('datetime').datetime.fromtimestamp(ts_ms / 1000, tz=__import__('datetime').UTC).strftime('%Y-%m-%dT%H:%M:%S')}Z"
    return json.dumps(
        {
            "source": "twitter",
            "author": author,
            "url": "https://x.com/x/status/1",
            "call_ts_utc": iso,
            "symbol": symbol,
            "direction": "long",
            "entry": "zone 100-101 on a reclaim of the level with confirmation and volume",
            "stop": "99",
            "target": "110",
            "horizon": "swing",
            "confidence": "high",
            "raw_quote": "quote",
        }
    )


def _priors() -> str:
    return json.dumps(
        {
            "generated_at": "2023-12-31T00:00:00Z",
            "as_of": "2023-12-31T00:00:00Z",
            "policy": {"windows": {}, "atr": "atr14", "min_n_marker": 5},
            "authors": {
                "alice": {"n": 6, "hit_rate": 0.5, "avg_r": 0.2, "avg_atr_r": 0.9},
                "bob": {"n": 2, "hit_rate": 1.0, "avg_r": 1.0, "avg_atr_r": 1.5},
            },
            "families": {
                "sweep_reclaim": {"long": {"n": 6, "hit_rate": 0.6, "avg_atr_r": 1.1}}
            },
        }
    )


def test_board_happy_path(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(
        "\n".join(
            [
                _call("alice", "BTCUSDT", 1),
                _call("bob", "ETHUSDT", 3),
                _call("carol", "BTCUSDT", 30),  # outside 14d window
                "not json at all",
            ]
        )
    )
    cfg.priors_path.write_text(_priors())
    board = build_board(cfg)
    assert board.priors_status == "ok"
    assert board.priors_age_days == 1
    assert board.min_n_marker == 5
    assert board.ledger_total == 4 and board.ledger_skipped == 1
    assert [c.author for c in board.recent_calls] == ["alice", "bob"]
    assert board.recent_calls[0].on_panel is True  # BTCUSDT is a panel symbol
    assert board.recent_calls[1].on_panel is False
    assert board.recent_calls[0].prior is not None
    assert board.recent_calls[0].prior.flagged is False  # n=6 >= 5
    assert len(board.recent_calls[0].entry) <= 60
    assert board.authors[0].author == "alice"  # sorted by n desc
    bob = next(a for a in board.authors if a.author == "bob")
    assert bob.flagged is True  # n=2 < 5
    assert board.families[0].family == "sweep_reclaim"
    assert board.families[0].direction == "long"


def test_board_absent_files(tmp_path: Path) -> None:
    board = build_board(_cfg(tmp_path))
    assert board.priors_status == "absent"
    assert board.ledger_status == "absent"
    assert board.recent_calls == [] and board.authors == []


def test_board_unreadable_priors(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.priors_path.write_text("{broken json")
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", 1))
    board = build_board(cfg)
    assert board.priors_status == "unreadable"
    assert len(board.recent_calls) == 1
    assert board.recent_calls[0].prior is None


def test_future_dated_call_excluded(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.ledger_path.write_text(_call("alice", "BTCUSDT", -2))  # 2 days in future
    board = build_board(cfg)
    assert board.recent_calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_pundit.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.pundit`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/pundit.py
"""Pundit board — FILE contract over pundit-calls.jsonl + pundit-priors.json.

Deliberately no import from the scorer tool: the board must work while the
scorer is unbuilt/absent and degrade gracefully on missing or malformed files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analytics.brief.config import BriefConfig
from analytics.brief.types import (
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
    PunditFamilyPrior,
)

_DAY_MS = 86_400_000
_MAX_PRIORS_ROWS = 8  # render constant per spec, not config
_TEXT_TRUNC = 60


def _parse_iso_ms(value: str) -> int | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _truncate(text: str) -> str:
    return text if len(text) <= _TEXT_TRUNC else text[: _TEXT_TRUNC - 1] + "…"


def _opt_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _load_priors(
    path: Path, as_of_ms: int
) -> tuple[
    str, int | None, int | None, dict[str, PunditAuthorPrior], list[PunditFamilyPrior]
]:
    """(status, age_days, min_n_marker, authors_by_name, top families)."""
    if not path.exists():
        return "absent", None, None, {}, []
    try:
        data = json.loads(path.read_text())
        authors_raw = data["authors"]
        families_raw = data.get("families", {})
        min_n = int(data.get("policy", {}).get("min_n_marker", 5))
        if not isinstance(authors_raw, dict):
            raise TypeError("authors must be a mapping")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "unreadable", None, None, {}, []
    age_days: int | None = None
    gen_ms = _parse_iso_ms(str(data.get("generated_at", "")))
    if gen_ms is not None:
        age_days = max(0, (as_of_ms - gen_ms) // _DAY_MS)
    authors: dict[str, PunditAuthorPrior] = {}
    for name, cell in authors_raw.items():
        if not isinstance(cell, dict):
            continue
        n = int(cell.get("n", 0))
        authors[str(name)] = PunditAuthorPrior(
            author=str(name),
            n=n,
            hit_rate=_opt_float(cell.get("hit_rate")),
            avg_r=_opt_float(cell.get("avg_r")),
            avg_atr_r=_opt_float(cell.get("avg_atr_r")),
            flagged=n < min_n,
        )
    families: list[PunditFamilyPrior] = []
    if isinstance(families_raw, dict):
        for fam, dirs in families_raw.items():
            if not isinstance(dirs, dict):
                continue
            for direction, cell in dirs.items():
                if not isinstance(cell, dict):
                    continue
                n = int(cell.get("n", 0))
                families.append(
                    PunditFamilyPrior(
                        family=str(fam),
                        direction=str(direction),
                        n=n,
                        hit_rate=_opt_float(cell.get("hit_rate")),
                        avg_atr_r=_opt_float(cell.get("avg_atr_r")),
                        flagged=n < min_n,
                    )
                )
    families.sort(key=lambda f: -f.n)
    return "ok", age_days, min_n, authors, families[:_MAX_PRIORS_ROWS]


def build_board(cfg: BriefConfig) -> PunditBoard:
    status, age_days, min_n, authors_by_name, families = _load_priors(
        cfg.priors_path, cfg.as_of_ms
    )
    ledger_status = "ok" if cfg.ledger_path.exists() else "absent"
    total = 0
    skipped = 0
    calls: list[PunditCallRow] = []
    if ledger_status == "ok":
        for line in cfg.ledger_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(raw, dict):
                skipped += 1
                continue
            ts_ms = _parse_iso_ms(str(raw.get("call_ts_utc", "")))
            author = raw.get("author")
            symbol = raw.get("symbol")
            direction = raw.get("direction")
            if ts_ms is None or not author or not symbol or not direction:
                skipped += 1
                continue
            age = (cfg.as_of_ms - ts_ms) // _DAY_MS
            if age < 0 or age > cfg.recent_call_days:
                continue  # future-dated or too old — excluded, not skipped
            calls.append(
                PunditCallRow(
                    author=str(author),
                    symbol=str(symbol),
                    direction=str(direction),
                    entry=_truncate(str(raw.get("entry") or "")),
                    target=_truncate(str(raw.get("target") or "")),
                    horizon=str(raw.get("horizon") or ""),
                    age_days=int(age),
                    on_panel=str(symbol) in cfg.symbols,
                    prior=authors_by_name.get(str(author)),
                )
            )
    calls.sort(key=lambda c: c.age_days)  # newest first; ties keep file order
    top_authors = sorted(authors_by_name.values(), key=lambda a: -a.n)
    return PunditBoard(
        priors_status=status,
        priors_age_days=age_days,
        min_n_marker=min_n,
        ledger_status=ledger_status,
        ledger_total=total,
        ledger_skipped=skipped,
        recent_calls=calls[: cfg.max_recent_calls],
        authors=top_authors[:_MAX_PRIORS_ROWS],
        families=families,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_pundit.py -v`
Expected: 4 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/pundit.py tests/test_brief_pundit.py
git commit -m "feat(brief): pundit board over the ledger/priors file contract"
```

---

### Task 7: `health.py` — staleness footer

**Files:**

- Create: `analytics/brief/health.py`
- Test: `tests/test_brief_health.py`

**Interfaces:**

- Consumes: `TF_MS` (Task 1), `BriefConfig`, `HealthRow`, `HealthReport` (Task 1).
- Produces: `build_health(conn, cfg, notes: list[str]) -> HealthReport`.
  Tracked tfs = `dict.fromkeys([*cfg.zone_tfs, "1h"])` (panels read 4h/1d, the
  seasonality stats read 1h).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_health.py
"""Health footer: staleness boundaries, missing data, notes passthrough."""

from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

from analytics.brief.config import BriefConfig
from analytics.brief.health import build_health

SYM = "BTCUSDT"


def _cfg(as_of_ms: int) -> BriefConfig:
    return BriefConfig(symbols=(SYM,), as_of_ms=as_of_ms)


def test_health_fresh() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    report = build_health(conn, _cfg(START_MS + 60 * DAY_MS), [])
    assert report.data_ok is True
    assert {r.tf for r in report.rows} == {"4h", "1d", "1h"}
    assert all(r.status == "ok" for r in report.rows)


def test_health_stale_after_gap() -> None:
    conn = make_conn()
    seed_symbol(conn, SYM, START_MS, 60)
    # as_of 3 days past the last seeded close -> 1d is 3 bars behind
    report = build_health(conn, _cfg(START_MS + 63 * DAY_MS), [])
    assert report.data_ok is False
    d1 = next(r for r in report.rows if r.tf == "1d")
    assert d1.status == "stale" and d1.bars_behind == 3


def test_health_missing_symbol() -> None:
    conn = make_conn()
    report = build_health(conn, _cfg(START_MS), ["a note"])
    assert report.data_ok is False
    assert all(r.status == "missing" for r in report.rows)
    assert report.notes == ["a note"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_health.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.health`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/health.py
"""Data-health footer: OHLCV staleness per symbol x tf."""

from __future__ import annotations

import duckdb

from analytics.brief._common import TF_MS
from analytics.brief.config import BriefConfig
from analytics.brief.types import HealthReport, HealthRow


def _check_symbol_tf(
    conn: duckdb.DuckDBPyConnection, symbol: str, tf: str, as_of_ms: int
) -> HealthRow:
    tf_ms = TF_MS[tf]
    row = conn.execute(
        "SELECT max(open_time) FROM ohlcv "
        "WHERE symbol = ? AND timeframe = ? AND open_time <= ?",
        [symbol, tf, as_of_ms - tf_ms],  # completed bars only
    ).fetchone()
    last_open = row[0] if row is not None else None
    if last_open is None:
        return HealthRow(symbol=symbol, tf=tf, status="missing", bars_behind=0)
    bars_behind = int((as_of_ms - (int(last_open) + tf_ms)) // tf_ms)
    status = "stale" if bars_behind >= 1 else "ok"
    return HealthRow(symbol=symbol, tf=tf, status=status, bars_behind=bars_behind)


def build_health(
    conn: duckdb.DuckDBPyConnection, cfg: BriefConfig, notes: list[str]
) -> HealthReport:
    tfs = list(dict.fromkeys([*cfg.zone_tfs, "1h"]))
    rows = [
        _check_symbol_tf(conn, symbol, tf, cfg.as_of_ms)
        for symbol in cfg.symbols
        for tf in tfs
    ]
    return HealthReport(
        rows=rows,
        notes=list(notes),
        data_ok=all(r.status == "ok" for r in rows),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_health.py -v`
Expected: 3 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/health.py tests/test_brief_health.py
git commit -m "feat(brief): per-symbol OHLCV staleness health footer"
```

---

### Task 8: `bundle.py` — orchestrator

**Files:**

- Create: `analytics/brief/bundle.py`
- Modify: `analytics/brief/__init__.py`
- Test: `tests/test_brief_bundle.py`

**Interfaces:**

- Consumes: everything from Tasks 1–7; `analytics.regime.classify_series`;
  `analytics.store.market_data.get_ohlcv`.
- Produces: `compute_brief(conn, cfg, extra_notes=None) -> BriefBundle`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_bundle.py
"""Orchestrator: panel assembly, per-symbol isolation, determinism."""

from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig

AS_OF = START_MS + 60 * DAY_MS


def _cfg(symbols: tuple[str, ...]) -> BriefConfig:
    return BriefConfig(symbols=symbols, as_of_ms=AS_OF, stats_days=60)


def test_compute_brief_happy_path() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    assert bundle.as_of_ms == AS_OF
    panel = bundle.panels[0]
    assert panel.error is None
    assert panel.symbol == "BTCUSDT"
    assert panel.ref_close > 0 and panel.atr14 > 0
    assert panel.regime_1d in ("trend", "range", "high_vol", "unknown")
    assert panel.levels_above or panel.levels_below
    assert panel.seasonality is not None
    assert bundle.health.data_ok is True


def test_compute_brief_symbol_isolation() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT", "NODATAUSDT")))
    ok, bad = bundle.panels
    assert ok.error is None
    assert bad.symbol == "NODATAUSDT" and bad.error is not None
    assert "insufficient 1d history" in bad.error


def test_compute_brief_deterministic() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    assert compute_brief(conn, _cfg(("BTCUSDT",))) == compute_brief(
        conn, _cfg(("BTCUSDT",))
    )


def test_compute_brief_extra_notes_flow_to_health() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)), extra_notes=["fallback"])
    assert bundle.health.notes == ["fallback"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_bundle.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.bundle`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/bundle.py
"""compute_brief — orchestrator assembling the BriefBundle (read-only)."""

from __future__ import annotations

import logging

import duckdb
import pandas as pd

from analytics.brief._common import (
    DAY_MS,
    BriefDataError,
    completed_bars,
    day_ahead_label,
)
from analytics.brief.config import BriefConfig
from analytics.brief.health import build_health
from analytics.brief.levels import adr_pct_14, atr14_wilder, build_level_rows
from analytics.brief.pundit import build_board
from analytics.brief.seasonality import build_strip
from analytics.brief.types import BriefBundle, SymbolPanel, error_panel
from analytics.brief.zones import build_zone_rows
from analytics.regime import classify_series
from analytics.store.market_data import get_ohlcv

logger = logging.getLogger(__name__)

_DAILY_FETCH_DAYS = 500  # regime 1d needs ~90d ATR history + EMA warmup
_H4_FETCH_DAYS = 200
_MIN_DAILY_BARS = 15  # ATR14 + one reference bar


def _regime_label(df: pd.DataFrame, timeframe: str) -> str:
    if df.empty:
        return "unknown"
    return str(classify_series(df, timeframe).iloc[-1])


def _compute_panel(
    conn: duckdb.DuckDBPyConnection, symbol: str, cfg: BriefConfig
) -> SymbolPanel:
    as_of = cfg.as_of_ms
    daily = get_ohlcv(conn, symbol, "1d", as_of - _DAILY_FETCH_DAYS * DAY_MS, as_of)
    completed_1d = completed_bars(daily, "1d", as_of)
    if len(completed_1d) < _MIN_DAILY_BARS:
        raise BriefDataError(
            f"insufficient 1d history ({len(completed_1d)} completed bars)"
        )
    ref_bar = completed_1d.iloc[-1]
    ref_close = float(ref_bar["close"])
    ref_ts = int(ref_bar["open_time"])
    atr = atr14_wilder(completed_1d)
    levels_above, levels_below = build_level_rows(
        daily, completed_1d, as_of, ref_close, atr, cfg.max_levels_per_side
    )
    frames: dict[str, pd.DataFrame] = {}
    for tf in cfg.zone_tfs:
        if tf == "1d":
            frames[tf] = completed_1d
        else:
            raw = get_ohlcv(conn, symbol, tf, as_of - _H4_FETCH_DAYS * DAY_MS, as_of)
            frames[tf] = completed_bars(raw, tf, as_of)
    zones_above, zones_below = build_zone_rows(
        frames, ref_close, atr, cfg.max_zones_per_side
    )
    return SymbolPanel(
        symbol=symbol,
        ref_close=ref_close,
        ref_close_ts_ms=ref_ts,
        atr14=atr,
        adr_pct=adr_pct_14(completed_1d),
        regime_1d=_regime_label(completed_1d, "1d"),
        regime_4h=_regime_label(frames.get("4h", pd.DataFrame()), "4h"),
        levels_above=levels_above,
        levels_below=levels_below,
        zones_above=zones_above,
        zones_below=zones_below,
        seasonality=build_strip(conn, symbol, as_of, cfg.stats_days),
        error=None,
    )


def compute_brief(
    conn: duckdb.DuckDBPyConnection,
    cfg: BriefConfig,
    extra_notes: list[str] | None = None,
) -> BriefBundle:
    """Assemble the full bundle. One failing symbol never kills the brief."""
    panels: list[SymbolPanel] = []
    for symbol in cfg.symbols:
        try:
            panels.append(_compute_panel(conn, symbol, cfg))
        except Exception as exc:  # per-symbol isolation is the contract
            logger.warning("brief: panel failed for %s: %s", symbol, exc)
            panels.append(error_panel(symbol, str(exc)))
    return BriefBundle(
        as_of_ms=cfg.as_of_ms,
        day_ahead=day_ahead_label(cfg.as_of_ms),
        panels=panels,
        pundit=build_board(cfg),
        health=build_health(conn, cfg, list(extra_notes or [])),
    )
```

Update `analytics/brief/__init__.py` to:

```python
"""Daily market brief — pure, read-only, deterministic.

Spec: docs/superpowers/specs/2026-07-04-daily-market-brief-design.md
"""

from analytics.brief.bundle import compute_brief
from analytics.brief.config import FALLBACK_SYMBOLS, BriefConfig, default_symbols
from analytics.brief.types import BriefBundle, bundle_to_dict

__all__ = [
    "FALLBACK_SYMBOLS",
    "BriefBundle",
    "BriefConfig",
    "bundle_to_dict",
    "compute_brief",
    "default_symbols",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_bundle.py -v`
Expected: 4 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/bundle.py analytics/brief/__init__.py tests/test_brief_bundle.py
git commit -m "feat(brief): compute_brief orchestrator with per-symbol isolation"
```

---

### Task 9: `render.py` — deterministic markdown

**Files:**

- Create: `analytics/brief/render.py`
- Modify: `analytics/brief/__init__.py`
- Test: `tests/test_brief_render.py`

**Interfaces:**

- Consumes: all Task 1 types; `compute_brief` (Task 8) in the e2e test.
- Produces: `render_markdown(bundle: BriefBundle) -> str`, plus formatting
  helpers `fmt_price`, `fmt_dist`, `fmt_frac`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brief_render.py
"""Renderer: formatting rules + end-to-end byte-stability."""

import json
from pathlib import Path

import duckdb

from tests._brief_fixtures import DAY_MS, START_MS, make_conn, seed_symbol

from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig
from analytics.brief.render import fmt_dist, fmt_frac, fmt_price, render_markdown

AS_OF = START_MS + 60 * DAY_MS


def test_fmt_price_tiers() -> None:
    assert fmt_price(61420.4) == "61,420"
    assert fmt_price(101.234) == "101.23"
    assert fmt_price(0.12345) == "0.1235"


def test_fmt_dist_and_frac() -> None:
    assert fmt_dist(0.256) == "+0.26"
    assert fmt_dist(-1.164) == "-1.16"
    assert fmt_frac(0.58) == "58%"


def _seeded_cfg(tmp_path: Path) -> tuple[BriefConfig, duckdb.DuckDBPyConnection]:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    ledger = tmp_path / "calls.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "author": "alice",
                "symbol": "BTCUSDT",
                "direction": "long",
                "call_ts_utc": "2024-02-28T00:00:00Z",
                "entry": "zone",
                "target": "moon",
                "horizon": "swing",
            }
        )
    )
    cfg = BriefConfig(
        symbols=("BTCUSDT",),
        as_of_ms=AS_OF,
        stats_days=60,
        ledger_path=ledger,
        priors_path=tmp_path / "missing-priors.json",
    )
    return cfg, conn


def test_render_end_to_end_byte_stable(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)
    out1 = render_markdown(compute_brief(conn, cfg))
    out2 = render_markdown(compute_brief(conn, cfg))
    assert out1 == out2
    assert "BUIBUI DAILY BRIEF" in out1
    assert "── BTCUSDT" in out1
    assert "Levels   above →" in out1
    assert "── PUNDIT BOARD" in out1
    assert "run make buibui-pundit-score" in out1  # absent priors note
    assert "── HEALTH ──" in out1
    assert "alice" in out1


def test_render_error_panel(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)
    cfg2 = BriefConfig(
        symbols=("NODATAUSDT",),
        as_of_ms=AS_OF,
        stats_days=60,
        ledger_path=cfg.ledger_path,
        priors_path=cfg.priors_path,
    )
    out = render_markdown(compute_brief(conn, cfg2))
    assert "ERROR: " in out
    assert "data ⚠" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_render.py -v`
Expected: FAIL with `ModuleNotFoundError` on `analytics.brief.render`

- [ ] **Step 3: Write the implementation**

```python
# analytics/brief/render.py
"""Deterministic markdown renderer for the BriefBundle (no wall-clock)."""

from __future__ import annotations

import pandas as pd

from analytics.brief.types import (
    BriefBundle,
    LevelRow,
    PunditAuthorPrior,
    PunditBoard,
    PunditCallRow,
    SeasonalityStrip,
    SymbolPanel,
    ZoneRow,
)


def fmt_price(value: float) -> str:
    """>=1000 -> 0dp grouped; >=1 -> 2dp; else 4dp."""
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 1:
        return f"{value:,.2f}"
    return f"{value:.4f}"


def fmt_dist(value: float) -> str:
    return f"{value:+.2f}"


def fmt_frac(value: float) -> str:
    return f"{value * 100:.0f}%"


def _level_str(row: LevelRow) -> str:
    swept = ", swept✓" if row.swept else ""
    return f"{row.name} {fmt_price(row.price)} ({fmt_dist(row.dist_atr)}{swept})"


def _zone_str(row: ZoneRow) -> str:
    if row.zone_low == row.zone_high:
        span = fmt_price(row.zone_low)
    else:
        span = f"{fmt_price(row.zone_low)}–{fmt_price(row.zone_high)}"
    marker = "inside" if row.inside else fmt_dist(row.dist_atr)
    return f"{row.tf} {row.zone_type.upper()}·{row.direction} {span} ({marker})"


def _strip_lines(strip: SeasonalityStrip | None) -> list[str]:
    if strip is None:
        return ["Day/Week seasonality: n/a"]
    parts: list[str] = []
    if strip.bull_pct is not None and strip.avg_range_pct is not None:
        parts.append(
            f"{strip.dow}: bull {fmt_frac(strip.bull_pct)} · avg range "
            f"{strip.avg_range_pct * 100:.1f}% (n={strip.sample_days})"
        )
    if (
        strip.high_session is not None
        and strip.high_session_pct is not None
        and strip.low_session is not None
        and strip.low_session_pct is not None
    ):
        parts.append(
            f"high most often {strip.high_session} "
            f"{fmt_frac(strip.high_session_pct)} · low {strip.low_session} "
            f"{fmt_frac(strip.low_session_pct)}"
        )
    lines = [f"Day/Week {' · '.join(parts)}" if parts else "Day/Week seasonality: n/a"]
    weekly: list[str] = []
    if strip.weekly_low_still_ahead is not None:
        weekly.append(f"low still ahead {fmt_frac(strip.weekly_low_still_ahead)}")
    if strip.weekly_high_still_ahead is not None:
        weekly.append(f"high still ahead {fmt_frac(strip.weekly_high_still_ahead)}")
    if strip.typical_low_day and strip.typical_high_day:
        weekly.append(
            f"typical low {strip.typical_low_day} / high {strip.typical_high_day}"
        )
    if weekly:
        lines.append(f"         week: {' · '.join(weekly)}")
    return lines


def _panel_lines(panel: SymbolPanel) -> list[str]:
    lines = [f"── {panel.symbol} " + "─" * 44]
    if panel.error is not None:
        lines.append(f"ERROR: {panel.error}")
        return lines
    adr = f" · ADR {fmt_frac(panel.adr_pct)}" if panel.adr_pct is not None else ""
    lines.append(
        f"Close {fmt_price(panel.ref_close)} · Regime 1d {panel.regime_1d} / "
        f"4h {panel.regime_4h} · ATR14(1d) {fmt_price(panel.atr14)}{adr}"
    )
    above = " · ".join(_level_str(r) for r in panel.levels_above) or "none"
    below = " · ".join(_level_str(r) for r in panel.levels_below) or "none"
    lines.append(f"Levels   above → {above}")
    lines.append(f"         below → {below}")
    zone_bits = " · ".join(
        _zone_str(r) for r in [*panel.zones_above, *panel.zones_below]
    )
    lines.append(f"Zones    {zone_bits or 'none'}")
    lines.extend(_strip_lines(panel.seasonality))
    return lines


def _author_str(prior: PunditAuthorPrior) -> str:
    if prior.flagged:
        return f"(⚠ n={prior.n})"
    bits = [f"n={prior.n}"]
    if prior.hit_rate is not None:
        bits.append(fmt_frac(prior.hit_rate))
    if prior.avg_atr_r is not None:
        bits.append(f"{prior.avg_atr_r:+.1f} ATR-R̄")
    return "(" + " · ".join(bits) + ")"


def _call_line(call: PunditCallRow) -> str:
    marker = "● " if call.on_panel else "  "
    prior = f" {_author_str(call.prior)}" if call.prior is not None else ""
    target = f' → "{call.target}"' if call.target else ""
    horizon = f" · {call.horizon}" if call.horizon else ""
    return (
        f"{marker}{call.author}{prior} {call.symbol} {call.direction} — "
        f'"{call.entry}"{target}{horizon} · {call.age_days}d'
    )


def _family_str(board: PunditBoard) -> str:
    bits: list[str] = []
    for f in board.families:
        cell = f"{f.family}/{f.direction} n={f.n}"
        if f.flagged:
            cell += " ⚠"
        else:
            if f.hit_rate is not None:
                cell += f" · {fmt_frac(f.hit_rate)}"
            if f.avg_atr_r is not None:
                cell += f" · {f.avg_atr_r:+.1f} ATR-R̄"
        bits.append(cell)
    return " | ".join(bits)


def _pundit_lines(board: PunditBoard) -> list[str]:
    if board.priors_status == "ok":
        priors_bit = f"priors {board.priors_age_days}d old"
    else:
        priors_bit = f"priors: {board.priors_status} — run make buibui-pundit-score"
    if board.ledger_status == "ok":
        ledger_bit = (
            f"ledger {board.ledger_total} calls · {len(board.recent_calls)} recent"
        )
    else:
        ledger_bit = "ledger: not found"
    lines = [f"── PUNDIT BOARD ({priors_bit} · {ledger_bit}) " + "─" * 8]
    for call in board.recent_calls:
        lines.append(_call_line(call))
    if not board.recent_calls:
        lines.append("no recent calls")
    if board.families:
        lines.append(f"FAMILIES  {_family_str(board)}")
    return lines


def _health_lines(bundle: BriefBundle) -> list[str]:
    parts: list[str] = []
    for symbol in dict.fromkeys(r.symbol for r in bundle.health.rows):
        tf_bits: list[str] = []
        for row in bundle.health.rows:
            if row.symbol != symbol:
                continue
            if row.status == "ok":
                tf_bits.append(f"{row.tf}✓")
            elif row.status == "stale":
                tf_bits.append(f"{row.tf}⚠ stale ({row.bars_behind} bars)")
            else:
                tf_bits.append(f"{row.tf}✗ no data")
        parts.append(f"{symbol} " + " ".join(tf_bits))
    board = bundle.pundit
    if board.priors_status == "ok":
        parts.append(f"priors {board.priors_age_days}d")
    else:
        parts.append(f"priors {board.priors_status}")
    if board.ledger_status == "ok":
        parts.append(f"ledger {board.ledger_total} ({board.ledger_skipped} skipped)")
    else:
        parts.append("ledger absent")
    lines = ["── HEALTH ── " + " · ".join(parts)]
    lines.extend(f"note: {n}" for n in bundle.health.notes)
    return lines


def render_markdown(bundle: BriefBundle) -> str:
    as_of = pd.Timestamp(bundle.as_of_ms, unit="ms", tz="UTC").strftime(
        "%Y-%m-%d %H:%M"
    )
    data = "OK" if bundle.health.data_ok else "⚠ (see health)"
    lines = [
        f"BUIBUI DAILY BRIEF — {bundle.day_ahead} · as-of {as_of} UTC · data {data}",
        "",
    ]
    for panel in bundle.panels:
        lines.extend(_panel_lines(panel))
        lines.append("")
    lines.extend(_pundit_lines(bundle.pundit))
    lines.append("")
    lines.extend(_health_lines(bundle))
    return "\n".join(lines)
```

Update `analytics/brief/__init__.py`: add
`from analytics.brief.render import render_markdown` and `"render_markdown"`
to `__all__` (keep the list sorted).

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_brief_render.py -v`
Expected: 4 PASS

- [ ] **Step 5: Gate + commit**

```bash
make lint-py && make typecheck
git add analytics/brief/render.py analytics/brief/__init__.py tests/test_brief_render.py
git commit -m "feat(brief): deterministic markdown renderer + e2e byte-stability test"
```

---

### Task 10: CLI — `buibui brief` + Makefile + docs

**Files:**

- Create: `cli/brief.py`
- Modify: `cli/main.py`
- Modify: `Makefile`
- Modify: `README.md` (CLI section)
- Modify: `CLAUDE.md` (CLI list + Project Structure)
- Test: `tests/test_cli_brief.py`

**Interfaces:**

- Consumes: `compute_brief`, `BriefConfig`, `default_symbols`, `render_markdown`,
  `bundle_to_dict`, `parse_as_of_ms` (Tasks 1–9); `DEFAULT_DB_PATH` from
  `analytics.store.schema` (repo rule: import from there or `analytics.data_store`).
- Produces: `run_brief_cmd(args) -> None`, `add_brief_subparser(subparsers) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_brief.py
"""CLI wiring: run_brief_cmd prints the brief; --json/--markdown write files."""

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb

from tests._brief_fixtures import START_MS, seed_symbol

from analytics.data_store import init_schema
from cli.brief import run_brief_cmd

AS_OF_ISO = "2024-03-01T00:00:00Z"  # START_MS + 60 days


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    conn = duckdb.connect(str(db))
    init_schema(conn)
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    conn.close()
    return db


def _args(db: Path, tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "symbols": ["BTCUSDT"],
        "db": str(db),
        "as_of": AS_OF_ISO,
        "days": 60,
        "json": None,
        "markdown": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_brief_cmd_prints_brief(tmp_path: Path, capsys: Any) -> None:
    db = _make_db(tmp_path)
    run_brief_cmd(_args(db, tmp_path))
    out = capsys.readouterr().out
    assert "BUIBUI DAILY BRIEF" in out
    assert "── BTCUSDT" in out


def test_run_brief_cmd_writes_outputs(tmp_path: Path, capsys: Any) -> None:
    db = _make_db(tmp_path)
    json_path = tmp_path / "brief.json"
    md_path = tmp_path / "brief.md"
    run_brief_cmd(_args(db, tmp_path, json=str(json_path), markdown=str(md_path)))
    capsys.readouterr()
    data = json.loads(json_path.read_text())
    assert data["panels"][0]["symbol"] == "BTCUSDT"
    assert "BUIBUI DAILY BRIEF" in md_path.read_text()


def test_run_brief_cmd_all_panels_failed_exits_1(tmp_path: Path, capsys: Any) -> None:
    db = _make_db(tmp_path)
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        run_brief_cmd(_args(db, tmp_path, symbols=["NODATAUSDT"]))
    assert exc_info.value.code == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_cli_brief.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cli.brief'`

- [ ] **Step 3: Write `cli/brief.py`**

```python
# cli/brief.py
"""Buibui CLI — `brief` subcommand (daily market brief, read-only)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import duckdb

from analytics.brief._common import parse_as_of_ms
from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig, default_symbols
from analytics.brief.render import render_markdown
from analytics.brief.types import bundle_to_dict
from analytics.store.schema import DEFAULT_DB_PATH


def run_brief_cmd(args: argparse.Namespace) -> None:
    as_of_ms = parse_as_of_ms(args.as_of) if args.as_of else int(time.time() * 1000)
    notes: list[str] = []
    if args.symbols:
        symbols = tuple(args.symbols)
    else:
        symbols, notes = default_symbols()
    cfg = BriefConfig(symbols=symbols, as_of_ms=as_of_ms, stats_days=args.days)
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        bundle = compute_brief(conn, cfg, extra_notes=notes)
    finally:
        conn.close()
    markdown = render_markdown(bundle)
    print(markdown)
    if args.json:
        Path(args.json).write_text(json.dumps(bundle_to_dict(bundle), indent=2))
    if args.markdown:
        Path(args.markdown).write_text(markdown + "\n")
    if bundle.panels and all(p.error is not None for p in bundle.panels):
        raise SystemExit(1)


def add_brief_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = subparsers.add_parser(
        "brief",
        help="Daily market brief: levels, zones, regime, seasonality, pundit board",
    )
    p.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to panel (default: coins.json keys)",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="DuckDB path")
    p.add_argument(
        "--as-of",
        default=None,
        dest="as_of",
        help="ISO8601 anchor, e.g. 2026-07-04T00:10:00Z (default: now)",
    )
    p.add_argument("--days", type=int, default=180, help="Seasonality window in days")
    p.add_argument(
        "--json", default=None, help="Also write the bundle JSON to this path"
    )
    p.add_argument(
        "--markdown", default=None, help="Also write the markdown to this path"
    )
    p.set_defaults(func=run_brief_cmd)
```

- [ ] **Step 4: Register in `cli/main.py`**

The import block currently reads:

```python
from cli import (
    analytics,
    backtest,
    digest,
    monitor,
    param,
    portfolio,
    recalibrate,
    signal,
    web,
)
```

Add `brief,` after `backtest,` (alphabetical). Then in `main()`, after
`backtest.add_backtest_subparser(subparsers)`, add:

```python
    brief.add_brief_subparser(subparsers)
```

- [ ] **Step 5: Makefile target**

Next to the other `buibui-*` targets (e.g. after the `buibui-portfolio-replay`
block), add the following — NOTE: the doc shows 4 spaces for lint reasons, but
the real Makefile recipe lines MUST be indented with hard TABs:

```make
.PHONY: buibui-brief
buibui-brief:  ## Daily market brief (read-only; SYMBOLS=/AS_OF= optional)
    @poetry run python buibui.py brief \
        $(if $(SYMBOLS),--symbols $(SYMBOLS),) \
        $(if $(AS_OF),--as-of $(AS_OF),)
```

- [ ] **Step 6: Docs**

- `README.md`: in the CLI subcommand list add
  `- \`buibui brief\` — daily market brief (levels/zones/regime/seasonality/pundit board; \`make buibui-brief\`)`.
- `CLAUDE.md`: (a) in the "## CLI" list add the same one-line bullet;
  (b) in "## Project Structure" under `analytics/`, add a bullet:
  `\`brief/\` — daily market-brief package (spec docs/superpowers/specs/2026-07-04-daily-market-brief-design.md): pure read-only BriefBundle over levels/zones/regime/seasonality/pundit files + deterministic markdown renderer; surfaces = \`buibui brief\` CLI, GET /api/brief, Svelte Brief page. Four stats computes gained additive keyword-only \`end_ms\` (default None = unchanged) for --as-of determinism.`

- [ ] **Step 7: Run tests**

Run: `poetry run pytest tests/test_cli_brief.py tests/test_cli.py -v`
Expected: PASS (new + existing CLI tests)

- [ ] **Step 8: Gate + commit**

```bash
make lint-py && make typecheck && make lint-md
git add cli/brief.py cli/main.py Makefile README.md CLAUDE.md tests/test_cli_brief.py
git commit -m "feat(cli): buibui brief subcommand + make buibui-brief + docs"
```

---

### Task 11: API — models, router, wiring

**Files:**

- Create: `web/api/models/brief.py`
- Create: `web/api/routers/brief.py`
- Modify: `web/api/main.py`
- Test: `tests/test_web_brief.py`

**Interfaces:**

- Consumes: `compute_brief`, `BriefConfig`, `default_symbols`, `bundle_to_dict`,
  `parse_as_of_ms`; `web.api.deps.get_db` / `require_token` (existing).
- Produces: `GET /api/brief?symbols=CSV&days=N&as_of=ISO` returning
  `BriefResponse` (mirror of `bundle_to_dict`). Never cached.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_brief.py
"""Brief router: happy path, param validation, dependency overrides."""

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._brief_fixtures import START_MS, make_conn, seed_symbol

from web.api.deps import get_db, require_token
from web.api.routers import brief as brief_router

AS_OF_ISO = "2024-03-01T00:00:00Z"


def _client(conn: duckdb.DuckDBPyConnection) -> TestClient:
    app = FastAPI()
    app.include_router(brief_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: conn
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_get_brief_happy_path() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT", START_MS, 60)
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "BTCUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["panels"][0]["symbol"] == "BTCUSDT"
    assert body["panels"][0]["error"] is None
    assert body["pundit"]["priors_status"] in ("ok", "absent", "unreadable")
    assert isinstance(body["health"]["data_ok"], bool)


def test_get_brief_invalid_as_of_400() -> None:
    conn = make_conn()
    client = _client(conn)
    res = client.get("/api/brief", params={"as_of": "not-a-date"})
    assert res.status_code == 400


def test_get_brief_error_panel_embedded() -> None:
    conn = make_conn()
    client = _client(conn)
    res = client.get(
        "/api/brief",
        params={"symbols": "NODATAUSDT", "days": 60, "as_of": AS_OF_ISO},
    )
    assert res.status_code == 200
    assert res.json()["panels"][0]["error"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_web_brief.py -v`
Expected: FAIL with `ImportError` (no `web.api.routers.brief`)

- [ ] **Step 3: Write the models**

```python
# web/api/models/brief.py
"""Pydantic models for the brief router (mirror of analytics.brief.types)."""

from pydantic import BaseModel


class LevelRowModel(BaseModel):
    name: str
    price: float
    dist_atr: float
    swept: bool


class ZoneRowModel(BaseModel):
    tf: str
    zone_type: str
    direction: str
    zone_low: float
    zone_high: float
    dist_atr: float
    inside: bool


class SeasonalityStripModel(BaseModel):
    dow: str
    bull_pct: float | None
    avg_range_pct: float | None
    sample_days: int | None
    high_session: str | None
    high_session_pct: float | None
    low_session: str | None
    low_session_pct: float | None
    weekly_low_still_ahead: float | None
    weekly_high_still_ahead: float | None
    typical_low_day: str | None
    typical_high_day: str | None


class SymbolPanelModel(BaseModel):
    symbol: str
    ref_close: float
    ref_close_ts_ms: int
    atr14: float
    adr_pct: float | None
    regime_1d: str
    regime_4h: str
    levels_above: list[LevelRowModel]
    levels_below: list[LevelRowModel]
    zones_above: list[ZoneRowModel]
    zones_below: list[ZoneRowModel]
    seasonality: SeasonalityStripModel | None
    error: str | None


class PunditAuthorPriorModel(BaseModel):
    author: str
    n: int
    hit_rate: float | None
    avg_r: float | None
    avg_atr_r: float | None
    flagged: bool


class PunditFamilyPriorModel(BaseModel):
    family: str
    direction: str
    n: int
    hit_rate: float | None
    avg_atr_r: float | None
    flagged: bool


class PunditCallRowModel(BaseModel):
    author: str
    symbol: str
    direction: str
    entry: str
    target: str
    horizon: str
    age_days: int
    on_panel: bool
    prior: PunditAuthorPriorModel | None


class PunditBoardModel(BaseModel):
    priors_status: str
    priors_age_days: int | None
    min_n_marker: int | None
    ledger_status: str
    ledger_total: int
    ledger_skipped: int
    recent_calls: list[PunditCallRowModel]
    authors: list[PunditAuthorPriorModel]
    families: list[PunditFamilyPriorModel]


class HealthRowModel(BaseModel):
    symbol: str
    tf: str
    status: str
    bars_behind: int


class HealthReportModel(BaseModel):
    rows: list[HealthRowModel]
    notes: list[str]
    data_ok: bool


class BriefResponse(BaseModel):
    as_of_ms: int
    day_ahead: str
    panels: list[SymbolPanelModel]
    pundit: PunditBoardModel
    health: HealthReportModel
```

- [ ] **Step 4: Write the router**

```python
# web/api/routers/brief.py
"""Brief router — GET /api/brief (computed fresh per request, never cached)."""

import time

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from analytics.brief._common import parse_as_of_ms
from analytics.brief.bundle import compute_brief
from analytics.brief.config import BriefConfig, default_symbols
from analytics.brief.types import bundle_to_dict
from web.api.deps import get_db, require_token
from web.api.models.brief import BriefResponse

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/brief", response_model=BriefResponse)
def get_brief(
    symbols: str | None = Query(default=None, description="CSV symbol list"),
    days: int = Query(default=180, ge=30, le=365),
    as_of: str | None = Query(default=None, description="ISO8601 anchor"),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> BriefResponse:
    """Compute the daily market brief. One failing symbol yields an error stub
    inside the 200 response; 4xx is reserved for invalid params."""
    if as_of is not None:
        try:
            as_of_ms = parse_as_of_ms(as_of)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid as_of (want ISO8601)"
            ) from None
    else:
        as_of_ms = int(time.time() * 1000)
    notes: list[str] = []
    if symbols:
        symbol_tuple = tuple(s.strip().upper() for s in symbols.split(",") if s.strip())
    else:
        symbol_tuple, notes = default_symbols()
    if not symbol_tuple:
        raise HTTPException(status_code=400, detail="No symbols")
    cfg = BriefConfig(symbols=symbol_tuple, as_of_ms=as_of_ms, stats_days=days)
    bundle = compute_brief(db, cfg, extra_notes=notes)
    return BriefResponse(**bundle_to_dict(bundle))
```

- [ ] **Step 5: Wire into `web/api/main.py`**

In the `from web.api.routers import (...)` block, add `brief,` after
`backtest,` (alphabetical). In the `for module in (...)` include-loop tuple near
`app.include_router(module.router, prefix="/api")`, add `brief,` (anywhere in
the tuple; keep style).

- [ ] **Step 6: Run tests**

Run: `poetry run pytest tests/test_web_brief.py -v`
Expected: 3 PASS

- [ ] **Step 7: Gate + commit**

```bash
make lint-py && make typecheck
git add web/api/models/brief.py web/api/routers/brief.py web/api/main.py tests/test_web_brief.py
git commit -m "feat(api): GET /api/brief router + pydantic models"
```

---

### Task 12: Web UI — Brief page

> **REQUIRED SUB-SKILLS for this task:** load `/frontend-design` and
> `/frontend-svelte` BEFORE writing any Svelte/CSS (CLAUDE.md rule). Follow the
> existing dark-minimal style and CSS variables (`--bg-panel`, `--border`,
> `--muted`, `--accent`, `--text`); timestamps display in MYT.

**Files:**

- Modify: `web/ui/src/api.ts` (types + `getBrief` helper)
- Create: `web/ui/src/pages/Brief.svelte`
- Modify: `web/ui/src/App.svelte` (import + route)
- Modify: `web/ui/src/components/Nav.svelte` (nav link)

**Interfaces:**

- Consumes: `GET /api/brief` (Task 11) via the existing `apiFetch` helper.
- Produces: hash route `#/brief` rendering the bundle.

- [ ] **Step 1: Add types + helper to `web/ui/src/api.ts`** (append near the other
  named helpers, before the SSE section):

```typescript
// ── Daily Brief ───────────────────────────────────────────────────────────────

export interface BriefLevelRow {
  name: string;
  price: number;
  dist_atr: number;
  swept: boolean;
}

export interface BriefZoneRow {
  tf: string;
  zone_type: string;
  direction: string;
  zone_low: number;
  zone_high: number;
  dist_atr: number;
  inside: boolean;
}

export interface BriefSeasonalityStrip {
  dow: string;
  bull_pct: number | null;
  avg_range_pct: number | null;
  sample_days: number | null;
  high_session: string | null;
  high_session_pct: number | null;
  low_session: string | null;
  low_session_pct: number | null;
  weekly_low_still_ahead: number | null;
  weekly_high_still_ahead: number | null;
  typical_low_day: string | null;
  typical_high_day: string | null;
}

export interface BriefSymbolPanel {
  symbol: string;
  ref_close: number;
  ref_close_ts_ms: number;
  atr14: number;
  adr_pct: number | null;
  regime_1d: string;
  regime_4h: string;
  levels_above: BriefLevelRow[];
  levels_below: BriefLevelRow[];
  zones_above: BriefZoneRow[];
  zones_below: BriefZoneRow[];
  seasonality: BriefSeasonalityStrip | null;
  error: string | null;
}

export interface BriefAuthorPrior {
  author: string;
  n: number;
  hit_rate: number | null;
  avg_r: number | null;
  avg_atr_r: number | null;
  flagged: boolean;
}

export interface BriefFamilyPrior {
  family: string;
  direction: string;
  n: number;
  hit_rate: number | null;
  avg_atr_r: number | null;
  flagged: boolean;
}

export interface BriefCallRow {
  author: string;
  symbol: string;
  direction: string;
  entry: string;
  target: string;
  horizon: string;
  age_days: number;
  on_panel: boolean;
  prior: BriefAuthorPrior | null;
}

export interface BriefPunditBoard {
  priors_status: string;
  priors_age_days: number | null;
  min_n_marker: number | null;
  ledger_status: string;
  ledger_total: number;
  ledger_skipped: number;
  recent_calls: BriefCallRow[];
  authors: BriefAuthorPrior[];
  families: BriefFamilyPrior[];
}

export interface BriefHealthRow {
  symbol: string;
  tf: string;
  status: string;
  bars_behind: number;
}

export interface BriefHealthReport {
  rows: BriefHealthRow[];
  notes: string[];
  data_ok: boolean;
}

export interface BriefResponse {
  as_of_ms: number;
  day_ahead: string;
  panels: BriefSymbolPanel[];
  pundit: BriefPunditBoard;
  health: BriefHealthReport;
}

export const getBrief = (params?: {
  symbols?: string[];
  days?: number;
  as_of?: string;
}) => {
  const q = new URLSearchParams({
    ...(params?.symbols?.length ? { symbols: params.symbols.join(",") } : {}),
    ...(params?.days ? { days: String(params.days) } : {}),
    ...(params?.as_of ? { as_of: params.as_of } : {}),
  });
  const qs = q.toString();
  return apiFetch<BriefResponse>(qs ? `/api/brief?${qs}` : "/api/brief");
};
```

- [ ] **Step 2: Create `web/ui/src/pages/Brief.svelte`**

Follow `/frontend-design` + `/frontend-svelte` conventions. Reference layout
(adapt styling to the loaded skills, keep the structure):

```svelte
<script lang="ts">
  import { onMount } from "svelte";
  import { getBrief, type BriefResponse } from "../api";

  let data = $state<BriefResponse | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);

  const fmtPrice = (v: number): string =>
    v >= 1000
      ? v.toLocaleString("en-US", { maximumFractionDigits: 0 })
      : v >= 1
        ? v.toFixed(2)
        : v.toFixed(4);
  const fmtDist = (v: number): string => (v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2));
  const fmtPct = (v: number): string => `${Math.round(v * 100)}%`;
  const asOfMyt = (ms: number): string =>
    new Date(ms).toLocaleString("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      dateStyle: "medium",
      timeStyle: "short",
    });

  async function load(): Promise<void> {
    loading = true;
    error = null;
    try {
      data = await getBrief();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<main>
  {#if loading}
    <p class="muted">computing brief…</p>
  {:else if error}
    <p class="error">{error}</p>
  {:else if data}
    <header class="brief-head">
      <h1>DAILY BRIEF — {data.day_ahead}</h1>
      <span class="muted">as-of {asOfMyt(data.as_of_ms)} MYT</span>
      <span class={data.health.data_ok ? "ok" : "warn"}>
        {data.health.data_ok ? "data OK" : "data ⚠"}
      </span>
      <button onclick={() => void load()}>refresh</button>
    </header>

    {#each data.panels as panel (panel.symbol)}
      <section class="panel">
        <h2>{panel.symbol}</h2>
        {#if panel.error}
          <p class="error">ERROR: {panel.error}</p>
        {:else}
          <p>
            Close {fmtPrice(panel.ref_close)} · 1d {panel.regime_1d} / 4h
            {panel.regime_4h} · ATR14 {fmtPrice(panel.atr14)}
            {#if panel.adr_pct !== null}· ADR {fmtPct(panel.adr_pct)}{/if}
          </p>
          <div class="ladder">
            {#each [...panel.levels_above].reverse() as lvl (lvl.name)}
              <div class="lvl above">
                <span>{lvl.name}</span>
                <span>{fmtPrice(lvl.price)}</span>
                <span>{fmtDist(lvl.dist_atr)}{lvl.swept ? " swept✓" : ""}</span>
              </div>
            {/each}
            <div class="lvl close-row">
              <span>CLOSE</span><span>{fmtPrice(panel.ref_close)}</span><span></span>
            </div>
            {#each panel.levels_below as lvl (lvl.name)}
              <div class="lvl below">
                <span>{lvl.name}</span>
                <span>{fmtPrice(lvl.price)}</span>
                <span>{fmtDist(lvl.dist_atr)}{lvl.swept ? " swept✓" : ""}</span>
              </div>
            {/each}
          </div>
          <p class="zones">
            {#each [...panel.zones_above, ...panel.zones_below] as z}
              <span class="zone">
                {z.tf}
                {z.zone_type.toUpperCase()}·{z.direction}
                {z.zone_low === z.zone_high
                  ? fmtPrice(z.zone_low)
                  : `${fmtPrice(z.zone_low)}–${fmtPrice(z.zone_high)}`}
                ({z.inside ? "inside" : fmtDist(z.dist_atr)})
              </span>
            {:else}
              <span class="muted">no active zones</span>
            {/each}
          </p>
          {#if panel.seasonality}
            <p class="muted">
              {panel.seasonality.dow}:
              {#if panel.seasonality.bull_pct !== null}
                bull {fmtPct(panel.seasonality.bull_pct)}
              {/if}
              {#if panel.seasonality.avg_range_pct !== null}
                · range {(panel.seasonality.avg_range_pct * 100).toFixed(1)}%
                (n={panel.seasonality.sample_days})
              {/if}
              {#if panel.seasonality.high_session}
                · high {panel.seasonality.high_session} / low
                {panel.seasonality.low_session}
              {/if}
              {#if panel.seasonality.typical_low_day}
                · week: typical low {panel.seasonality.typical_low_day} / high
                {panel.seasonality.typical_high_day}
              {/if}
            </p>
          {/if}
        {/if}
      </section>
    {/each}

    <section class="panel">
      <h2>PUNDIT BOARD</h2>
      {#if data.pundit.priors_status !== "ok"}
        <p class="muted">
          priors {data.pundit.priors_status} — run make buibui-pundit-score
        </p>
      {/if}
      {#each data.pundit.recent_calls as call}
        <p>
          {call.on_panel ? "●" : "·"}
          <strong>{call.author}</strong>
          {#if call.prior}
            {call.prior.flagged
              ? `(⚠ n=${call.prior.n})`
              : `(n=${call.prior.n}${call.prior.hit_rate !== null ? ` · ${fmtPct(call.prior.hit_rate)}` : ""})`}
          {/if}
          {call.symbol}
          {call.direction} — "{call.entry}"
          {#if call.target}→ "{call.target}"{/if}
          · {call.age_days}d
        </p>
      {:else}
        <p class="muted">no recent calls</p>
      {/each}
    </section>

    <section class="panel health">
      <h2>HEALTH</h2>
      <p>
        {#each data.health.rows as row}
          <span class={row.status === "ok" ? "ok" : "warn"}>
            {row.symbol}/{row.tf}
            {row.status === "ok" ? "✓" : row.status}
          </span>
        {/each}
      </p>
      {#each data.health.notes as note}
        <p class="muted">note: {note}</p>
      {/each}
    </section>
  {/if}
</main>

<style>
  main {
    padding: 20px;
    max-width: 980px;
    margin: 0 auto;
    display: grid;
    gap: 16px;
  }
  .brief-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  h1 {
    font-size: 14px;
    letter-spacing: 0.1em;
  }
  .panel {
    background: var(--bg-panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 14px 16px;
    font-size: 12px;
  }
  h2 {
    font-size: 12px;
    letter-spacing: 0.12em;
    color: var(--accent);
    margin-bottom: 8px;
  }
  .ladder {
    display: grid;
    gap: 2px;
    margin: 8px 0;
    font-variant-numeric: tabular-nums;
  }
  .lvl {
    display: grid;
    grid-template-columns: 60px 110px 1fr;
  }
  .lvl.above span:last-child { color: var(--muted); }
  .lvl.below span:last-child { color: var(--muted); }
  .close-row {
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    color: var(--accent);
  }
  .zone { margin-right: 12px; }
  .muted { color: var(--muted); }
  .ok { color: var(--accent); margin-right: 10px; }
  .warn { color: #e0a030; margin-right: 10px; }
  .error { color: #e05050; }
</style>
```

- [ ] **Step 3: Route + nav**

- `web/ui/src/App.svelte`: add `import Brief from "./pages/Brief.svelte";` next to
  the other page imports, and a route branch before the final `{:else}`:

```svelte
{:else if route === "#/brief"}
  <Brief />
```

- `web/ui/src/components/Nav.svelte`: in the `links` array add
  `{ href: "#/brief", label: "Brief" },` after the Stats entry.

- [ ] **Step 4: Build gate**

Run: `make web-build`
Expected: vite build succeeds with no TS errors.

- [ ] **Step 5: Visual check + commit**

Start `make web-dev`, open `http://localhost:5173/#/brief` (or the printed port),
take a Playwright/manual screenshot for the PR, then:

```bash
git add web/ui/src/api.ts web/ui/src/pages/Brief.svelte web/ui/src/App.svelte web/ui/src/components/Nav.svelte
git commit -m "feat(ui): Brief page — daily market brief panels + pundit board"
```

---

### Task 13: Finalize — full DoD, real-run eyeball, PR update

**Files:**

- Modify: none expected (fixes only if gates fail)

- [ ] **Step 1: Full gates (state each result plainly)**

```bash
make lint-py
make typecheck
make test
make test-regression
make lint-md
```

Expected: all green; regression goldens UNMOVED (this feature is read-only).

- [ ] **Step 2: Real-run eyeball**

```bash
make buibui-brief
```

Expected: a real brief over the local `analytics.db` — check levels look sane vs
a chart, the pundit board reads the real ledger (priors line says "not found"
until the scorer has run — that is correct), and HEALTH flags any genuinely
stale symbol. Paste the output into the PR description.

- [ ] **Step 3: Push + PR**

The docs PR for this branch already carries the spec + this plan. Push the
implementation commits to the same branch/PR (`feat/market-brief`), update the
PR body with a summary + test plan + the real-run output + UI screenshot, and
run the `/post-branch` docs sweep. Do NOT merge without review.

---

## Self-review notes (already applied)

- eqh_eql/bos zones carry a single `price` key — normalised in `zones.py`
  (`_zone_bounds`), covered by `test_build_zone_rows_normalises_and_signs`.
- All stats scales are FRACTIONS (0–1): `bull_pct`, session `high_pct`/`low_pct`,
  `*_still_ahead_by_dow` — `fmt_frac` multiplies by 100 exactly once.
- `_start_ms` wall-clock dependency neutralised via Task 2 `end_ms`; the brief
  never calls a stats compute without `end_ms=as_of_ms`.
- `parse_as_of_ms` lives in `analytics/brief/_common.py` so the router never
  imports from `cli/` (layering rule).
- `tests/_brief_fixtures.py` is intentionally NOT named `test_*` (helpers only);
  it seeds via the store's own `upsert_ohlcv` so schema drift cannot bite.
