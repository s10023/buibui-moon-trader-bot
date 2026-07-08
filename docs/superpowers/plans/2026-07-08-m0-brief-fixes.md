# M0 Brief Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the false "swept" flag on Brief reference levels, replace the up-to-24h-stale reference price with the freshest 1h close (with fallback chain), and add a legend card to the Brief UI.

**Architecture:** All logic changes are confined to the pure `analytics/brief/` package (spec: `docs/superpowers/specs/2026-07-08-m0-brief-fixes-design.md`). Sweeps move from defining-era completed daily bars to *current-period* windows sliced from the already-fetched daily frame (incl. the forming bar). The reference price gains a resolver with a 3-step fallback chain and an additive `ref_price_source` field that flows dataclass → `bundle_to_dict` → pydantic model → TS interface. The legend is a static UI card.

**Tech Stack:** Python 3.11+ / pandas / duckdb (in-memory for tests) / pytest; Svelte 5 + Vite for the UI.

## Global Constraints

- Branch: work on `docs/m0-brief-fixes` (spec + plan already committed there); one PR at the end carries docs + code.
- mypy strict — every function fully annotated, including `-> None` on tests.
- `analytics/reference_levels.py::sweep_flag` must NOT be modified (the proximity audit depends on its per-entry-bar semantics).
- No DB schema, config, or regression-golden change. Brief tests use `duckdb.connect(":memory:")` via `tests/_brief_fixtures.py` — never the real `analytics.db`.
- All times UTC in lib code; MYT only in UI display strings.
- Commits: conventional commits, each ending with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` (shown once here; add it to every commit in this plan).
- After any Python change: `make lint-py && make typecheck` must pass before commit.
- The pre-commit markdownlint hook fails on the pre-existing untracked `audit.md` (repo root, not ours). If it blocks a commit, move it aside and restore after: `mv audit.md /tmp/audit.md.parked` … commit … `mv /tmp/audit.md.parked audit.md`. Do NOT delete it, do NOT use `--no-verify`.

---

### Task 1: Forming-bar sweep windows in `analytics/brief/levels.py`

**Files:**

- Modify: `analytics/brief/levels.py`
- Modify: `analytics/brief/bundle.py:53-55` (call site)
- Test: `tests/test_brief_levels.py`

**Interfaces:**

- Consumes: `analytics.reference_levels.compute_levels(daily_df, as_of_ms)` (unchanged); `LevelRow` from `analytics.brief.types` (unchanged).
- Produces: `build_level_rows(daily_df: pd.DataFrame, as_of_ms: int, ref_price: float, atr14: float, max_per_side: int) -> tuple[list[LevelRow], list[LevelRow]]` — NOTE: the `completed_1d` parameter is REMOVED and `ref_close` is renamed `ref_price`. Also produces module-privates `_swept_current_period` and `_window_start_ms` (unit-tested directly).

- [ ] **Step 1: Write the failing tests**

Replace the two existing `build_level_rows` tests' call signatures and add the sweep suite. In `tests/test_brief_levels.py`, replace the whole file content below the imports as follows (keep `_daily` and the three ATR/ADR tests unchanged):

```python
"""Level gauge: ATR, ADR, distance/side split, sweep flags."""

import pandas as pd

from analytics.brief._common import DAY_MS
from analytics.brief.levels import (
    _swept_current_period,
    _window_start_ms,
    adr_pct_14,
    atr14_wilder,
    build_level_rows,
)

