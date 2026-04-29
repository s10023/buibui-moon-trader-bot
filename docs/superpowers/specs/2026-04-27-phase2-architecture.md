# Phase 2 — Core Code Architecture (Design Spec)

**Date:** 2026-04-27
**Status:** Draft (ready for `/writing-plans`)
**Owner:** s10023
**Predecessors:**
`2026-04-25-overhaul-roadmap.md`,
`2026-04-25-phase0-strategy-findings.md`,
`2026-04-25-phase0-skills-audit.md`,
`2026-04-26-phase1-foundations.md`

## Goal

Split the 6 monster files into per-concern packages so Phase 3's per-strategy audit can land 1 strategy per PR (~30-line diffs) instead of 1 strategy per `git blame` excavation through a 3,143-line file. Behaviour preserved end-to-end. `main` green after every PR.

The split is purely structural — no logic changes, no abstraction layer added, no inheritance introduced. Detectors stay duck-typed `def detect_<name>(df) -> pd.DataFrame` functions held together by `STRATEGY_REGISTRY` and `DETECTOR_REGISTRY` dicts. Each file we extract gets a re-export shim at the old path so external callers (param_sweep, backtest_runner, signal_runner, web routers, 30+ test files) need no edits.

## Non-goals

- **No `src/` layout migration.** Originally bundled with Phase 2 in the roadmap; deferred (likely to its own dedicated phase, possibly never). Doing it alongside the splits doubles the failure mode per PR — every PR would mix structural moves with rename churn across 30+ import sites. Current flat-top-level layout stays.
- **No detector logic changes.** Phase 3 does that.
- **No new abstraction.** No `BaseStrategy` ABC, no `Detector` Protocol class, no decorator-based registration. Plain functions, plain dataclasses, plain dicts.
- **No CLI surface changes.** `buibui --help` output is byte-identical (or trivially-identical, e.g., alphabetical reorder of subcommands) before and after the CLI split.
- **No Poetry entry-point change.** `[tool.poetry.scripts]` stays pointing at `buibui.py`. After cli-1 that file becomes a 3-line entry: `from cli.main import main; main()`.
- **No P5/P8 perf optimisations.** Profile-only in this phase. P5 (batch candle sync) and P8 (DuckDB covering indexes) move to Phase 4 scope.
- **No regression-fixture regeneration.** End-of-phase regression must pass against UNCHANGED goldens. Drift = code bug, bisect; do not run `make regression-update` reflexively.

## Scope (in / out)

**In:** split 6 monster files into per-concern packages with re-export shims; formalise the `analytics/` ↔ `signals/` boundary contract with an AST-walking pytest gate; profile baseline at start and end of phase; defer `src/` and the two concrete perf items.

**Out:** monster-file logic edits (Phase 3); `src/` adoption; backtest-pipeline behavioural changes (Phase 4); UI/API work (Phase 5); funding-rate plumbing changes (Phase 4 if at all); MEMORY-listed To-Do items not on the P1 list.

## Open-question resolutions

### Q1. Test partition rule

**Decision: hybrid (Q1 = c).**

- `tests/` for `analytics/strategies/` mirrors the per-strategy file split: `tests/strategies/test_wick_fill.py`, `tests/strategies/test_marubozu.py`, etc. Phase 3 audits become `git diff strategies/wick_fill.py tests/strategies/test_wick_fill.py` — one strategy, one PR, one diff.
- `tests/` for `analytics/stats/`, `analytics/backtest/`, `analytics/store/`, `analytics/signal/` stay as bundled files (`test_stats_lib.py`, `test_backtest_lib.py`, etc.). No Phase 3-equivalent workflow operates over those modules per-symbol.
- Existing partial-splits (`test_indicators_lib.py`, `test_candle_patterns.py`, `test_fib_strategies.py`) get reorganised to fit the new convention — each detector's tests collected into one `tests/strategies/test_<strategy>.py` file.

