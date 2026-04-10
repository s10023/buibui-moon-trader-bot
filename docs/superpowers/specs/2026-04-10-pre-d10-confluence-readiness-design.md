# Pre-D10 Confluence Readiness — Design Spec

**Date:** 2026-04-10
**Status:** Approved
**Goal:** Establish a trusted, direction-aware signal foundation before building Strategy Confluence (D10).

---

## Overview

Six sequential gates, each a merge-to-main milestone. Nothing starts until the previous gate lands.

```text
Gate 1: A16 threshold evaluation → merge
Gate 2: TOML extends refactor
Gate 3: Direction-split params (tp_r_long/short + directional min_avg_r)
Gate 4: Visual debugging (C14 outcome markers + pattern geometry overlays)
Gate 5: Strategy audit (research process, not a branch)
Gate 6: D10 Strategy Confluence (backtest co-firing → combined alert)
```

**Why this order:**

- Gate 1 before Gate 3 — clean signal data before adding directional param complexity
- Gate 2 before Gate 3 — TOML doubles in size when direction params land; refactor first
- Gate 4 before Gate 5 — can't audit what you can't see
- Gate 5 before Gate 6 — confluence on unaudited strategies amplifies noise, not signal
- Gate 6 last — only meaningful on a trusted, direction-aware foundation

---

## Gate 1: A16 Candle Size Filter — Threshold Evaluation

**Problem:** The current branch uses `min_range_pct = 0.003` (0.3%). Previous analysis found "0 signals filtered" but that used stale data. April 8 BTC 15m examples confirm the filter IS needed:

- 2:00am MYT candle: range = 0.2543%
- 12:45pm MYT candle: range = 0.1935%

Both are below 0.3% and should be filtered as micro-patterns with no meaningful price action.

**Process:**

1. Run threshold sweep across all 6 candlestick strategies (engulfing, pin\_bar, inside\_bar, hammer\_hanging\_man, doji, morning\_evening\_star) on fresh data including April 8
2. Measure: signals filtered %, avg\_r of filtered signals vs kept signals, delta
3. Pick `min_range_pct` based on evidence — balance between noise removal and signal loss
4. Update the branch with the chosen value
5. Merge `feat/a16-candle-size-filter` → main

**Scope:** `analytics/indicators_lib.py` only (filter already wired through all call paths in the branch).

---

## Gate 2: TOML `extends` Refactor

**Problem:** `signal_watch.toml` is 12.5KB. Adding direction-split params (Gate 3) will push it to ~18KB. Shared strategy params are duplicated across 5 config files.

**Design:** TOML has no native include. Implement at the Python loader level.

**File layout after refactor:**

```text
config/
  strategy_params.toml          ← shared base: all strategy sections (~300 lines)
  signal_watch.toml             ← extends base, adds runtime overrides (~30 lines)
  signal_watch_weekdays.toml    ← extends base, different day_filter (~30 lines)
  conservative.toml             ← extends base, overrides tp_r globally (~30 lines)
  scalping.toml                 ← extends base, overrides tp_r + sl_pct (~30 lines)
  swing.toml                    ← extends base, swing-specific overrides (~30 lines)
```

**Loader change** (`signal_config.py` + `backtest_config.py`):

```python
def _load_toml_with_extends(path: str) -> dict:
    raw = tomllib.load(open(path, "rb"))
    if "extends" in raw:
        base_path = Path(path).parent / raw.pop("extends")
        base = tomllib.load(open(base_path, "rb"))
        raw = _deep_merge(base, raw)
    return raw
```

**Merge rules:**

- Scalar values and inline tables: override wins
- Array-of-tables (`[[strategy.bos]]`): if override defines the block, it fully replaces the base entry for that strategy — no partial merging of array entries
- Arrays (e.g. `strategies = [...]`): override fully replaces base

**What moves to `strategy_params.toml`:**

- All `[strategy_timeframes]` entries
- All `[[strategy.*]]` blocks (tp\_r, sl\_pct, per-symbol overrides, adr\_exempt, volume\_suppress, etc.)
- `[bias]` defaults
- `[backtest]` defaults (fee\_pct, min\_sl\_pct, min\_avg\_r, volume\_suppress, volume\_spike\_boost)

**What stays in each config file:**

- `extends = "strategy_params.toml"`
- `timeframes`, `symbols`, `day_filter`, `days`, `min_trades`, `min_trades_*tf`
- Any per-config overrides to strategy params

**Behaviour:** Zero behaviour change — pure structural refactor. All existing tests pass unchanged.

---

## Gate 3: Direction-Split Parameters

**Problem:** `tp_r` is a single value per strategy+TF. In a bear market, longs have less room to run — a 2.5R TP that works for shorts never gets reached on longs, suppressing long win rate unfairly. The hard-mode `min_avg_r` threshold applies the same pass/fail bar to both directions, further punishing longs.

### New fields

`StrategySpec` (`indicators_lib.py`):

```python
tp_r_long: float | None = None   # falls back to tp_r if not set
tp_r_short: float | None = None

def get_tp_r(self, direction: str) -> float:
    if direction == "long" and self.tp_r_long is not None:
        return self.tp_r_long
    if direction == "short" and self.tp_r_short is not None:
        return self.tp_r_short
    return self.tp_r
```

`StrategyOverride` (`signal_config.py` + `backtest_config.py`):

- Add `tp_r_long: float | None = None`, `tp_r_short: float | None = None`
- Parsed from TOML `[[strategy.*]]` blocks

`BacktestFilterConfig`:

- Add `min_avg_r_long: float | None = None`, `min_avg_r_short: float | None = None`
- Falls back to `min_avg_r` when not set