AS_OF = 1_704_067_200_000 + 30 * DAY_MS  # 2024-01-31 00:00 UTC (Wednesday)
AS_OF_MID = AS_OF + 12 * 3_600_000  # 2024-01-31 12:00 UTC
MON_OPEN = AS_OF - 2 * DAY_MS  # Monday 2024-01-29 00:00 UTC
TUE_OPEN = AS_OF - 1 * DAY_MS
```

(`_daily`, `test_atr14_wilder_flat_series`, `test_atr14_insufficient_rows`, `test_adr_pct_14` stay byte-identical.)

Update the two existing `build_level_rows` tests to the new signature:

```python
def test_build_level_rows_sides_and_cap() -> None:
    df = _daily(30)
    above, below = build_level_rows(
        daily_df=df,
        as_of_ms=AS_OF,
        ref_price=101.0,
        atr14=4.0,
        max_per_side=4,
    )
    assert len(above) <= 4 and len(below) <= 4
    assert [r.dist_atr for r in above] == sorted(r.dist_atr for r in above)
    dists_below = [r.dist_atr for r in below]
    assert dists_below == sorted(dists_below, reverse=True)
    assert any(r.name == "PDH" for r in above)
    for r in above:
        assert r.dist_atr > 0
    for r in below:
        assert r.dist_atr <= 0


def test_build_level_rows_zero_atr_returns_empty() -> None:
    df = _daily(30)
    above, below = build_level_rows(df, AS_OF, 101.0, 0.0, 4)
    assert above == [] and below == []
```

Append the new sweep tests:

```python
def _with_bar(
    df: pd.DataFrame, open_time: int, high: float, low: float, close: float
) -> pd.DataFrame:
    row = {
        "open_time": open_time,
        "open": float(df.iloc[-1]["close"]),
        "high": high,
        "low": low,
        "close": close,
    }
    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def _downtrend_frame() -> pd.DataFrame:
    """27 flat bars then 3 completed lower-high days: 110, 108, 106 (=PDH)."""
    df = _daily(30)
    df.loc[len(df) - 3, "high"] = 110.0
    df.loc[len(df) - 2, "high"] = 108.0
    df.loc[len(df) - 1, "high"] = 106.0
    return df


def test_window_start_ms() -> None:
    assert _window_start_ms(AS_OF_MID, "day") == AS_OF
    assert _window_start_ms(AS_OF_MID, "week") == MON_OPEN
    assert _window_start_ms(AS_OF_MID, "week_after_monday") == TUE_OPEN


def test_pdh_not_swept_in_lower_high_downtrend() -> None:
    """Regression: the shipped bug flagged PDH swept on any 3-day lower-high run."""
    df = _with_bar(_downtrend_frame(), AS_OF, high=105.0, low=100.0, close=104.0)
    assert _swept_current_period(df, AS_OF_MID, "PDH", 106.0, 104.0) is False


def test_pdh_swept_pierce_and_reclaim_today() -> None:
    df = _with_bar(_downtrend_frame(), AS_OF, high=107.0, low=100.0, close=105.0)
    assert _swept_current_period(df, AS_OF_MID, "PDH", 106.0, 105.0) is True


def test_pdh_breakout_in_progress_not_swept() -> None:
    df = _with_bar(_downtrend_frame(), AS_OF, high=107.0, low=100.0, close=106.5)
    assert _swept_current_period(df, AS_OF_MID, "PDH", 106.0, 106.5) is False


def test_pdl_mirror_long_sweep() -> None:
    df = _with_bar(_daily(30), AS_OF, high=100.0, low=97.0, close=99.0)
    assert _swept_current_period(df, AS_OF_MID, "PDL", 98.0, 99.0) is True
    assert _swept_current_period(df, AS_OF_MID, "PDL", 96.0, 99.0) is False


def _week_frame(
    mon_high: float, tue_high: float, wed_high: float, wed_close: float
) -> pd.DataFrame:
    """History ending Sunday + explicit Mon/Tue/forming-Wed bars of this week.

    ``_daily(n)`` always ends at AS_OF - 1d, so truncate two rows to end on
    Sunday before appending this week's bars (no duplicate open_times).
    """
    df = _daily(30).iloc[:-2].reset_index(drop=True)  # ends Sunday (AS_OF - 3d)
    df = _with_bar(df, MON_OPEN, high=mon_high, low=99.0, close=110.0)
    df = _with_bar(df, TUE_OPEN, high=tue_high, low=105.0, close=112.0)
    return _with_bar(df, AS_OF, high=wed_high, low=110.0, close=wed_close)