### Q2. Canonical `SignalEvent` import path

**Decision: package-level re-export (Q2 = b).**

`analytics/signal/__init__.py` does `from .types import SignalEvent` and lists it in `__all__`. Canonical import: `from analytics.signal import SignalEvent`. The deep path `analytics.signal.types` works but is not the documented form.

Same rule applies to `analytics/strategies` and `analytics/store` — package `__init__.py` is the public API.

### Q3. PR strat-2 size

**Decision: single atomic PR (Q3 = a).**

`strat-2` moves all 20 detectors to per-strategy files in one shot (~2,700-line diff, near-100% pure motion). Reviewed as "did the script run cleanly + do tests pass?" not as a line-by-line read. Splitting into batches creates 4–6 half-built-registry intermediate states, each requiring a 3-layer gate run; the cost outweighs the reviewability benefit when the diff is mechanical.

### Q4. End-of-Phase-2 regression-fixture handling

**Decision: zero drift permitted (Q4 = b).**

`make test-regression` against UNCHANGED Phase-1-era goldens passes after every PR including `strat-2`. If drift appears, treat as a code bug — bisect the offending PR, fix the root cause, do not regenerate goldens. Goldens are regenerated only at intentional phase boundaries (e.g., when Phase 3's strategy logic edits land).

## Per-file split design

### 2.1 `buibui.py` CLI (1,141 LOC)

**Target:** `cli/` package + 3-line `buibui.py` entry.

```text
cli/
├── __init__.py
├── main.py            # argparse tree assembly + dispatch (~200 LOC)
├── _common.py         # _parse_since_to_ms, _parse_smt_pairs, shared parser helpers
├── monitor.py         # run_price_monitor, run_position_monitor + add_*_subparser
├── analytics.py       # run_analytics_backfill, run_analytics_sync + add_*_subparser
├── digest.py          # run_digest_cmd + add_digest_subparser
├── backtest.py        # run_backtest + add_backtest_subparser (~120 LOC)
├── signal.py          # run_signal_test, run_signal_watch + add_signal_subparser (~140 LOC)
├── param.py           # run_param_sweep, run_param_audit + add_param_subparser (~125 LOC)
├── recalibrate.py     # run_recalibrate + add_recalibrate_subparser
└── web.py             # run_web_server + add_web_subparser

buibui.py              # 3-line entry: from cli.main import main; main()
```

**Per-command file contract:**

```python
# cli/backtest.py
def add_backtest_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the `buibui backtest` subparser. Called by cli/main.py."""

def run_backtest(args: argparse.Namespace) -> None:
    """Handler invoked by cli/main.py dispatch."""
```

`cli/main.py` imports each `add_<cmd>_subparser` and `run_<cmd>` pair, builds the argparse tree, dispatches based on `args.command`. Keeps the full argparse tree assembly visible in one file for `--help` parity.

**Risk:** Low. Pure motion. Manual smoke: `buibui --help` output diffed against pre-split capture; trivial reordering allowed, missing flags = revert.

### 2.2 `analytics/stats_lib.py` (1,529 LOC)

**Target:** `analytics/stats/` flat package.

```text
analytics/stats/
├── __init__.py            # __all__ re-exports all dataclasses + compute_*
├── _common.py             # shared helpers (timezone constants, session windows)
├── p1p2.py                # compute_p1p2_daily + P1P2DailyResult
├── hourly.py              # compute_hourly_extremes + HourlyExtremesResult
├── adr.py                 # compute_adr + ADRResult
├── dow.py                 # compute_dow_patterns + DOWPatternsResult
├── session.py             # compute_session_breakdown + SessionBreakdownResult
├── daily_distance.py      # compute_daily_distance + DailyDistanceResult
├── weekly_p1p2.py         # compute_weekly_p1p2 + WeeklyP1P2Result
├── weekly_p2_timing.py    # compute_weekly_p2_timing + WeeklyP2TimingResult
├── weekly_state.py        # compute_weekly_current_state + WeeklyCurrentStateResult
├── weekly_flip_risk.py    # compute_weekly_flip_risk_conditioned + WeeklyFlipRiskResult
├── weekly_wick.py         # compute_weekly_wick_percentile + WeeklyWickResult
└── bundle.py              # compute_all bundler, _inject_live_fields()

analytics/stats_lib.py     # shim: from analytics.stats import *
```

Flat layout (no `weekly/` sub-package) per locked decision — 5 weekly files at top level keeps imports short and discovery cheap.

**Per-stat file contract:** dataclass(es) + `compute_<stat>(...)` function + any private helpers used only by that stat.

**Risk:** Low. All 11 compute functions are independent — no cross-imports between them today. Bundle module just calls each.

### 2.3 `analytics/backtest_lib.py` (1,424 LOC)

**Target:** `analytics/backtest/` package, 5 files.

```text
analytics/backtest/
├── __init__.py            # re-exports Trade, BacktestResult, ComboBacktestResult,
│                          # CrossTfComboBacktestResult, run_backtest, format_*
├── engine.py              # Trade, BacktestResult, run_backtest, _compute_atr14
├── gates.py               # _is_low_volume, _is_volume_spike, filter_signals_by_day
│                          # (underscore preserved on volume gates)
├── combo.py               # ComboBacktestResult + run_combo_backtest
├── cross_tf.py            # CrossTfComboBacktestResult + run_cross_tf_combo_backtest
└── formatters.py          # 9× format_* functions (format_seasonality, etc.)

analytics/backtest_lib.py  # shim: from analytics.backtest import *
```

`_is_low_volume` and `_is_volume_spike` keep the leading underscore (de-facto-public — imported by 2 tests + signal_lib). `filter_signals_by_day` is public (no underscore) — used by both engine and runners.

**Risk:** Med. `run_backtest` is the most-imported function in the codebase after `data_store` helpers. Manual smoke: `make buibui-backtest CONFIG=config/signal_watch.toml SAVE=0` against a single symbol; result table diffs zero against pre-split capture.

### 2.4 `analytics/data_store.py` (1,434 LOC)

**Target:** `analytics/store/` package, 9 files. Two PRs.

```text
analytics/store/
├── __init__.py            # __all__ re-exports BacktestSnapshot, init_schema,
│                          # DEFAULT_DB_PATH, all upsert/get/list functions
├── _common.py             # _upsert helper (load-bearing — try/finally with explicit
│                          # conn.register/unregister; NEVER switch to implicit
│                          # replacement scan), _connect helper, DEFAULT_DB_PATH
├── schema.py              # init_schema (all 13 CREATE TABLEs in one place)
├── market_data.py         # ohlcv, funding_rates, open_interest upserts/gets
├── signals.py             # signals + signal_alert_outcomes upserts/queries/history
├── backtest_runs.py       # backtest_runs + backtest_trades upserts/queries
├── backtest_cache.py      # backtest_cache table + get/put/prune_backtest_cache
├── stats_cache.py         # stats_cache get/put/invalidate
├── confidence.py          # confidence_ratings + confidence_ratings_v2 + BacktestSnapshot
└── combos.py              # backtest_combos + backtest_cross_tf_combos
                            # + query_cross_tf_combos

analytics/data_store.py    # shim: from analytics.store import *
```

**Two-PR sequence** (per locked decision: leave callers alone — no opportunistic caller migration):

- **`store-1` (PR 5):** Extract `analytics/store/schema.py` only. `data_store.py` keeps everything else but imports `init_schema` from the new module. Tiny, low-risk PR proves the package import chain works.
- **`store-2` (PR 6):** Move all upsert/get/list functions to their table-group files. `data_store.py` becomes the shim. Largest data-layer blast radius in the phase.

**Critical:** `_upsert` helper in `_common.py` is moved verbatim — no edits, no rewrites. The try/finally with explicit `conn.register("__rel", df)` / `conn.unregister("__rel")` is the only way to avoid the malloc heap corruption bug documented in CLAUDE.md. Spec-level rule: any PR that touches `_upsert`'s body in any way (formatting, type hints, comment changes) is rejected.

**Risk:** Med-High. 30+ external import sites including every web router. Manual smoke after store-2: full `make test-regression` + `buibui web` startup + one API call per router (`GET /api/active-config`, `GET /api/zones`, `GET /api/signals/recent`).

### 2.5 `analytics/signal_lib.py` (1,726 LOC)

**Target:** `analytics/signal/` package, 8 files. Three PRs.

```text
analytics/signal/
├── __init__.py            # __all__ re-exports SignalEvent, scan_symbol,
│                          # run_scan_cycle, _reset_bt_cache, etc.
├── types.py               # SignalEvent (MOVED from signals/alert_formatter.py)
├── _common.py             # _CANDLE_CLOSE_BUFFER_SECS, _SCAN_WINDOW=200,
│                          # _bt_mem_cache (MUST live here, never re-bound),
│                          # _reset_bt_cache, _fmt_hold,
│                          # parse_timeframe_secs, secs_until_next_boundary
├── gates.py               # _filter_signals_by_adr, _is_adr_exempt
├── resolvers.py           # all 10× _resolve_* helpers (de-facto-public —
│                          # imported by param_sweep + 4 tests; explicit __all__)
├── bt_cache.py            # _compute_backtest, _backtest_summary
├── stats_context.py       # _compute_stats_context
├── cofire.py              # _find_live_cofire, _find_cross_tf_cofire,
│                          # _parse_htf_ltf_pairs
└── scanner.py             # scan_symbol (~580 LOC) + run_scan_cycle (~370 LOC)

analytics/signal_lib.py    # shim: from analytics.signal import *
```

**Three-PR sequence:**

- **`signal-1` (PR 7):** Move `SignalEvent` to `analytics/signal/types.py`. Update import lines in `signal_runner.py` + `signal_test_runner.py`. Add `tests/test_layering.py` AST-walking layering enforcement (see §3). Closes the `analytics → signals` boundary violation. ~30-line code diff + new test file.
- **`signal-2` (PR 8):** Extract leaves: `_common.py`, `gates.py`, `resolvers.py`, `bt_cache.py`, `stats_context.py`, `cofire.py`. `signal_lib.py` keeps `scan_symbol` + `run_scan_cycle`, imports leaves from the package.
- **`signal-3` (PR 9):** Move `scan_symbol` + `run_scan_cycle` into `scanner.py`. `signal_lib.py` becomes the shim. Highest blast radius in the phase.

**Critical hazards:**

1. **`_bt_mem_cache` module-state preservation.** Lives in `_common.py`. `_reset_bt_cache()` mutates it via `_bt_mem_cache.clear()` (NOT re-binding). Any code that imports it does `from analytics.signal._common import _bt_mem_cache` then mutates in place — never `_bt_mem_cache = {}`. After each PR the dict identity is the same.
2. **Underscore-prefix preservation on `_resolve_*` helpers.** They're de-facto-public (imported externally) but were never publicised. Keep underscore + add explicit `__all__` in `resolvers.py` so the import path is stable.
3. **Manual smoke after signal-3:** `make buibui-signal-test SYMBOL=BTCUSDT TF=1h` against a known historical candle. Full live-scan path; no unit test exercises this end-to-end.

**Risk:** High at signal-3 specifically (10+ external import sites for `scan_symbol`).

### 2.6 `analytics/indicators_lib.py` (3,143 LOC)

**Target:** `analytics/strategies/` package, 25 files. Two PRs.

```text
analytics/strategies/
├── __init__.py            # re-exports STRATEGY_REGISTRY, DETECTOR_REGISTRY,
│                          # KNOWN_STRATEGIES, KNOWN_STRATEGY_TYPES,
│                          # STRATEGY_TYPE_GROUPS, INCOMPATIBLE_PAIRS,
│                          # SIGNAL_COLUMNS, ParamSpec, StrategySpec,
│                          # seasonality_stats (re-exported for stats consumers)
├── _base.py               # ParamSpec, StrategySpec dataclasses, SIGNAL_COLUMNS
├── _shared.py             # _find_bos_swing (used by bos.py, choch.py, AND
│                          # analytics/zones_lib.py), volume_confirm
├── _registry.py           # explicit _STRATEGY_MODULES tuple in canonical order;
│                          # imports each module's SPEC; assembles
│                          # STRATEGY_REGISTRY, DETECTOR_REGISTRY,
│                          # KNOWN_STRATEGIES, INCOMPATIBLE_PAIRS
├── _seasonality.py        # seasonality_stats (NOT a detector — kept adjacent to
│                          # the seasonality.py detector that uses it)
├── wick_fill.py           # detect_wick_fills + SPEC = StrategySpec(...)
├── marubozu.py
├── orb.py
├── liquidity_sweep.py
├── fvg.py
├── bos.py
├── choch.py
├── eqh_eql.py
├── ob.py
├── smt_divergence.py
├── inside_bar.py
├── pin_bar.py
├── hammer.py
├── engulfing.py
├── morning_star.py
├── doji.py
├── trend_day.py
├── fib_golden_zone.py
├── ote_entry.py
└── seasonality.py         # detect-only; helper imports from _seasonality.py

analytics/indicators_lib.py    # shim: from analytics.strategies import *
                                # plus explicit re-exports for any symbol the
                                # `*` import doesn't carry
```

**Per-strategy file contract:**

```python
# analytics/strategies/wick_fill.py
import pandas as pd
from analytics.strategies._base import StrategySpec, ParamSpec, SIGNAL_COLUMNS
from analytics.strategies._shared import volume_confirm

def detect_wick_fills(df: pd.DataFrame, ...) -> pd.DataFrame: ...

SPEC = StrategySpec(
    name="wick_fill",
    detector=detect_wick_fills,
    params=[ParamSpec(...)],
    ...
)
```

Each file owns: its `detect_*` function, its single `SPEC` literal, any private helpers used only by that detector. No cross-strategy imports — if two strategies share a helper, it goes in `_shared.py`.

`_registry.py` uses an **explicit** module tuple, not `pkgutil.iter_modules`:

```python
_STRATEGY_MODULES = (
    "analytics.strategies.wick_fill",
    "analytics.strategies.marubozu",
    # ... 18 more in canonical order matching old STRATEGY_REGISTRY ordering
)
```

This guarantees registry iteration order matches the pre-split source order — regression goldens stay byte-equal.

**Test partition** (per Q1):

```text
tests/strategies/
├── test_wick_fill.py       # extracted from test_indicators_lib.py
├── test_marubozu.py
├── ... 18 more ...
└── test_seasonality.py
tests/test_strategy_registry.py   # tests for STRATEGY_REGISTRY, INCOMPATIBLE_PAIRS,
                                  # taxonomy invariants
```

`tests/test_indicators_lib.py`, `tests/test_candle_patterns.py`, `tests/test_fib_strategies.py` are removed; their content redistributed.

**Two-PR sequence (per locked C-sequencing):**

- **`strat-1` (PR 10):** Scaffold `analytics/strategies/` with `_base.py`, `_shared.py`, `_registry.py`, `_seasonality.py`, `__init__.py`. Move `ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`, `_find_bos_swing`, `volume_confirm`, `seasonality_stats`, `INCOMPATIBLE_PAIRS`. `indicators_lib.py` keeps the 20 detectors but imports the moved infra. ~400-line move, registry mechanics visible in isolation.
- **`strat-2` (PR 11):** Atomic mechanical move — every `detect_*` + its `StrategySpec` literal goes into a per-strategy file. `indicators_lib.py` becomes the shim. Tests reorganised to `tests/strategies/`. ~2,700-line diff, near-100% pure motion.

**Risk:** High at strat-2 (registry-shape regressions hide easily). Mitigation: explicit `_STRATEGY_MODULES` tuple guards iteration order; full `make test-regression` + `make buibui-signal-test` smoke per strategy taxonomy group (Structure / Fibonacci / Price Action / Candlestick / Flow / Session) before merge.

## Section 3 — `analytics/` ↔ `signals/` boundary contract

**Three rules:**

1. `analytics/*` MUST NOT import from `signals/*`.
   *(Currently violated: `analytics/signal_lib.py` imports `SignalEvent` from `signals/alert_formatter.py`. Fixed by PR signal-1 relocating `SignalEvent` to `analytics/signal/types.py`.)*
2. `signals/*` MAY import from `analytics/*` — one direction only.
3. Domain ownership:
   - `signals/` owns: `SIGNAL_REGISTRY` (alerting plugins), `cooldown_store` (dedup), `alert_formatter` (Telegram message construction).
   - `analytics/signal/` owns: `SignalEvent`, `StatsContext`, `ConfluenceData`, the scanner, all detection wiring.

**Enforcement:** `tests/test_layering.py` — AST-walking pytest that walks `ast.parse` over every `.py` under `analytics/`, fails on any `from signals` / `import signals` node. ~30 lines, no new dependency, fails fast in `make test`. Bundled into PR signal-1 so the gate exists from day one of any signal_lib work.

The test is extensible — future contracts (e.g., "no `web/api/routers/*` may import from `analytics/store/_common.py`") slot in as additional asserts.

## Section 4 — P1 perf hot-spots

MEMORY P1 is the umbrella "profile first" item. P5 (batch candle sync in `run_scan_cycle`) and P8 (DuckDB covering indexes on `(symbol, timeframe, open_time_ms)`) are the two concrete optimisations.

**Decision: profile-only in Phase 2; defer P5 + P8 to Phase 4 (Backtest / signal pipeline).**

Rationale: Phase 2's gate is *behaviour-preserving*. Perf optimisations are intrinsically behavioural (timing, query plans, occasionally correctness). Bundling them with the splits muddies bisect. Phase 4's scope (backtest / signal pipeline) is where the hot paths live, making it the natural home.

The profile baseline is genuinely useful as a *regression check* on the splits themselves — if Phase 2's close-of-phase profile shows >10% wall-clock regression on any benchmarked path, we have evidence the split introduced something.

**`perf-1` PR shape (PR 1):**

- `scripts/profile_suite.py` — runs `cProfile` over: 1× full backtest run on BTCUSDT/1h, 1× `param_sweep` for one strategy, 1× `run_scan_cycle` against a stubbed Binance client, 1× combo backtest. Each profile runs 3× and reports median + IQR.
- `docs/perf-baseline-2026-04-27.md` — top 20 cumulative lines from each profile + wall-clock totals.
- No code changes outside `scripts/`. Risk: zero.

**`perf-2` PR shape (PR 12):** identical profile suite re-run; output to `docs/perf-baseline-phase2-close.md`; manual diff vs `perf-1` baseline; flag any >10% regression.

**Phase 4 scope addition (annotate roadmap when Phase 2 closes):**

- P5: batch OHLCV upsert + signal persist into single write transaction per `run_scan_cycle`.
- P8: covering index on `(symbol, timeframe, open_time_ms)`; EXPLAIN ANALYZE on `get_signals_history` and `list_backtest_runs`.

## PR-by-PR breakdown

| # | PR | Touches | Risk | Notes |
| --- | --- | --- | --- | --- |
| **1** | `perf-1` profile baseline | `scripts/profile_suite.py`, `docs/perf-baseline-2026-04-27.md` | None | 3-run median; commits a doc. |
| **2** | `cli-1` split CLI | `buibui.py` → 3-line; new `cli/` package | Low | Manual smoke: `buibui --help` byte-diff. |
| **3** | `stats-1` split stats_lib | 1 → 12 files; shim | Low | 11 compute_* are independent. |
| **4** | `backtest-1` split backtest_lib | 1 → 5 files; shim | Med | Manual smoke: 1 backtest run. |
| **5** | `store-1` extract schema | new `analytics/store/schema.py`; data_store imports it | Low | Proves package import chain. |
| **6** | `store-2` split table groups | 1 → 9 files; shim | Med-High | Web router smoke. |
| **7** | `signal-1` SignalEvent move + boundary AST test | `signals/alert_formatter.py` (delete SignalEvent), `analytics/signal/types.py` (new), `tests/test_layering.py` (new) | Low | Closes boundary violation; gate active from here. |
| **8** | `signal-2` extract signal leaves | 6 new files in `analytics/signal/`; signal_lib keeps scanner | Med | `_bt_mem_cache` lives in `_common.py`. |
| **9** | `signal-3` move scanner | `analytics/signal/scanner.py`; signal_lib becomes shim | High | `make buibui-signal-test` smoke. |
| **10** | `strat-1` strategies scaffold | `_base.py`, `_shared.py`, `_registry.py`, `_seasonality.py`, `__init__.py`; indicators_lib imports them | Low | ~400-line move. |
| **11** | `strat-2` atomic detector move | 20 detectors → per-strategy files; tests → `tests/strategies/`; indicators_lib becomes shim | High | ~2,700-line diff; full regression + smoke per taxonomy group. |
| **12** | `perf-2` close-of-phase baseline | `docs/perf-baseline-phase2-close.md` | None | Diff vs `perf-1`; >10% = investigate. |

**Total: 12 PRs.** Estimated 2–4 calendar weeks at solo pace.

**Universal gate per PR (durable):**

1. `make lint-py && make typecheck && make test` clean (test count never shrinks).
2. `make test-regression` against UNCHANGED Phase-1-era goldens.
3. From PR 7 onward: `tests/test_layering.py` passes.
4. Shim smoke: `python -c "from analytics.<old_module> import <key_symbol>; assert ..."` per shim.
5. PR-specific manual smokes (CLI help, backtest run, web router, signal-test, etc. — see PR table).

**Hard rule:** if any of (1)(2)(3)(4) fail and the cause isn't immediately obvious, revert and bisect — do not patch forward inside the same PR.

## Risk register

| Risk | Likelihood | Impact | Mitigation | Rollback play |
| --- | --- | --- | --- | --- |
| `_bt_mem_cache` rebound — after signal-2 split, `_reset_bt_cache()` mutates a different dict than `_compute_backtest` reads | Med | Backtest cache silently stale; param sweeps return wrong numbers | Define `_bt_mem_cache` once in `_common.py`; `_reset_bt_cache` lives in same file; never `from x import _bt_mem_cache` then re-assign | Revert signal-2; if too late, hot-fix with `_common._bt_mem_cache.clear()` |
| `_upsert` malloc corruption — accidentally switch to implicit replacement scan during data_store split | Low | Hard crash; possible DB corruption | Hard rule: `_upsert` body moves verbatim, no edits permitted in any PR; spec rejects diffs that touch the body | Revert store-2 immediately |
| `scan_symbol` import cycle after signal-3 — scanner.py imports from store/, signal_lib shim imports from scanner, runner imports from signal_lib | Med | ImportError at startup; daemon dead | Pre-merge: `python -c "import analytics.signal_lib; import analytics.signal.scanner"` from clean env | Revert signal-3 |
| `STRATEGY_REGISTRY` ordering drift in strat-2 — pkgutil scan replaces canonical source ordering | Low (mitigated) | Subtle: backtest output ordering differs; regression goldens diff | `_registry.py` uses an explicit `_STRATEGY_MODULES` tuple in canonical order, NOT pkgutil scan | If goldens drift: check `_STRATEGY_MODULES` order matches old `STRATEGY_REGISTRY` keys |
| Boundary AST test false positive — pytest layering rule blocks a legitimate import in `signals/` because of typo glob | Low | CI red | Test has explicit comments per import; review carefully in PR signal-1 | Tighten the rule; not a revert |
| Shim re-export hides new public symbol — `from x import *` doesn't carry symbols missing from `__all__` | Med | Old import path silently breaks for new symbols | Each shim does `from <pkg> import *` + explicit named re-exports; `__init__.py` defines `__all__` exhaustively | Add to `__all__`; one-line fix |
| Regression goldens drift from data, not code, masking a behavioural change | Low (durable feedback note exists) | False sense of safety | Spec clause: regression failure after a split PR is treated as code bug until proven otherwise | If proven data-only: regen, document date in commit |
| CLI argparse builder drift — `--help` output differs after cli-1 | Low | UX-visible | `cli/main.py` keeps the argparse tree assembly; per-command files only export `add_<cmd>_subparser` and `run_<cmd>` | Capture `buibui --help` pre-cli-1; post-diff check; large diff = revert |
| Profile suite false alarm — `perf-2` shows >10% regression from a single noisy run | Low | Wasted investigation | Each profile runs 3× and reports median + IQR | Re-run; document variance |
| `_find_bos_swing` import drift — `analytics/zones_lib.py` imports it from `indicators_lib` today; split must keep that path working | Low | zones_lib breaks → `GET /api/zones` 500s | Shim re-exports `_find_bos_swing` from `analytics.indicators_lib`; zones_lib import line unchanged | Add explicit re-export in `indicators_lib.py` shim |
| Test reorganisation in strat-2 loses a test file silently | Low | Coverage drops without anyone noticing | Pre-strat-2: capture `pytest --collect-only` test count; post-strat-2: count must match | Restore missing tests |

## Acceptance criteria

- All 12 PRs merged on `main` with green gate (lint+typecheck+test+regression+layering).
- `make test-regression` passes against the same goldens that pass on `main` today (Phase-1 era). Zero regeneration during Phase 2.
- Manual smokes per PR table all green.
- `analytics/indicators_lib.py`, `analytics/signal_lib.py`, `analytics/data_store.py`, `analytics/stats_lib.py`, `analytics/backtest_lib.py`, `buibui.py` all reduced to shim/entry size (<= ~50 LOC each).
- `tests/test_layering.py` passes; `signals/` no longer exports `SignalEvent`.
- `docs/perf-baseline-2026-04-27.md` and `docs/perf-baseline-phase2-close.md` both committed; close-of-phase shows no >10% wall-clock regression on any benchmarked path.
- MEMORY To-Do list updated: P5 + P8 moved to Phase 4 scope; P1 marked done.

## Build order summary

```text
PR 1   perf-1                    profile baseline (no code change)
PR 2   cli-1                     CLI split (most-leaf)
PR 3   stats-1                   stats_lib split
PR 4   backtest-1                backtest_lib split
PR 5   store-1                   data_store schema extract
PR 6   store-2                   data_store table-group split
PR 7   signal-1                  SignalEvent move + boundary AST test
PR 8   signal-2                  signal_lib leaves split
PR 9   signal-3                  scanner move (highest blast radius in signal)
PR 10  strat-1                   strategies scaffold + infra move
PR 11  strat-2                   atomic 20-detector move (highest blast radius overall)
PR 12  perf-2                    close-of-phase profile (no code change)
```

Sequential. No parallelism — each PR's shim assumes the previous one merged. Solo developer; sequential is fine and the gate matters more than wall-clock.

---

Ready for `/writing-plans` to produce the implementation plan.