### Backtest engine (`backtest_lib.py`)

- `Trade.direction` already exists
- Use `get_tp_r(trade.direction)` when computing TP distance — one lookup change

### Signal path

- `signal_lib.py`: `_compute_backtest()` passes direction into tp\_r resolution
- `alert_formatter.py`: shows the directional tp\_r used in the alert

### WFO sweep (`param_sweep.py`)

- Add `tp_r_long` / `tp_r_short` to sweep param ranges alongside existing `tp_r`
- Results already split by direction — extend the grid

### TOML example (in `strategy_params.toml`)

```toml
[[strategy.bos]]
tp_r = 2.5          # fallback for combined / unset direction
tp_r_long = 1.8     # tighter in bear market
tp_r_short = 2.5    # shorts have more room
```

---

## Gate 4: Visual Debugging

Two sub-items delivered on one branch. Together they enable visual strategy auditing in Gate 5.

### 4a — C14: Signal Outcome Markers

**DB change:**

- New columns on `signals` table: `outcome TEXT NULL` ('win'/'loss'), `closed_at_ms BIGINT NULL`

**Outcome resolution job** (`analytics/outcome_resolver.py`):

- `resolve_outcomes(conn)`: for each signal with `outcome IS NULL`, scan subsequent OHLCV forward; first candle to touch TP price → 'win', first to touch SL price → 'loss'
- Runs at web server startup and once per sync cycle (only processes NULL rows — cheap)

**Chart frontend:**

- Signal markers already render on Chart tab
- Add `color` (green/red/grey) and `text` (`✓`/`✗`) fields from outcome
- lightweight-charts `SeriesMarker` supports both `color` and `text` fields

### 4b — Pattern Geometry Overlays

**New API endpoint:** `GET /api/chart/overlays/{symbol}/{tf}?days=N`

Returns geometry for each structural strategy — computed server-side from OHLCV (reuses detector internals):

| Strategy | Geometry |
| -------- | -------- |
| FVG | Green/red price boxes (gap zone low/high, start candle to current edge) |
| Order Block | Zone box: last opposing candle body before impulse move |
| BOS | Horizontal line at broken swing level, origin candle to current edge |
| EQH/EQL | Paired horizontal lines at equal high/low levels |
| Liq Sweep | Horizontal line at swept prior swing high/low |

**Frontend:**

- Each strategy gets a toggle pill in the Indicators row (alongside EMA/RSI/Range Levels/CME Gap)
- Overlays extend right to current candle edge (same pattern as Range Levels)
- Hidden when strategy not in active config (same hiding logic as strategy group pills)

---

## Gate 5: Strategy Audit

Not a feature branch. A research process that produces a findings doc.

**Scope:** Five structural strategies — BOS, FVG, liq\_sweep, order\_block, eqh\_eql.

**Process per strategy:**

1. Use Gate 4 overlays to visually inspect 20–30 historical signals on Chart tab
2. Questions: Does the detected pattern look correct? Is entry timing right? Where does it lose?
3. Cross-reference Analysis sub-tab direction bias — is loss concentrated on one direction?
4. Run `buibui signal test --at <ts>` on specific fail cases to diagnose logic
5. Deliver verdict: **keep as-is** / **tune params** / **suppress TF combo** / **logic fix needed**

**Output:** `docs/strategy-audit-2026.md` — one section per strategy with verdict + evidence.

Only strategies that pass audit (keep / tune) are candidates for confluence pairing in Gate 6.

---

## Gate 6: D10 Strategy Confluence

Delivered in two phases. Phase 1 must prove edge before Phase 2 changes alert behaviour.

### Phase 1 — Backtest co-firing measurement

**New DB table** `confluence_pairs`:

```sql
CREATE TABLE confluence_pairs (
    symbol TEXT,
    tf TEXT,
    strategy_a TEXT,
    strategy_b TEXT,
    candle_ts BIGINT,
    direction TEXT,
    outcome TEXT NULL,
    r_multiple REAL NULL,
    PRIMARY KEY (symbol, tf, strategy_a, strategy_b, candle_ts, direction)
)
```

Populated during backtest: when 2+ strategies fire on the same symbol+TF+candle, record the pair.

**New digest query `confluence_pairs`** (added to `digest_lib.py`):

- Shows: pair → co-firing count, combined avg\_r, win\_rate vs each strategy solo
- CLI: `buibui digest --query confluence_pairs`

Only run on strategies that passed Gate 5 audit.

### Phase 2 — Combined alert (proven pairs only)

**Trigger:** 2 strategies fire on same symbol+TF+candle within the same scan cycle AND the pair appears in a pre-approved list (from Phase 1 findings).

**Alert behaviour:**

- Emit one merged Telegram message: "⚡ BOS + fib\_golden\_zone — 2-strategy confluence"
- Use the better (more conservative) of the two tp\_r values
- Cooldown dedup still applies per `(symbol, direction, cooldown_window)`
- Single-strategy alerts unchanged — confluence is additive, not a gate

**Pre-approved pairs list:** hardcoded initially (from Phase 1 backtest findings), TOML-configurable later.

---

## What This Enables

After all 6 gates:

- Signal data is clean (no micro-pattern noise from Gate 1)
- Config is maintainable (shared base from Gate 2)
- Direction-aware TP and quality bar (Gate 3) — longs and shorts tuned independently
- Every signal's outcome is visible on the chart (Gate 4)
- Structural strategies are understood and trusted (Gate 5)
- Confluence alerts fire only on proven pairs with measured edge (Gate 6)