def test_monh_excludes_defining_monday_bar() -> None:
    # Monday high 120 defines MonH; nothing after Monday pierces 120.
    df = _week_frame(mon_high=120.0, tue_high=118.0, wed_high=119.0, wed_close=117.0)
    assert _swept_current_period(df, AS_OF_MID, "MonH", 120.0, 117.0) is False


def test_monh_swept_by_post_monday_pierce() -> None:
    df = _week_frame(mon_high=120.0, tue_high=121.0, wed_high=118.0, wed_close=117.0)
    assert _swept_current_period(df, AS_OF_MID, "MonH", 120.0, 117.0) is True


def test_pwh_window_is_this_week_only() -> None:
    # PWH := 115 (defined last week); this week's max high 114 → no sweep...
    no_pierce = _week_frame(114.0, 113.0, 114.0, 113.0)
    assert _swept_current_period(no_pierce, AS_OF_MID, "PWH", 115.0, 113.0) is False
    # ...but a forming-Wednesday pierce to 116 with price back at 113 sweeps.
    pierce = _week_frame(114.0, 113.0, 116.0, 113.0)
    assert _swept_current_period(pierce, AS_OF_MID, "PWH", 115.0, 113.0) is True


def test_sweep_unknown_level_or_empty_window() -> None:
    df = _daily(30)  # no bars at/after Wednesday 00:00 → empty "day" window
    assert _swept_current_period(df, AS_OF_MID, "PDH", 102.0, 101.0) is False
    assert _swept_current_period(df, AS_OF_MID, "DO", 100.0, 101.0) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_levels.py -v`
Expected: FAIL — `ImportError: cannot import name '_swept_current_period'`.

- [ ] **Step 3: Implement in `analytics/brief/levels.py`**

Replace the import of `sweep_flag` and the sweep logic. Final relevant sections:

```python
from analytics.brief.types import LevelRow
from analytics.reference_levels import compute_levels

# Extreme levels get sweep flags; sweep of a LOW is a long-side reclaim,
# sweep of a HIGH is a short-side rejection.
_SWEEP_DIRECTION: dict[str, str] = {
    "PDH": "short",
    "PWH": "short",
    "MonH": "short",
    "PDL": "long",
    "PWL": "long",
    "MonL": "long",
}

# Sweep-check window per level: the period the level is ACTIVE as a
# reference, never the period that defines it (a bar cannot sweep the
# level it defines — its own extreme IS the level).
_SWEEP_WINDOW: dict[str, str] = {
    "PDH": "day",
    "PDL": "day",
    "PWH": "week",
    "PWL": "week",
    "MonH": "week_after_monday",
    "MonL": "week_after_monday",
}


def _window_start_ms(as_of_ms: int, kind: str) -> int:
    """UTC-ms start of the sweep window containing ``as_of_ms``."""
    ts = pd.Timestamp(as_of_ms, unit="ms", tz="UTC")
    day_start = ts.normalize()
    if kind == "day":
        start = day_start
    else:
        week_start = day_start - pd.Timedelta(days=int(day_start.weekday()))
        start = week_start if kind == "week" else week_start + pd.Timedelta(days=1)
    return int(start.value // 1_000_000)


def _swept_current_period(
    daily_df: pd.DataFrame,
    as_of_ms: int,
    name: str,
    level_price: float,
    as_of_price: float,
) -> bool:
    """Did current-period bars pierce ``level_price`` with price now back?

    Windows are sliced from ``daily_df`` (incl. the forming bar). Strict
    inequalities: touching a level is not piercing it.
    """
    direction = _SWEEP_DIRECTION.get(name)
    kind = _SWEEP_WINDOW.get(name)
    if direction is None or kind is None or daily_df.empty:
        return False
    start_ms = _window_start_ms(as_of_ms, kind)
    window = daily_df[
        (daily_df["open_time"] >= start_ms) & (daily_df["open_time"] <= as_of_ms)
    ]
    if window.empty:
        return False
    if direction == "short":
        pierced = bool((window["high"].astype(float) > level_price).any())
        return pierced and as_of_price < level_price
    pierced = bool((window["low"].astype(float) < level_price).any())
    return pierced and as_of_price > level_price
```

And `build_level_rows` becomes:

```python
def build_level_rows(
    daily_df: pd.DataFrame,
    as_of_ms: int,
    ref_price: float,
    atr14: float,
    max_per_side: int,
) -> tuple[list[LevelRow], list[LevelRow]]:
    """(above, below) LevelRows — nearest-first, capped per side.

    ``daily_df`` must include the (possibly forming) bar containing as_of so
    the current-period opens (DO/WO/MO) resolve AND so sweep flags can see
    today's/this week's action. ``dist_atr <= 0`` lands on the below side.
    """
    if atr14 <= 0:
        return [], []
    levels = compute_levels(daily_df, as_of_ms)
    rows: list[LevelRow] = []
    for name, price in levels.items():
        if price is None:
            continue
        dist = (float(price) - ref_price) / atr14
        swept = _swept_current_period(
            daily_df, as_of_ms, name, float(price), ref_price
        )
        rows.append(LevelRow(name=name, price=float(price), dist_atr=dist, swept=swept))
    above = sorted((r for r in rows if r.dist_atr > 0), key=lambda r: r.dist_atr)
    below = sorted((r for r in rows if r.dist_atr <= 0), key=lambda r: -r.dist_atr)
    return above[:max_per_side], below[:max_per_side]
```

Update the call site in `analytics/brief/bundle.py` (currently lines 53–55):

```python
    levels_above, levels_below = build_level_rows(
        daily, as_of, ref_close, atr, cfg.max_levels_per_side
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_levels.py tests/test_brief_bundle.py tests/test_brief_render.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add analytics/brief/levels.py analytics/brief/bundle.py tests/test_brief_levels.py
git commit -m "fix(brief): sweep flags use forming-bar current-period windows"
```

---

### Task 2: As-of reference price resolver + `ref_price_source` field

**Files:**

- Modify: `analytics/brief/bundle.py`
- Modify: `analytics/brief/types.py:44-77` (`SymbolPanel`, `error_panel`)
- Modify: `web/api/models/brief.py` (`SymbolPanelModel`)
- Test: `tests/test_brief_bundle.py`

**Interfaces:**

- Consumes: `completed_bars(df, timeframe, as_of_ms)` and `TF_MS` from `analytics.brief._common`; `build_level_rows` from Task 1.
- Produces: `_resolve_ref_price(completed_1h: pd.DataFrame, daily: pd.DataFrame, completed_1d: pd.DataFrame, as_of_ms: int) -> tuple[float, int, str]` in `analytics/brief/bundle.py` (price, bar open_time ms, source ∈ {"1h", "1d_forming", "1d_close"}); `SymbolPanel.ref_price_source: str`; `_compute_panel(conn, symbol, cfg, notes)` — new `notes: list[str]` accumulator parameter.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brief_bundle.py` (it already imports `compute_brief`, `BriefConfig`, fixtures; add the new imports shown):

```python
import pandas as pd

from analytics.brief._common import TF_MS
from analytics.brief.bundle import _resolve_ref_price

H1_MS = TF_MS["1h"]


def _h1_frame(last_open_ms: int, n: int = 5, close: float = 101.0) -> pd.DataFrame:
    rows = [
        {
            "open_time": last_open_ms - i * H1_MS,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": close,
        }
        for i in reversed(range(n))
    ]
    return pd.DataFrame(rows)


def _d1_frame(as_of_ms: int, forming: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(daily, completed_1d): 3 completed days, optional forming bar at as_of."""
    day = 86_400_000
    day_start = (as_of_ms // day) * day
    rows = [
        {
            "open_time": day_start - i * day,
            "open": 100.0,
            "high": 103.0,
            "low": 97.0,
            "close": 100.5,
        }
        for i in reversed(range(1, 4))
    ]
    completed = pd.DataFrame(rows)
    if not forming:
        return completed.copy(), completed
    f = {"open_time": day_start, "open": 100.5, "high": 104.0, "low": 100.0, "close": 103.5}
    return pd.concat([completed, pd.DataFrame([f])], ignore_index=True), completed


def test_resolve_ref_price_fresh_1h() -> None:
    as_of = AS_OF + 12 * H1_MS
    h1 = _h1_frame(last_open_ms=as_of - H1_MS)  # closes exactly at as_of
    daily, completed = _d1_frame(as_of, forming=True)
    price, ts, source = _resolve_ref_price(h1, daily, completed, as_of)
    assert source == "1h"
    assert price == 101.0
    assert ts == as_of - H1_MS


def test_resolve_ref_price_stale_1h_falls_to_forming() -> None:
    as_of = AS_OF + 12 * H1_MS
    h1 = _h1_frame(last_open_ms=as_of - 4 * H1_MS)  # closed 3h ago → stale
    daily, completed = _d1_frame(as_of, forming=True)
    price, ts, source = _resolve_ref_price(h1, daily, completed, as_of)
    assert source == "1d_forming"
    assert price == 103.5


def test_resolve_ref_price_no_1h_no_forming() -> None:
    as_of = AS_OF + 12 * H1_MS
    daily, completed = _d1_frame(as_of, forming=False)
    price, ts, source = _resolve_ref_price(pd.DataFrame(), daily, completed, as_of)
    assert source == "1d_close"
    assert price == 100.5


def test_bundle_uses_1h_ref_price_and_flags_source() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT")
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.ref_price_source == "1h"
    assert not any("ref price" in n for n in bundle.health.notes)


def test_bundle_falls_back_without_1h_and_notes_it() -> None:
    conn = make_conn()
    seed_symbol(conn, "BTCUSDT")
    conn.execute("DELETE FROM ohlcv WHERE timeframe = '1h'")
    bundle = compute_brief(conn, _cfg(("BTCUSDT",)))
    panel = bundle.panels[0]
    assert panel.ref_price_source == "1d_close"
    assert any("ref price" in n for n in bundle.health.notes)
```

(Adjust the existing bundle test if it asserts on `ref_close`: with 1h data seeded through `AS_OF`, `ref_close` becomes the last 1h close — the existing assertion `panel.ref_close > 0` still holds.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_brief_bundle.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_ref_price'`.

- [ ] **Step 3: Implement**

`analytics/brief/types.py` — add the field to `SymbolPanel` right after `ref_close_ts_ms` and to `error_panel`:

```python
@dataclass(frozen=True)
class SymbolPanel:
    symbol: str
    ref_close: float
    ref_close_ts_ms: int
    ref_price_source: str  # "1h" | "1d_forming" | "1d_close"
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
```

```python
def error_panel(symbol: str, message: str) -> SymbolPanel:
    """Stub panel for a symbol that failed to compute (renders the error)."""
    return SymbolPanel(
        symbol=symbol,
        ref_close=0.0,
        ref_close_ts_ms=0,
        ref_price_source="1d_close",
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
```

`analytics/brief/bundle.py` — add const, import `TF_MS`, the resolver, and wire it:

```python
from analytics.brief._common import (
    DAY_MS,
    TF_MS,
    BriefDataError,
    completed_bars,
    day_ahead_label,
)

_H1_FETCH_DAYS = 3
_REF_1H_MAX_LAG_MS = 2 * TF_MS["1h"]


def _resolve_ref_price(
    completed_1h: pd.DataFrame,
    daily: pd.DataFrame,
    completed_1d: pd.DataFrame,
    as_of_ms: int,
) -> tuple[float, int, str]:
    """(price, bar_open_ms, source) — freshest available reference price.

    Chain: last completed 1h close if its close is <= 2h behind as_of;
    else the forming 1d bar's close (latest synced price); else the last
    completed 1d close (the pre-M0 behavior).
    """
    if not completed_1h.empty:
        bar = completed_1h.iloc[-1]
        close_ms = int(bar["open_time"]) + TF_MS["1h"]
        if as_of_ms - close_ms <= _REF_1H_MAX_LAG_MS:
            return float(bar["close"]), int(bar["open_time"]), "1h"
    if len(daily) > len(completed_1d):
        bar = daily.iloc[-1]
        return float(bar["close"]), int(bar["open_time"]), "1d_forming"
    bar = completed_1d.iloc[-1]
    return float(bar["close"]), int(bar["open_time"]), "1d_close"
```

In `_compute_panel`, replace the `ref_bar`/`ref_close`/`ref_ts` block and add the accumulator parameter — full function after the change:

```python
def _compute_panel(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    cfg: BriefConfig,
    notes: list[str],
) -> SymbolPanel:
    as_of = cfg.as_of_ms
    daily = get_ohlcv(conn, symbol, "1d", as_of - _DAILY_FETCH_DAYS * DAY_MS, as_of)
    completed_1d = completed_bars(daily, "1d", as_of)
    if len(completed_1d) < _MIN_DAILY_BARS:
        raise BriefDataError(
            f"insufficient 1d history ({len(completed_1d)} completed bars)"
        )
    raw_1h = get_ohlcv(conn, symbol, "1h", as_of - _H1_FETCH_DAYS * DAY_MS, as_of)
    completed_1h = completed_bars(raw_1h, "1h", as_of)
    ref_close, ref_ts, ref_source = _resolve_ref_price(
        completed_1h, daily, completed_1d, as_of
    )
    if ref_source != "1h":
        notes.append(
            f"{symbol}: ref price fell back to {ref_source} (1h missing/stale)"
        )
    atr = atr14_wilder(completed_1d)
    levels_above, levels_below = build_level_rows(
        daily, as_of, ref_close, atr, cfg.max_levels_per_side
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
        ref_price_source=ref_source,
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
```

Note the `ref_close` variable now holds the resolved price, so the
`build_zone_rows` call needs no edit — zone distances pick up the new
basis automatically. In `compute_brief`, thread the accumulator:

```python
    panels: list[SymbolPanel] = []
    panel_notes: list[str] = []
    for symbol in cfg.symbols:
        try:
            panels.append(_compute_panel(conn, symbol, cfg, panel_notes))
        except Exception as exc:  # per-symbol isolation is the contract
            logger.warning("brief: panel failed for %s: %s", symbol, exc)
            panels.append(error_panel(symbol, str(exc)))
    ...
        health=build_health(conn, cfg, [*(extra_notes or []), *panel_notes]),
```

`web/api/models/brief.py` — add to `SymbolPanelModel` after `ref_close_ts_ms`:

```python
    ref_price_source: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_bundle.py tests/test_web_brief.py tests/test_cli_brief.py tests/test_brief_types.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add analytics/brief/bundle.py analytics/brief/types.py web/api/models/brief.py tests/test_brief_bundle.py
git commit -m "feat(brief): 1h as-of reference price with fallback chain + ref_price_source"
```

---

### Task 3: Renderer label — `Last <price> (<source>)`

**Files:**

- Modify: `analytics/brief/render.py:86-95` (`_panel_lines`)
- Test: `tests/test_brief_render.py`

**Interfaces:**

- Consumes: `SymbolPanel.ref_price_source` + `ref_close_ts_ms` from Task 2; `TF_MS` from `analytics.brief._common`.
- Produces: `_ref_price_label(panel: SymbolPanel) -> str` (render-private); panel header line now starts `Last …`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_brief_render.py` (fixtures seed 1h bars whose last bar closes exactly at `AS_OF`, so the source is `1h` and the close time is `00:00`):

```python
def test_render_last_price_label(tmp_path: Path) -> None:
    cfg, conn = _seeded_cfg(tmp_path)
    out = render_markdown(compute_brief(conn, cfg))
    assert "Last " in out
    assert "(1h close 00:00 UTC)" in out
    assert "Close " not in out  # old label gone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_brief_render.py -v`
Expected: FAIL — the output still contains the old `Close` header line and no `Last` line.

- [ ] **Step 3: Implement in `analytics/brief/render.py`**

Add the import and helper:

```python
from analytics.brief._common import TF_MS


def _ref_price_label(panel: SymbolPanel) -> str:
    """Human tag for the reference-price basis, e.g. "1h close 09:00 UTC"."""
    if panel.ref_price_source == "1d_forming":
        return "1d forming"
    tf = "1h" if panel.ref_price_source == "1h" else "1d"
    close_ts = pd.Timestamp(
        panel.ref_close_ts_ms + TF_MS[tf], unit="ms", tz="UTC"
    ).strftime("%H:%M")
    return f"{tf} close {close_ts} UTC"
```

Replace the header line in `_panel_lines`:

```python
    lines.append(
        f"Last {fmt_price(panel.ref_close)} ({_ref_price_label(panel)}) · "
        f"Regime 1d {panel.regime_1d} / "
        f"4h {panel.regime_4h} · ATR14(1d) {fmt_price(panel.atr14)}{adr}"
    )
```

(`SymbolPanel` must be added to the `analytics.brief.types` import list in `render.py` if not already imported.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_brief_render.py tests/test_cli_brief.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, typecheck, commit**

```bash
make lint-py && make typecheck
git add analytics/brief/render.py tests/test_brief_render.py
git commit -m "feat(brief): render Last price with source tag instead of stale Close"
```

---

### Task 4: Svelte — LAST row, source tag, legend card

**Files:**

- Modify: `web/ui/src/api.ts:631-645` (`BriefSymbolPanel`)
- Modify: `web/ui/src/pages/Brief.svelte`

**Interfaces:**

- Consumes: `ref_price_source` field from the API (Task 2).
- Produces: UI only — no downstream consumers.

Note for the executor: this repo's convention is to load the `frontend-design` skill before Svelte/CSS work; visual style is dark-minimal, matching existing cards.

- [ ] **Step 1: Add the TS field**

In `web/ui/src/api.ts`, inside `interface BriefSymbolPanel` after `ref_close_ts_ms: number;`:

```typescript
  ref_price_source: string;
```

- [ ] **Step 2: Update the price row in `Brief.svelte`**

Replace the CLOSE row (currently lines 114–118):

```svelte
              <div class="lvl-row close-row">
                <span class="lvl-name">LAST</span>
                <span class="lvl-price num">{fmtPrice(panel.ref_close)}</span>
                <span class="lvl-dist num muted">
                  {panel.ref_price_source === "1h"
                    ? "1h close"
                    : panel.ref_price_source === "1d_forming"
                      ? "1d forming"
                      : "1d close"}
                </span>
              </div>
```

- [ ] **Step 3: Add the legend toggle + card**

In the `<script>` block add:

```typescript
  let showLegend = $state(false);
```

In the page-header `controls` div, before the Refresh button:

```svelte
      <button class="btn-refresh" onclick={() => (showLegend = !showLegend)}>
        ⓘ legend
      </button>
```

Directly under the `page-header` div (before the error banner), add:

```svelte
  {#if showLegend}
    <div class="card legend-card">
      <div class="card-header"><span class="card-title">Reading this brief</span></div>
      <dl class="legend-list muted">
        <dt>Regime 1D / 4H</dt>
        <dd>
          Classifier output (trend / range / high-vol / unknown) from the same
          regime model that soft-gates live signals.
        </dd>
        <dt>ATR14</dt>
        <dd>
          Daily Wilder ATR in price units — the yardstick: every ± number on
          levels and zones is a distance in daily ATRs from the Last price.
        </dd>
        <dt>Levels</dt>
        <dd>
          PDH/PDL prior day high/low · PWH/PWL prior week · MonH/MonL Monday's
          range (active Tue onward) · DO/WO/MO today's / this week's / this
          month's open.
        </dd>
        <dt>swept</dt>
        <dd>
          Price pierced the level during the current period and now trades back
          on the original side (sweep + reclaim/reject).
        </dd>
        <dt>Zones</dt>
        <dd>
          Structural zones per timeframe — FVG fair-value gap · OB order block ·
          BOS break of structure · EQH/EQL equal highs/lows. Tag shows ATR
          distance, or "inside" when price is within the zone.
        </dd>
        <dt>Last</dt>
        <dd>
          Reference price for all distances: freshest completed 1h close,
          falling back to the forming or last completed daily bar (tagged).
        </dd>
        <dt>Seasonality</dt>
        <dd>
          Day-of-week stats over the lookback: bull % of days, average range,
          which session most often prints the day's high/low, and typical
          weekly high/low days.
        </dd>
        <dt>Pundit board</dt>
        <dd>
          Recent ledger calls with per-author priors: n calls scored, hit
          rate, and average R (ATR-proxy R for stop-less calls). ⚠ marks
          low-sample authors; ● marks calls on a symbol shown above.
        </dd>
      </dl>
    </div>
  {/if}
```

In the `<style>` block append:

```css
  .legend-card { margin-bottom: 16px; }
  .legend-list { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; font-size: 0.85rem; margin: 0; }
  .legend-list dt { font-weight: 600; color: var(--text-dim); }
  .legend-list dd { margin: 0; }
```

- [ ] **Step 4: Build to verify**

Run: `make web-build`
Expected: Vite build completes with no TypeScript/Svelte errors.

- [ ] **Step 5: Commit**

```bash
git add web/ui/src/api.ts web/ui/src/pages/Brief.svelte
git commit -m "feat(brief-ui): LAST price row with source tag + legend card"
```

---

### Task 5: Definition-of-Done sweep, docs sync, PR

**Files:**

- Modify: `CLAUDE.md` (one line, `analytics/brief/` bullet)
- No other code changes — verification and delivery only.

**Interfaces:**

- Consumes: everything above.
- Produces: green DoD + pushed branch + PR.

- [ ] **Step 1: Full gate run**

```bash
make lint-py && make typecheck && make test && make test-regression
```

Expected: all green; regression goldens UNMOVED (brief is not in the regression pipeline — if goldens move, STOP and investigate, do not regenerate).

- [ ] **Step 2: CLAUDE.md sync**

In the `analytics/brief/` bullet of CLAUDE.md, extend the parenthetical description with the M0 behavior so docs match reality. Change:

```text
pure read-only `BriefBundle` over levels/zones/regime/seasonality/pundit files + deterministic markdown renderer
```

to:

```text
pure read-only `BriefBundle` over levels/zones/regime/seasonality/pundit files + deterministic markdown renderer; M0: sweep flags use forming-bar current-period windows, as-of ref price = freshest 1h close w/ fallback chain (`SymbolPanel.ref_price_source`), UI legend card
```

```bash
git add CLAUDE.md
git commit -m "docs: sync CLAUDE.md brief bullet with M0 behavior"
```

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin docs/m0-brief-fixes
```

Then follow the repo's `pr-summary` skill (writes `/tmp/pr-<branch>.md`), create the PR with `gh pr create`, and run the `post-branch` skill before reporting the PR URL. PR body ends with:

```text
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```
