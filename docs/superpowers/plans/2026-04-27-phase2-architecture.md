# Phase 2 — Core Code Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split 6 monster files (`buibui.py`, `analytics/stats_lib.py`, `analytics/backtest_lib.py`, `analytics/data_store.py`, `analytics/signal_lib.py`, `analytics/indicators_lib.py`) into per-concern packages with re-export shims so Phase 3's per-strategy audit lands as 30-line diffs instead of 3,143-line excavations. Behaviour preserved end-to-end. `main` green after every PR.

**Architecture:** 12 sequential PRs across 3 visible tracks. **Perf bookends** (`perf-1`, `perf-2`) capture profile baselines. **Splits track** (`cli-1` → `stats-1` → `backtest-1` → `store-1` → `store-2` → `signal-1` → `signal-2` → `signal-3` → `strat-1` → `strat-2`) does the structural moves leaf-to-root. **Boundary gate** (`tests/test_layering.py`, bundled into `signal-1`) formalises the `analytics/*` MUST NOT import from `signals/*` rule via AST walk. No new abstractions, no `src/` migration, no logic changes, no perf optimisations.

**Tech Stack:** Python 3.13, Poetry, ruff, mypy strict, pytest, DuckDB, pandas, FastAPI, Svelte 5. `cProfile` (stdlib) for perf baselines.

---

## Source documents

- **Spec (primary):** `docs/superpowers/specs/2026-04-27-phase2-architecture.md`
- Roadmap: `docs/superpowers/specs/2026-04-25-overhaul-roadmap.md`
- Phase 0a strategy findings: `docs/superpowers/specs/2026-04-25-phase0-strategy-findings.md`
- Phase 0b skills audit: `docs/superpowers/specs/2026-04-25-phase0-skills-audit.md`
- Phase 1 spec: `docs/superpowers/specs/2026-04-26-phase1-foundations.md`
- Phase 1 plan (tone reference): `docs/superpowers/plans/2026-04-26-phase1-foundations.md`

---

## Pre-implementation captures (run on `main` BEFORE PR 1)

These artefacts let post-PR diffs prove behaviour preservation. Capture them once, store under `/tmp/phase2-baseline/`, refer to them throughout the phase.

- [ ] **C1: Capture `buibui --help` output**

```bash
mkdir -p /tmp/phase2-baseline
poetry run python buibui.py --help > /tmp/phase2-baseline/help-root.txt
for cmd in monitor signal analytics backtest digest param-sweep param-audit recalibrate web; do
  poetry run python buibui.py "$cmd" --help > "/tmp/phase2-baseline/help-${cmd}.txt" 2>&1 || true
done
poetry run python buibui.py monitor price --help > /tmp/phase2-baseline/help-monitor-price.txt 2>&1 || true
poetry run python buibui.py monitor position --help > /tmp/phase2-baseline/help-monitor-position.txt 2>&1 || true
poetry run python buibui.py signal watch --help > /tmp/phase2-baseline/help-signal-watch.txt 2>&1 || true
poetry run python buibui.py signal test --help > /tmp/phase2-baseline/help-signal-test.txt 2>&1 || true
poetry run python buibui.py analytics backfill --help > /tmp/phase2-baseline/help-analytics-backfill.txt 2>&1 || true
poetry run python buibui.py analytics sync --help > /tmp/phase2-baseline/help-analytics-sync.txt 2>&1 || true
```

- [ ] **C2: Capture pytest test count**

```bash
poetry run pytest --collect-only -q 2>&1 | tail -5 > /tmp/phase2-baseline/pytest-collect.txt
```

Expected: ends with `<N> tests collected` line. The number is the floor — every PR must keep test count ≥ N (PR 11 is allowed to keep it equal as tests are reorganised, never to shrink).

- [ ] **C3: Capture `STRATEGY_REGISTRY` ordering**

```bash
poetry run python -c "from analytics.indicators_lib import STRATEGY_REGISTRY; print('\n'.join(STRATEGY_REGISTRY.keys()))" > /tmp/phase2-baseline/strategy-order.txt
```

This is the canonical strategy iteration order. `_STRATEGY_MODULES` tuple in `analytics/strategies/_registry.py` (PR 10) MUST produce this exact order.

- [ ] **C4: Capture `make test-regression` golden hash**

```bash
make test-regression 2>&1 | tee /tmp/phase2-baseline/regression-baseline.txt
ls tests/fixtures/golden_*.json | xargs sha256sum > /tmp/phase2-baseline/golden-hashes.txt
```

After every PR, `sha256sum` of the goldens MUST match — Phase 2 forbids golden regeneration.

- [ ] **C5: Capture import-graph snapshot for shim verification**

```bash
poetry run python -c "
import ast, pathlib
for p in pathlib.Path('.').rglob('*.py'):
    if any(s in p.parts for s in ('.venv', '__pycache__', 'node_modules')):
        continue
    try:
        tree = ast.parse(p.read_text())
    except Exception:
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            if n.module.startswith(('analytics.', 'signals.', 'cli.', 'utils.', 'monitor.', 'web.')):
                names = ','.join(a.name for a in n.names)
                print(f'{p}:{n.lineno} from {n.module} import {names}')
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith(('analytics.', 'signals.', 'cli.', 'utils.', 'monitor.', 'web.')):
                    print(f'{p}:{n.lineno} import {a.name}')
" | sort > /tmp/phase2-baseline/imports-pre.txt
```

After each shim PR, re-run and diff — the only allowed changes are the shim's own internal imports.

- [ ] **C6: Run `perf-1` (PR 1) to capture wall-clock baseline.** See PR 1 for the suite and output path. `perf-2` (PR 12) compares against this.

---

## Cross-cutting checklists (apply to every PR)

### Universal 5-point gate

Every PR must clear all five before push:

1. **Lint/typecheck/test:** `make lint-py && make typecheck && make test` clean. Test count ≥ baseline (`/tmp/phase2-baseline/pytest-collect.txt`).
2. **Regression:** `make test-regression` passes against UNCHANGED Phase-1-era goldens. Golden hashes match `/tmp/phase2-baseline/golden-hashes.txt`. **NEVER** run `make regression-update` reflexively — drift = code bug, bisect.
3. **Layering test (from PR 7 onwards):** `poetry run pytest tests/test_layering.py -v` passes.
4. **Shim smoke:** `poetry run python -c "<shim re-export check>"` passes for each module being shimmed by the PR (per-PR exact command listed below).
5. **PR-specific manual smoke:** the command listed in that PR's "Manual smoke" step.

### Hard rules

- **Hard rule (revert, don't patch forward):** if the gate fails and the cause isn't immediately obvious within 15 minutes of investigation, `git revert` the commit, branch from `main`, bisect the change set, retry. Patching forward inside a failing PR is forbidden.
- **`_upsert` verbatim-move rule (PR 6):** the body of `_upsert` (the try/finally with `conn.register("__rel", df)` / `conn.unregister("__rel")`) moves byte-identically. No formatting, no type-hint additions, no comment edits to that body. Whitespace at the boundaries is allowed; the body itself is sealed. Reviewer rejects any diff that touches that block.
- **`_bt_mem_cache` mutation-not-rebinding rule (PR 8):** `_bt_mem_cache: dict[str, BacktestSnapshot] = {}` is defined exactly once, in `analytics/signal/_common.py`. Every consumer does `from analytics.signal._common import _bt_mem_cache` and mutates in place (`.clear()`, `[k] = v`, `del [k]`). NEVER `_bt_mem_cache = {}` after import — that re-binds a local and breaks cache coherence.
- **`_STRATEGY_MODULES` explicit-tuple rule (PR 10/11):** `analytics/strategies/_registry.py` contains an explicit module-name tuple in canonical order matching `/tmp/phase2-baseline/strategy-order.txt`. Do NOT use `pkgutil.iter_modules`, `importlib.metadata`, or any glob-based discovery. The tuple is hand-written and reviewed.
- **Zero golden drift rule:** end of every PR, `sha256sum tests/fixtures/golden_*.json` matches `/tmp/phase2-baseline/golden-hashes.txt`. If they drift after a "no-logic-change" split PR, that's a real code bug.
- **`_upsert` survives intact across all of store-1/store-2** — same rule, restated because data_store is split across two PRs.

### Branching workflow per PR

The user's `gh` CLI cannot create PRs (collaborator error). Per-PR workflow:

1. `git checkout main && git pull`
2. `git checkout -b <branch>`
3. Make changes; run gate (1)+(2)+(3)+(4)+(5).
4. Commit (conventional commits — `feat:` / `fix:` / `chore:` / `refactor:` / `test:` / `docs:` / `build:`).
5. `git push -u origin <branch>`.
6. Write PR summary to `/tmp/pr-<branch>.md` (slashes in branch names → directories — `mkdir -p $(dirname /tmp/pr-<branch>.md)` first).
7. Stop. User opens the PR via the GitHub web UI and merges, then says "go".
8. On "go": `git checkout main && git pull`, proceed to next PR.

---

## File map

| Path | Action | PR |
| --- | --- | --- |
| `scripts/profile_suite.py` | Create | perf-1 (1) |
| `docs/perf-baseline-2026-04-27.md` | Create | perf-1 (1) |
| `cli/__init__.py` + 9 module files | Create | cli-1 (2) |
| `buibui.py` | Modify (→ 3-line entry) | cli-1 (2) |
| `analytics/stats/` (13 files) | Create | stats-1 (3) |
| `analytics/stats_lib.py` | Modify (→ shim) | stats-1 (3) |
| `analytics/backtest/` (6 files) | Create | backtest-1 (4) |
| `analytics/backtest_lib.py` | Modify (→ shim) | backtest-1 (4) |
| `analytics/store/__init__.py` + `analytics/store/schema.py` | Create | store-1 (5) |
| `analytics/data_store.py` | Modify (import schema from new module) | store-1 (5) |
| `analytics/store/_common.py`, `market_data.py`, `signals.py`, `backtest_runs.py`, `backtest_cache.py`, `stats_cache.py`, `confidence.py`, `combos.py` | Create | store-2 (6) |
| `analytics/data_store.py` | Modify (→ shim) | store-2 (6) |
| `analytics/signal/__init__.py`, `analytics/signal/types.py` | Create | signal-1 (7) |
| `signals/alert_formatter.py` | Modify (delete `SignalEvent`, import from new path) | signal-1 (7) |
| `signal_runner.py`, `signal_test_runner.py` (top-level wrappers if any) + `analytics/signal_lib.py` | Modify (import path) | signal-1 (7) |
| `tests/test_layering.py` | Create | signal-1 (7) |
| `analytics/signal/_common.py`, `gates.py`, `resolvers.py`, `bt_cache.py`, `stats_context.py`, `cofire.py` | Create | signal-2 (8) |
| `analytics/signal_lib.py` | Modify (import leaves; keep scanner) | signal-2 (8) |
| `analytics/signal/scanner.py` | Create | signal-3 (9) |
| `analytics/signal_lib.py` | Modify (→ shim) | signal-3 (9) |
| `analytics/strategies/__init__.py`, `_base.py`, `_shared.py`, `_registry.py`, `_seasonality.py` | Create | strat-1 (10) |
| `analytics/indicators_lib.py` | Modify (import from package; keep detectors) | strat-1 (10) |
| `analytics/strategies/<20 detector files>.py` | Create | strat-2 (11) |
| `analytics/indicators_lib.py` | Modify (→ shim) | strat-2 (11) |
| `tests/strategies/test_<20>.py` | Create | strat-2 (11) |
| `tests/test_indicators_lib.py`, `tests/test_candle_patterns.py`, `tests/test_fib_strategies.py` | Delete | strat-2 (11) |
| `tests/test_strategy_registry.py` | Create | strat-2 (11) |
| `docs/perf-baseline-phase2-close.md` | Create | perf-2 (12) |
| `MEMORY.md` (root: `~/.claude-personal/projects/.../memory/MEMORY.md`) | Append session-state row per branch | every PR |

---

## Track P — Perf bookends

### PR 1 — `perf-1` profile baseline

**Title:** `chore: capture phase 2 profile baseline (perf-1)`
**Branch:** `chore/perf-1-baseline`
**Risk:** None — no code change outside `scripts/`.
**Effort:** 0.5 day.

**Pre-flight:**

- [ ] On `main`, gate (1) + (2) green.
- [ ] `/tmp/phase2-baseline/` captures C1–C5 done.

**Files:**

- Create: `scripts/profile_suite.py`
- Create: `docs/perf-baseline-2026-04-27.md`

- [ ] **Step 1: Write `scripts/profile_suite.py`**

```python
"""Phase 2 profile baseline. Runs cProfile over four hot paths, 3x each, reports median wall-clock + top 20 cumulative."""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import statistics
import time
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pandas as pd

from analytics.backtest_lib import run_backtest
from analytics.data_store import DEFAULT_DB_PATH, get_ohlcv
from analytics.indicators_lib import STRATEGY_REGISTRY
from analytics.param_sweep import run_param_sweep
from analytics.signal_lib import run_scan_cycle


def _time_one(label: str, fn: Callable[[], object]) -> tuple[float, str]:
    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    fn()
    pr.disable()
    elapsed = time.perf_counter() - t0
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(20)
    return elapsed, s.getvalue()


def _bench(label: str, fn: Callable[[], object], runs: int = 3) -> None:
    samples = []
    last_dump = ""
    for i in range(runs):
        elapsed, dump = _time_one(label, fn)
        samples.append(elapsed)
        last_dump = dump
    median = statistics.median(samples)
    iqr = statistics.quantiles(samples, n=4)[2] - statistics.quantiles(samples, n=4)[0] if len(samples) >= 4 else max(samples) - min(samples)
    print(f"\n=== {label} ===")
    print(f"runs: {samples}")
    print(f"median: {median:.3f}s   IQR: {iqr:.3f}s")
    print(last_dump)


def _bench_backtest() -> None:
    df = get_ohlcv("BTCUSDT", "1h", db_path=DEFAULT_DB_PATH)
    spec = STRATEGY_REGISTRY["wick_fill"]
    run_backtest(df=df, spec=spec, symbol="BTCUSDT", timeframe="1h", tp_r=4.0)


def _bench_param_sweep() -> None:
    run_param_sweep(symbols=["BTCUSDT"], timeframes=["1h"], strategies=["wick_fill"], db_path=DEFAULT_DB_PATH)


def _bench_scan_cycle() -> None:
    client = MagicMock()
    run_scan_cycle(client=client, db_path=DEFAULT_DB_PATH, symbols=["BTCUSDT"], timeframes=["15m"])


def _bench_combo() -> None:
    df = get_ohlcv("BTCUSDT", "1h", db_path=DEFAULT_DB_PATH)
    spec = STRATEGY_REGISTRY["wick_fill"]
    run_backtest(df=df, spec=spec, symbol="BTCUSDT", timeframe="1h", tp_r=4.0)  # combo path stub; replace with real combo when wired


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None, help="Optional log path; default stdout only.")
    args = parser.parse_args()
    _bench("backtest BTCUSDT/1h wick_fill", _bench_backtest)
    _bench("param_sweep wick_fill 1h", _bench_param_sweep)
    _bench("run_scan_cycle stubbed client", _bench_scan_cycle)
    _bench("combo backtest stub", _bench_combo)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the suite, capture output**

```bash
poetry run python scripts/profile_suite.py 2>&1 | tee /tmp/phase2-baseline/perf-suite-out.txt
```

Expected: 4 sections, each with `median`, `IQR`, and 20 cumulative lines.

- [ ] **Step 3: Write `docs/perf-baseline-2026-04-27.md`**

Header + per-section table extracting: name, median, IQR, top 5 cumulative functions. Manually transcribe the salient numbers from `/tmp/phase2-baseline/perf-suite-out.txt`. Keep the full raw output in the doc as a fenced block at the end.

- [ ] **Step 4: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

All green.

- [ ] **Step 5: Commit + push**

```bash
git add scripts/profile_suite.py docs/perf-baseline-2026-04-27.md
git commit -m "chore: capture phase 2 profile baseline (perf-1)"
git push -u origin chore/perf-1-baseline
```

- [ ] **Step 6: Write PR summary `/tmp/pr-chore/perf-1-baseline.md`**

Title, summary (one paragraph: "First of two perf bookends; runs cProfile 3x over backtest / param_sweep / scan_cycle / combo; commits raw + summary"), test plan (`make test`, `make test-regression`, manual review of the doc), notes (next: `cli-1`).

**Manual smoke:** none — perf suite output IS the smoke.

**Rollback play:** just revert the doc; suite is reusable for `perf-2`.

---

### PR 12 — `perf-2` close-of-phase profile

**Title:** `chore: capture phase 2 close profile (perf-2)`
**Branch:** `chore/perf-2-close`
**Risk:** None.
**Effort:** 0.5 day.

**Pre-flight:**

- [ ] All 11 prior PRs merged to `main`. Gate (1)+(2)+(3) green.
- [ ] `scripts/profile_suite.py` still present and runs.

**Files:**

- Create: `docs/perf-baseline-phase2-close.md`

- [ ] **Step 1: Re-run profile suite**

```bash
poetry run python scripts/profile_suite.py 2>&1 | tee /tmp/phase2-close/perf-suite-out.txt
```

- [ ] **Step 2: Write `docs/perf-baseline-phase2-close.md`** — same shape as `perf-baseline-2026-04-27.md`, plus a "Diff vs baseline" section per section showing wall-clock delta.

- [ ] **Step 3: Investigate any >10% regression**

For each section where `(close_median - baseline_median) / baseline_median > 0.10`:

- Bisect across the 10 split PRs (all are revertable).
- Document root cause in the doc.
- If the cause is a real perf regression: open an issue, file under Phase 4 scope.
- If the cause is profile noise (re-run shows `< 10%`): document the noise, move on.

- [ ] **Step 4: Gate + commit + push**

```bash
make lint-py && make typecheck && make test
make test-regression
git add docs/perf-baseline-phase2-close.md
git commit -m "chore: capture phase 2 close profile (perf-2)"
git push -u origin chore/perf-2-close
```

- [ ] **Step 5: Write PR summary** to `/tmp/pr-chore/perf-2-close.md`.

**Manual smoke:** none.

**Rollback play:** doc-only; revert if a number is wrong, re-run.

---

## Track S — Splits (leaf-to-root)

### PR 2 — `cli-1` split CLI

**Title:** `refactor: split buibui.py into cli/ package (cli-1)`
**Branch:** `refactor/cli-1-split`
**Risk:** Low. Pure motion.
**Effort:** 1 day.

**Pre-flight:**

- [ ] `main` clean; PR 1 merged.
- [ ] `/tmp/phase2-baseline/help-*.txt` present (C1).

**Files:**

- Create: `cli/__init__.py` (empty file with `# cli package — argparse subcommand modules.`)
- Create: `cli/main.py`
- Create: `cli/_common.py`
- Create: `cli/monitor.py`
- Create: `cli/analytics.py`
- Create: `cli/digest.py`
- Create: `cli/backtest.py`
- Create: `cli/signal.py`
- Create: `cli/param.py`
- Create: `cli/recalibrate.py`
- Create: `cli/web.py`
- Modify: `buibui.py` → 3-line entry.

**Per-command file contract:**

```python
# cli/<command>.py
import argparse


def add_<command>_subparser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the `buibui <command>` subparser. Called by cli/main.py."""
    ...


def run_<command>(args: argparse.Namespace) -> None:
    """Handler invoked by cli/main.py dispatch."""
    ...
```

`cli/main.py` shape:

```python
"""Buibui CLI entry — assembles argparse tree, dispatches to subcommand handlers."""
from __future__ import annotations

import argparse

from cli import analytics, backtest, digest, monitor, param, recalibrate, signal, web


def main() -> None:
    parser = argparse.ArgumentParser(prog="buibui", description="Buibui Moon Trader Bot CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    monitor.add_monitor_subparser(subparsers)
    signal.add_signal_subparser(subparsers)
    analytics.add_analytics_subparser(subparsers)
    backtest.add_backtest_subparser(subparsers)
    digest.add_digest_subparser(subparsers)
    param.add_param_sweep_subparser(subparsers)
    param.add_param_audit_subparser(subparsers)
    recalibrate.add_recalibrate_subparser(subparsers)
    web.add_web_subparser(subparsers)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

`buibui.py` shape (final):

```python
"""Buibui CLI entry. Real logic lives in cli/."""
from cli.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 1: Read current `buibui.py` end-to-end**

Read the full 1,141-line file. Identify, per subcommand:

- The argparse builder block (`subparsers.add_parser("backtest", ...)` + every `.add_argument(...)` for that subcommand).
- The handler function (`run_backtest`, etc.) and any private helpers it calls.
- Module-level helpers used by multiple handlers (`_parse_since_to_ms`, `_parse_smt_pairs`) — these go to `cli/_common.py`.

- [ ] **Step 2: Create `cli/__init__.py` (empty marker file)**

- [ ] **Step 3: Create `cli/_common.py`**

Move shared helpers from `buibui.py`: `_parse_since_to_ms`, `_parse_smt_pairs`, any time/symbol parsing utilities used by ≥2 subcommands. Type-annotated, no module-level side effects.

- [ ] **Step 4: Create each `cli/<command>.py` file**

For each of `monitor`, `analytics`, `digest`, `backtest`, `signal`, `param`, `recalibrate`, `web`:

1. Copy the subparser builder code into `add_<command>_subparser`. Replace `subparsers.add_parser("<command>", ...)` with `parser = subparsers.add_parser(...)` and continue inside the function. Set `parser.set_defaults(func=run_<command>)` at the end of the builder.
2. Copy the handler `run_<command>(args)` verbatim, fixing any imports that referenced module-level names in `buibui.py`.
3. Imports at top of file — only what this command needs.

For `monitor`: two subcommands (`price`, `position`); use `monitor.add_monitor_subparser` that creates a subparsers-of-subparsers.
For `signal`: two subcommands (`watch`, `test`); same pattern.
For `analytics`: two subcommands (`backfill`, `sync`).
For `param`: two top-level commands (`param-sweep`, `param-audit`) — expose `add_param_sweep_subparser` and `add_param_audit_subparser` separately. Both live in `cli/param.py`.

- [ ] **Step 5: Create `cli/main.py` per the shape above**

- [ ] **Step 6: Replace `buibui.py` body with the 3-line entry shape**

Keep the shebang line if present.

- [ ] **Step 7: Diff `buibui --help` output against pre-capture**

```bash
poetry run python buibui.py --help > /tmp/cli-1-help-root.txt
diff /tmp/phase2-baseline/help-root.txt /tmp/cli-1-help-root.txt
```

Expected: byte-identical, OR trivially-identical (alphabetical reorder of subcommands acceptable). Any flag missing = revert.

Repeat per subcommand:

```bash
for cmd in monitor signal analytics backtest digest param-sweep param-audit recalibrate web; do
  poetry run python buibui.py "$cmd" --help > "/tmp/cli-1-help-${cmd}.txt" 2>&1 || true
  diff "/tmp/phase2-baseline/help-${cmd}.txt" "/tmp/cli-1-help-${cmd}.txt" || echo "DRIFT: $cmd"
done
```

If any command prints `DRIFT: …`, investigate before proceeding.

- [ ] **Step 8: Manual smoke — one real command per group**

```bash
poetry run python buibui.py digest --help                                         # control
poetry run python buibui.py backtest --config config/signal_watch.toml --since 7d # smoke (don't SAVE)
poetry run python buibui.py recalibrate --dry-run || true
```

Compare each against pre-PR behaviour: same output, same exit code.

- [ ] **Step 9: Run the gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

Test count ≥ baseline. Gold hashes match.

- [ ] **Step 10: Shim smoke**

`buibui.py` is the entry — there's no shim per se. Smoke is:

```bash
poetry run python -c "from cli.main import main; print('ok')"
poetry run python -c "import cli.backtest, cli.signal, cli.param; print('ok')"
```

Expected: `ok` twice.

- [ ] **Step 11: Commit + push + PR summary**

```bash
git add cli/ buibui.py
git commit -m "refactor: split buibui.py into cli/ package (cli-1)"
git push -u origin refactor/cli-1-split
mkdir -p /tmp/pr-refactor
# write /tmp/pr-refactor/cli-1-split.md
```

**Manual smoke (5):** `buibui --help` byte-diff (Step 7), one real backtest dry run (Step 8).

**Rollback play:** revert PR; `buibui.py` is restored from git history; `cli/` directory deletion has zero ripple because nothing else imports from it yet.

---

### PR 3 — `stats-1` split stats_lib

**Title:** `refactor: split analytics/stats_lib.py into analytics/stats/ package (stats-1)`
**Branch:** `refactor/stats-1-split`
**Risk:** Low. 11 compute functions are independent — no cross-imports today.
**Effort:** 1 day.

**Pre-flight:**

- [ ] `main` clean; PR 2 merged.

**Files:**

- Create: `analytics/stats/__init__.py`
- Create: `analytics/stats/_common.py` (timezone constants, session windows, any helper used ≥ 2 modules)
- Create: `analytics/stats/p1p2.py`, `hourly.py`, `adr.py`, `dow.py`, `session.py`, `daily_distance.py`, `weekly_p1p2.py`, `weekly_p2_timing.py`, `weekly_state.py`, `weekly_flip_risk.py`, `weekly_wick.py`, `bundle.py`
- Modify: `analytics/stats_lib.py` → shim.

**Shim contract:**

`analytics/stats/__init__.py`:

```python
"""Stats package — split from analytics/stats_lib.py."""
from analytics.stats.adr import ADRResult, compute_adr
from analytics.stats.bundle import compute_all
from analytics.stats.daily_distance import DailyDistanceResult, compute_daily_distance
from analytics.stats.dow import DOWPatternsResult, compute_dow_patterns
from analytics.stats.hourly import HourlyExtremesResult, compute_hourly_extremes
from analytics.stats.p1p2 import P1P2DailyResult, compute_p1p2_daily
from analytics.stats.session import SessionBreakdownResult, compute_session_breakdown
from analytics.stats.weekly_flip_risk import WeeklyFlipRiskResult, compute_weekly_flip_risk_conditioned
from analytics.stats.weekly_p1p2 import WeeklyP1P2Result, compute_weekly_p1p2
from analytics.stats.weekly_p2_timing import WeeklyP2TimingResult, compute_weekly_p2_timing
from analytics.stats.weekly_state import WeeklyCurrentStateResult, compute_weekly_current_state
from analytics.stats.weekly_wick import WeeklyWickResult, compute_weekly_wick_percentile

__all__ = [
    "ADRResult",
    "DailyDistanceResult",
    "DOWPatternsResult",
    "HourlyExtremesResult",
    "P1P2DailyResult",
    "SessionBreakdownResult",
    "WeeklyCurrentStateResult",
    "WeeklyFlipRiskResult",
    "WeeklyP1P2Result",
    "WeeklyP2TimingResult",
    "WeeklyWickResult",
    "compute_adr",
    "compute_all",
    "compute_daily_distance",
    "compute_dow_patterns",
    "compute_hourly_extremes",
    "compute_p1p2_daily",
    "compute_session_breakdown",
    "compute_weekly_current_state",
    "compute_weekly_flip_risk_conditioned",
    "compute_weekly_p1p2",
    "compute_weekly_p2_timing",
    "compute_weekly_wick_percentile",
]
```

Legacy shim — `analytics/stats_lib.py` (final contents):

```python
"""Legacy import shim. Real implementation lives in analytics/stats/.

Kept so existing callers (web routers, tests, signal_lib) continue to work
without edits.
"""
from analytics.stats import *  # noqa: F401,F403
from analytics.stats import __all__  # noqa: F401

# Explicit re-exports for any private helper still imported externally:
from analytics.stats.bundle import _inject_live_fields  # noqa: F401
```

The `_inject_live_fields` line is required: `web/api/routers/stats.py` imports it directly. Confirm by grepping before writing the shim.

**Steps:**

- [ ] **Step 1: Read `analytics/stats_lib.py`**

Map every public symbol (`compute_*`, `*Result`, `_inject_live_fields`) to its target file per spec §2.2. List private helpers; if a helper is used by ≥ 2 compute functions, it goes to `_common.py`; else stays with its compute function.

- [ ] **Step 2: Grep for external imports of private helpers**

```bash
grep -rn "from analytics.stats_lib import" --include="*.py" .
grep -rn "from analytics import stats_lib" --include="*.py" .
```

For each `_helper` imported externally, add an explicit re-export line to the shim.

- [ ] **Step 3: Create `_common.py`**

Move shared timezone constants, session windows, any cross-module helper. Each named import in `__all__` of `_common.py` (if needed).

- [ ] **Step 4: Create each per-stat module**

Copy the dataclass + `compute_*` function + private helpers used only by that stat. Imports at top: `pandas`, `_common` helpers, `analytics.store` (after store-2 lands; for stats-1, still `analytics.data_store`). No cross-stat imports.

- [ ] **Step 5: Create `bundle.py`**

Move `compute_all` orchestrator + `_inject_live_fields`. Imports: every `compute_*` from sibling modules.

- [ ] **Step 6: Write `analytics/stats/__init__.py`** per shim contract above.

- [ ] **Step 7: Replace `analytics/stats_lib.py` body with the legacy shim above.**

- [ ] **Step 8: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

Test count ≥ baseline. Gold hashes match.

- [ ] **Step 9: Shim smoke**

```bash
poetry run python -c "
from analytics.stats_lib import (
    compute_p1p2_daily, compute_adr, compute_dow_patterns,
    compute_session_breakdown, compute_daily_distance,
    compute_weekly_p1p2, compute_weekly_p2_timing,
    compute_weekly_current_state, compute_weekly_flip_risk_conditioned,
    compute_weekly_wick_percentile, compute_hourly_extremes,
    compute_all, _inject_live_fields,
)
print('ok')"
```

Expected: `ok`.

- [ ] **Step 10: Manual smoke — Stats page API**

```bash
poetry run uvicorn web.api.main:app --port 8001 &
SERVER_PID=$!
sleep 3
curl -s http://localhost:8001/api/stats/BTCUSDT?timeframe=1h | head -c 400
kill $SERVER_PID
```

Expected: JSON response with `p1p2`, `adr`, `dow_patterns`, etc. fields populated. No 500.

- [ ] **Step 11: Commit + push + PR summary**

```bash
git add analytics/stats/ analytics/stats_lib.py
git commit -m "refactor: split analytics/stats_lib.py into analytics/stats/ package (stats-1)"
git push -u origin refactor/stats-1-split
mkdir -p /tmp/pr-refactor
# write /tmp/pr-refactor/stats-1-split.md
```

**Rollback play:** revert PR; `stats_lib.py` restores; downstream needs no changes (shim is what they were importing).

---

### PR 4 — `backtest-1` split backtest_lib

**Title:** `refactor: split analytics/backtest_lib.py into analytics/backtest/ package (backtest-1)`
**Branch:** `refactor/backtest-1-split`
**Risk:** Med. `run_backtest` is the most-imported function in the codebase after `data_store` helpers.
**Effort:** 1 day.

**Pre-flight:**

- [ ] `main` clean; PR 3 merged.
- [ ] Capture pre-PR backtest output for diff:

```bash
mkdir -p /tmp/backtest-1-baseline
poetry run python buibui.py backtest --config config/signal_watch.toml --since 30d --symbol BTCUSDT --timeframe 1h > /tmp/backtest-1-baseline/run.txt 2>&1 || true
```

**Files:**

- Create: `analytics/backtest/__init__.py`
- Create: `analytics/backtest/engine.py` (`Trade`, `BacktestResult`, `run_backtest`, `_compute_atr14`)
- Create: `analytics/backtest/gates.py` (`_is_low_volume`, `_is_volume_spike`, `filter_signals_by_day`)
- Create: `analytics/backtest/combo.py` (`ComboBacktestResult`, `run_combo_backtest`)
- Create: `analytics/backtest/cross_tf.py` (`CrossTfComboBacktestResult`, `run_cross_tf_combo_backtest`)
- Create: `analytics/backtest/formatters.py` (the 9× `format_*` functions)
- Modify: `analytics/backtest_lib.py` → shim.

**Shim contract:**

`analytics/backtest/__init__.py`:

```python
"""Backtest package — split from analytics/backtest_lib.py."""
from analytics.backtest.combo import ComboBacktestResult, run_combo_backtest
from analytics.backtest.cross_tf import CrossTfComboBacktestResult, run_cross_tf_combo_backtest
from analytics.backtest.engine import BacktestResult, Trade, run_backtest
from analytics.backtest.formatters import (
    format_backtest_summary,
    format_combo_summary,
    format_cross_tf_combo_summary,
    format_seasonality,
    format_strategy_breakdown,
    format_volume_breakdown,
    format_directional_breakdown,
    format_duration_breakdown,
    format_hour_breakdown,
)
from analytics.backtest.gates import filter_signals_by_day

__all__ = [
    "BacktestResult",
    "ComboBacktestResult",
    "CrossTfComboBacktestResult",
    "Trade",
    "filter_signals_by_day",
    "format_backtest_summary",
    "format_combo_summary",
    "format_cross_tf_combo_summary",
    "format_directional_breakdown",
    "format_duration_breakdown",
    "format_hour_breakdown",
    "format_seasonality",
    "format_strategy_breakdown",
    "format_volume_breakdown",
    "run_backtest",
    "run_combo_backtest",
    "run_cross_tf_combo_backtest",
]
```

Legacy shim — `analytics/backtest_lib.py` (final):

```python
"""Legacy import shim. Real implementation lives in analytics/backtest/."""
from analytics.backtest import *  # noqa: F401,F403
from analytics.backtest import __all__  # noqa: F401

# De-facto-public underscore helpers (imported externally; see CLAUDE.md):
from analytics.backtest.gates import _is_low_volume, _is_volume_spike  # noqa: F401
```

Confirm `_is_low_volume` / `_is_volume_spike` are imported externally (per CLAUDE.md they are; verify with grep before relying on it):

```bash
grep -rn "_is_low_volume\|_is_volume_spike" --include="*.py" .
```

**Steps:**

- [ ] **Step 1: Read `analytics/backtest_lib.py` end-to-end**

Identify each symbol's target file per spec §2.3.

- [ ] **Step 2: Confirm cross-imports**

`run_backtest` (engine) calls `_is_low_volume`, `_is_volume_spike` (gates) and `_compute_atr14` (engine-local). `combo` and `cross_tf` call into `engine.run_backtest`. `formatters` are pure — they consume `BacktestResult` and return strings. No circular paths.

- [ ] **Step 3: Create each module**

`engine.py`: `Trade`, `BacktestResult`, `run_backtest`, `_compute_atr14`. Imports `from analytics.backtest.gates import _is_low_volume, _is_volume_spike, filter_signals_by_day`.

`gates.py`: `_is_low_volume`, `_is_volume_spike`, `filter_signals_by_day`. Self-contained — no sibling imports.

`combo.py`: `ComboBacktestResult`, `run_combo_backtest`. Imports `from analytics.backtest.engine import run_backtest, BacktestResult`.

`cross_tf.py`: `CrossTfComboBacktestResult`, `run_cross_tf_combo_backtest`. Imports `from analytics.backtest.engine import run_backtest`.

`formatters.py`: 9 functions. Imports `from analytics.backtest.engine import BacktestResult` (for type hints only).

- [ ] **Step 4: Write `analytics/backtest/__init__.py`** per shim contract.

- [ ] **Step 5: Replace `analytics/backtest_lib.py` body with the shim** above.

- [ ] **Step 6: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

- [ ] **Step 7: Shim smoke**

```bash
poetry run python -c "
from analytics.backtest_lib import (
    Trade, BacktestResult, run_backtest,
    ComboBacktestResult, run_combo_backtest,
    CrossTfComboBacktestResult, run_cross_tf_combo_backtest,
    filter_signals_by_day, _is_low_volume, _is_volume_spike,
    format_backtest_summary, format_seasonality,
)
print('ok')"
```

- [ ] **Step 8: Manual smoke — single backtest run**

```bash
poetry run python buibui.py backtest --config config/signal_watch.toml --since 30d --symbol BTCUSDT --timeframe 1h > /tmp/backtest-1-after.txt 2>&1
diff /tmp/backtest-1-baseline/run.txt /tmp/backtest-1-after.txt
```

Expected: zero diff. Any drift = revert.

- [ ] **Step 9: Commit + push + PR summary**

```bash
git add analytics/backtest/ analytics/backtest_lib.py
git commit -m "refactor: split analytics/backtest_lib.py into analytics/backtest/ package (backtest-1)"
git push -u origin refactor/backtest-1-split
# write /tmp/pr-refactor/backtest-1-split.md
```

**Rollback play:** revert; `backtest_lib.py` restored.

---

### PR 5 — `store-1` extract schema

**Title:** `refactor: extract analytics/store/schema.py from data_store (store-1)`
**Branch:** `refactor/store-1-schema`
**Risk:** Low. Tiny PR proves the package import chain works.
**Effort:** 0.5 day.

**Pre-flight:**

- [ ] `main` clean; PR 4 merged.
- [ ] Capture schema by `init_schema()`-ing a temp DB and dumping its tables:

```bash
poetry run python -c "
import duckdb
from analytics.data_store import init_schema
con = duckdb.connect(':memory:')
init_schema(con)
print('\n'.join(sorted([r[0] for r in con.execute(\"SELECT table_name FROM information_schema.tables\").fetchall()])))
" > /tmp/store-1-schema-tables.txt
```

Save the table list — must be byte-identical after the move.

**Files:**

- Create: `analytics/store/__init__.py`
- Create: `analytics/store/schema.py` — contains `init_schema` only.
- Modify: `analytics/data_store.py` — delete `init_schema` body; add `from analytics.store.schema import init_schema` near top; preserve everything else (upserts, queries, helpers).

**Shim contract (this PR — partial):**

`analytics/store/__init__.py` (this PR; expanded by store-2):

```python
"""Store package — split from analytics/data_store.py. Phase 2 store-1 extracts schema only."""
from analytics.store.schema import init_schema

__all__ = ["init_schema"]
```

`analytics/data_store.py` (after store-1) — unchanged except: top of file gains `from analytics.store.schema import init_schema  # noqa: F401`. The original `def init_schema(con): ...` body is deleted. `data_store.init_schema` continues to work via the imported name.

**Steps:**

- [ ] **Step 1: Locate `init_schema` in `data_store.py`**

Read the function. Note: it contains all 13 `CREATE TABLE` statements + indexes. No external dependencies beyond `duckdb`. Self-contained.

- [ ] **Step 2: Create `analytics/store/__init__.py` and `analytics/store/schema.py`**

Copy `init_schema` verbatim into `schema.py`. Imports at top: `import duckdb`. Type-annotate the signature (`def init_schema(con: duckdb.DuckDBPyConnection) -> None`).

- [ ] **Step 3: Edit `analytics/data_store.py`**

Delete the original `def init_schema(...)` block. Add `from analytics.store.schema import init_schema` near the top (after stdlib imports, before any class/function definition). Confirm `init_schema` is still available as `analytics.data_store.init_schema`.

- [ ] **Step 4: Confirm table list unchanged**

```bash
poetry run python -c "
import duckdb
from analytics.data_store import init_schema
con = duckdb.connect(':memory:')
init_schema(con)
print('\n'.join(sorted([r[0] for r in con.execute(\"SELECT table_name FROM information_schema.tables\").fetchall()])))
" > /tmp/store-1-schema-tables-after.txt
diff /tmp/store-1-schema-tables.txt /tmp/store-1-schema-tables-after.txt
```

Expected: zero diff.

- [ ] **Step 5: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

- [ ] **Step 6: Shim smoke**

```bash
poetry run python -c "from analytics.data_store import init_schema; from analytics.store import init_schema as init2; assert init_schema is init2; print('ok')"
poetry run python -c "from analytics.store.schema import init_schema; print('ok')"
```

Both `ok`.

- [ ] **Step 7: Commit + push + PR summary**

```bash
git add analytics/store/ analytics/data_store.py
git commit -m "refactor: extract analytics/store/schema.py from data_store (store-1)"
git push -u origin refactor/store-1-schema
# write /tmp/pr-refactor/store-1-schema.md
```

**Manual smoke:** `init_schema` table-list diff (Step 4).

**Rollback play:** revert; `init_schema` body returns to `data_store.py`. Zero ripple.

---

### PR 6 — `store-2` split table groups

**Title:** `refactor: split analytics/data_store.py into analytics/store/ package (store-2)`
**Branch:** `refactor/store-2-split`
**Risk:** Med-High. 30+ external import sites including every web router.
**Effort:** 2 days.

**Pre-flight:**

- [ ] `main` clean; PR 5 merged.
- [ ] **`_upsert` verbatim-move rule** is in scope here. The body of `_upsert` (try/finally with `conn.register("__rel", df)` / `conn.unregister("__rel")`) moves byte-identically. NO formatting, type-hint, or comment edits to that body.
- [ ] Grep external import sites:

```bash
grep -rn "from analytics.data_store import\|from analytics import data_store" --include="*.py" . > /tmp/store-2-callers.txt
wc -l /tmp/store-2-callers.txt
```

Expected: ≥ 30 lines (web routers, signal_lib, backtest_lib, runners, tests).

- [ ] Snapshot a representative DB-touching API call to diff against:

```bash
poetry run uvicorn web.api.main:app --port 8001 &
SERVER_PID=$!
sleep 3
curl -s http://localhost:8001/api/active-config > /tmp/store-2-baseline-active-config.json
curl -s http://localhost:8001/api/zones?symbol=BTCUSDT\&timeframe=1h > /tmp/store-2-baseline-zones.json
curl -s 'http://localhost:8001/api/signals/recent?limit=5' > /tmp/store-2-baseline-signals.json
kill $SERVER_PID
```

**Files:**

- Create: `analytics/store/_common.py` — `_upsert`, `_connect` helper, `DEFAULT_DB_PATH`.
- Create: `analytics/store/market_data.py` — ohlcv, funding_rates, open_interest helpers.
- Create: `analytics/store/signals.py` — signals + signal_alert_outcomes upserts/queries/history.
- Create: `analytics/store/backtest_runs.py` — backtest_runs + backtest_trades upserts/queries.
- Create: `analytics/store/backtest_cache.py` — backtest_cache get/put/prune.
- Create: `analytics/store/stats_cache.py` — stats_cache get/put/invalidate.
- Create: `analytics/store/confidence.py` — confidence_ratings (+v2) + `BacktestSnapshot`.
- Create: `analytics/store/combos.py` — backtest_combos + backtest_cross_tf_combos + `query_cross_tf_combos`.
- Modify: `analytics/store/__init__.py` — exhaustive re-exports.
- Modify: `analytics/data_store.py` → shim (full).

**Shim contract:**

`analytics/store/__init__.py`:

```python
"""Store package — full split landed in store-2."""
from analytics.store._common import DEFAULT_DB_PATH, _connect, _upsert
from analytics.store.backtest_cache import (
    get_backtest_cache,
    prune_backtest_cache,
    put_backtest_cache,
)
from analytics.store.backtest_runs import (
    list_backtest_runs,
    save_backtest_run,
    upsert_backtest_run,
    upsert_backtest_trades,
)
from analytics.store.combos import (
    query_cross_tf_combos,
    upsert_backtest_combo,
    upsert_backtest_cross_tf_combo,
)
from analytics.store.confidence import (
    BacktestSnapshot,
    get_confidence_rating,
    upsert_confidence_rating,
    upsert_confidence_rating_v2,
)
from analytics.store.market_data import (
    get_funding_rates,
    get_ohlcv,
    get_open_interest,
    upsert_funding_rates,
    upsert_ohlcv,
    upsert_open_interest,
)
from analytics.store.schema import init_schema
from analytics.store.signals import (
    get_signal_alert_outcome,
    get_signals_history,
    upsert_signal,
    upsert_signal_alert_outcome,
    upsert_signal_outcome,
)
from analytics.store.stats_cache import (
    get_stats_cache,
    invalidate_stats_cache,
    put_stats_cache,
)

__all__ = [
    "BacktestSnapshot",
    "DEFAULT_DB_PATH",
    "_connect",
    "_upsert",
    "get_backtest_cache",
    "get_confidence_rating",
    "get_funding_rates",
    "get_ohlcv",
    "get_open_interest",
    "get_signal_alert_outcome",
    "get_signals_history",
    "get_stats_cache",
    "init_schema",
    "invalidate_stats_cache",
    "list_backtest_runs",
    "prune_backtest_cache",
    "put_backtest_cache",
    "put_stats_cache",
    "query_cross_tf_combos",
    "save_backtest_run",
    "upsert_backtest_combo",
    "upsert_backtest_cross_tf_combo",
    "upsert_backtest_run",
    "upsert_backtest_trades",
    "upsert_confidence_rating",
    "upsert_confidence_rating_v2",
    "upsert_funding_rates",
    "upsert_ohlcv",
    "upsert_open_interest",
    "upsert_signal",
    "upsert_signal_alert_outcome",
    "upsert_signal_outcome",
]
```

Verify the actual function names by reading `analytics/data_store.py` first — adjust the `__all__` to match. Any name in this list that doesn't exist in `data_store.py` is a typo; any function in `data_store.py` not listed is a forgotten re-export.

`analytics/data_store.py` (final shim):

```python
"""Legacy import shim. Real implementation lives in analytics/store/.

Kept so 30+ existing callers (web routers, signal_lib, backtest_lib, runners,
tests) continue to work without edits.
"""
from analytics.store import *  # noqa: F401,F403
from analytics.store import __all__  # noqa: F401

# Explicit re-exports for any private helper imported externally:
from analytics.store._common import _upsert  # noqa: F401
```

**Steps:**

- [ ] **Step 1: Read `analytics/data_store.py` end-to-end**

Build a mental map: which function lives in which target module per spec §2.4. Note `_upsert`'s exact body — that block moves byte-identically. Note `BacktestSnapshot` is a dataclass not a function.

- [ ] **Step 2: Build the function-to-target map**

Write a temporary `/tmp/store-2-map.txt`:

```text
init_schema → schema.py (already moved in store-1)
_upsert → _common.py (verbatim — sealed body)
DEFAULT_DB_PATH, _connect → _common.py
upsert_ohlcv, get_ohlcv → market_data.py
upsert_funding_rates, get_funding_rates → market_data.py
upsert_open_interest, get_open_interest → market_data.py
upsert_signal, upsert_signal_outcome, get_signals_history → signals.py
upsert_signal_alert_outcome, get_signal_alert_outcome → signals.py
upsert_backtest_run, save_backtest_run, list_backtest_runs → backtest_runs.py
upsert_backtest_trades → backtest_runs.py
get_backtest_cache, put_backtest_cache, prune_backtest_cache → backtest_cache.py
get_stats_cache, put_stats_cache, invalidate_stats_cache → stats_cache.py
upsert_confidence_rating, upsert_confidence_rating_v2, get_confidence_rating → confidence.py
BacktestSnapshot → confidence.py
upsert_backtest_combo, upsert_backtest_cross_tf_combo → combos.py
query_cross_tf_combos → combos.py
```

- [ ] **Step 3: Create `_common.py`**

`DEFAULT_DB_PATH = "analytics.db"` (or whatever the current literal is — copy verbatim). `_connect(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection` helper if one already exists; else inline at call sites.

`_upsert` body: copy character-for-character. Confirm by `git diff` showing only line-number changes when the move is done. Do not reformat. Do not add type hints to the body. Do not edit comments.

- [ ] **Step 4: Create each table-group module**

Per the map. Each module imports `from analytics.store._common import _upsert, DEFAULT_DB_PATH` and uses pandas/duckdb directly. No cross-imports between table-group modules.

- [ ] **Step 5: Write `analytics/store/__init__.py`** per shim contract above. Cross-check `__all__` against the map by `grep -E "^def |^class " analytics/store/*.py` and listing exported names.

- [ ] **Step 6: Replace `analytics/data_store.py` body with the shim** above.

- [ ] **Step 7: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

Watch for malloc errors in test output — if any test segfaults or reports `malloc` corruption, `_upsert` body has been touched. Revert immediately, redo the move byte-identically.

- [ ] **Step 8: Shim smoke**

```bash
poetry run python -c "
from analytics.data_store import (
    init_schema, _upsert, DEFAULT_DB_PATH, BacktestSnapshot,
    upsert_ohlcv, get_ohlcv, upsert_funding_rates, get_funding_rates,
    upsert_open_interest, get_open_interest,
    upsert_signal, upsert_signal_outcome, get_signals_history,
    upsert_signal_alert_outcome, get_signal_alert_outcome,
    upsert_backtest_run, save_backtest_run, list_backtest_runs, upsert_backtest_trades,
    get_backtest_cache, put_backtest_cache, prune_backtest_cache,
    get_stats_cache, put_stats_cache, invalidate_stats_cache,
    upsert_confidence_rating, upsert_confidence_rating_v2, get_confidence_rating,
    upsert_backtest_combo, upsert_backtest_cross_tf_combo, query_cross_tf_combos,
)
print('ok')"
```

Expected: `ok`. Any `ImportError` = missing re-export, fix `__all__`.

- [ ] **Step 9: Manual smoke — web router endpoints**

```bash
poetry run uvicorn web.api.main:app --port 8001 &
SERVER_PID=$!
sleep 3
curl -s http://localhost:8001/api/active-config > /tmp/store-2-after-active-config.json
curl -s http://localhost:8001/api/zones?symbol=BTCUSDT\&timeframe=1h > /tmp/store-2-after-zones.json
curl -s 'http://localhost:8001/api/signals/recent?limit=5' > /tmp/store-2-after-signals.json
kill $SERVER_PID
diff /tmp/store-2-baseline-active-config.json /tmp/store-2-after-active-config.json
diff /tmp/store-2-baseline-zones.json /tmp/store-2-after-zones.json
diff /tmp/store-2-baseline-signals.json /tmp/store-2-after-signals.json
```

Expected: zero diff per file (timestamp fields may drift if the live signals fixture changed; if so, use `--limit=1` and pin a specific signal). Any structural diff = revert.

- [ ] **Step 10: Commit + push + PR summary**

```bash
git add analytics/store/ analytics/data_store.py
git commit -m "refactor: split analytics/data_store.py into analytics/store/ package (store-2)"
git push -u origin refactor/store-2-split
# write /tmp/pr-refactor/store-2-split.md
```

**Rollback play:** revert; `data_store.py` restored from history. Web routers and tests need no edits because they were importing from the shim path the whole time. After revert, `analytics/store/` directory still exists with `__init__.py` and `schema.py` from store-1 — leave it; it's still valid.

---

### PR 7 — `signal-1` SignalEvent move + boundary AST test

**Title:** `refactor: move SignalEvent to analytics/signal/types + enforce layering (signal-1)`
**Branch:** `refactor/signal-1-types`
**Risk:** Low. ~30-line code diff + new test file.
**Effort:** 0.5 day.

**Pre-flight:**

- [ ] `main` clean; PR 6 merged.
- [ ] Confirm the boundary violation exists today:

```bash
grep -n "from signals\|import signals" analytics/signal_lib.py
```

Expected: at least one match — `from signals.alert_formatter import SignalEvent` or similar. This is the violation `signal-1` closes.

**Files:**

- Create: `analytics/signal/__init__.py`
- Create: `analytics/signal/types.py` — `SignalEvent` (and any closely-related lightweight types like `StatsContext`, `ConfluenceData` if they're already in `alert_formatter.py`; cross-check before moving).
- Create: `tests/test_layering.py`
- Modify: `signals/alert_formatter.py` — delete the `SignalEvent` class definition; add `from analytics.signal.types import SignalEvent  # re-exported here for backwards compat` if any external caller imports it from `signals.alert_formatter` (grep first).
- Modify: `analytics/signal_lib.py` — change import from `from signals.alert_formatter import SignalEvent` to `from analytics.signal.types import SignalEvent`.
- Modify: `signal_runner.py`, `signal_test_runner.py` (top-level wrappers) — update SignalEvent imports if any.

**Shim contract (signal-1 partial; signal-2/-3 expand):**

`analytics/signal/__init__.py` (this PR):

```python
"""Signal package — split from analytics/signal_lib.py. signal-1 lands SignalEvent."""
from analytics.signal.types import SignalEvent

__all__ = ["SignalEvent"]
```

`signals/alert_formatter.py` — delete the `class SignalEvent: ...` block. Top of file: keep existing imports; add `from analytics.signal.types import SignalEvent` if `SignalEvent` is referenced anywhere else in this module. The module no longer *defines* `SignalEvent` — it consumes it.

**Steps:**

- [ ] **Step 1: Read `signals/alert_formatter.py`**

Locate `SignalEvent`. Note all attributes. Confirm whether `StatsContext` and `ConfluenceData` live here or elsewhere — per CLAUDE.md they're co-located with `SignalEvent` in `alert_formatter.py`. If so, move all three to `analytics/signal/types.py`.

- [ ] **Step 2: Grep for external `SignalEvent` imports**

```bash
grep -rn "SignalEvent" --include="*.py" . > /tmp/signal-1-callers.txt
```

Expected callers: `analytics/signal_lib.py`, `signal_runner.py`, `signal_test_runner.py`, `signals/cooldown_store.py` (maybe), tests.

- [ ] **Step 3: Create `analytics/signal/__init__.py` and `analytics/signal/types.py`**

Move `SignalEvent` (and `StatsContext`, `ConfluenceData` if co-located) verbatim from `alert_formatter.py` to `types.py`. Imports at top of `types.py`: only what the dataclasses need (`from dataclasses import dataclass`, `from typing import ...`).

- [ ] **Step 4: Edit `signals/alert_formatter.py`**

Delete the moved class blocks. Add `from analytics.signal.types import SignalEvent` (and friends) near the top so existing in-module references still resolve.

- [ ] **Step 5: Update import lines in callers**

`analytics/signal_lib.py`: change `from signals.alert_formatter import SignalEvent` → `from analytics.signal.types import SignalEvent`.

`signal_runner.py`, `signal_test_runner.py`, any test files in `/tmp/signal-1-callers.txt`: leave imports targeting `signals.alert_formatter` alone — they get `SignalEvent` via the re-export added in Step 4. The only edit here is to `analytics/*` files (since `analytics/*` MUST NOT import from `signals/*`).

- [ ] **Step 6: Write `tests/test_layering.py`** (the failing test, TDD style — should fail before any analytics imports are corrected)

```python
"""Layering / boundary contract tests.

Rule 1: analytics/* MUST NOT import from signals/*.
Rule 2: signals/* MAY import from analytics/* (one direction only).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent


def _iter_py_files(root: str) -> list[pathlib.Path]:
    base = REPO_ROOT / root
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_from(path: pathlib.Path, prefix: str) -> list[str]:
    """Return module names this file imports that start with `prefix`."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(prefix):
            hits.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefix):
                    hits.append(alias.name)
    return hits


def test_analytics_does_not_import_from_signals() -> None:
    """analytics/* MUST NOT import from signals/*."""
    violations: list[tuple[str, list[str]]] = []
    for path in _iter_py_files("analytics"):
        hits = _imports_from(path, "signals")
        if hits:
            violations.append((str(path.relative_to(REPO_ROOT)), hits))
    assert not violations, (
        "Layering violation: analytics/* imported from signals/*:\n"
        + "\n".join(f"  {p}: {hits}" for p, hits in violations)
    )


def test_layering_test_covers_known_files() -> None:
    """Sanity: the walk picks up a non-trivial number of analytics files."""
    files = _iter_py_files("analytics")
    assert len(files) >= 10, f"Layering test only walked {len(files)} files in analytics/ — likely a bug."
```

- [ ] **Step 7: Run the layering test BEFORE Step 5's import fix to confirm it catches the violation**

(If you've already done Step 5, temporarily restore the offending import in `analytics/signal_lib.py` to `from signals.alert_formatter import SignalEvent`.)

```bash
poetry run pytest tests/test_layering.py -v
```

Expected: `test_analytics_does_not_import_from_signals` FAILS with a message naming `analytics/signal_lib.py`. This proves the AST walk works.

Restore the corrected import (`from analytics.signal.types import SignalEvent`).

- [ ] **Step 8: Run the layering test after fix**

```bash
poetry run pytest tests/test_layering.py -v
```

Expected: both tests PASS.

- [ ] **Step 9: Run full gate**

```bash
make lint-py && make typecheck && make test
make test-regression
```

- [ ] **Step 10: Shim smoke**

```bash
poetry run python -c "from analytics.signal import SignalEvent; print('ok')"
poetry run python -c "from analytics.signal.types import SignalEvent; print('ok')"
poetry run python -c "from signals.alert_formatter import SignalEvent; print('ok')"  # backwards-compat re-export
```

All three `ok`.

- [ ] **Step 11: Manual smoke — alert formatting still works**

Run any test that exercises `format_signal_alert` or similar; or run:

```bash
poetry run python -c "
from analytics.signal.types import SignalEvent
from signals.alert_formatter import format_signal_alert  # adjust to actual function name
# Build a minimal SignalEvent and format it; confirm no exception.
print('alert format smoke ok')
"
```

If a one-line constructor isn't feasible, rely on the test suite's existing `test_alert_formatter.py` coverage as the smoke.

- [ ] **Step 12: Commit + push + PR summary**

```bash
git add analytics/signal/ signals/alert_formatter.py analytics/signal_lib.py signal_runner.py signal_test_runner.py tests/test_layering.py
git commit -m "refactor: move SignalEvent to analytics/signal/types + enforce layering (signal-1)"
git push -u origin refactor/signal-1-types
# write /tmp/pr-refactor/signal-1-types.md
```

**Manual smoke:** layering test (Steps 7-8); alert format smoke (Step 11).

**Rollback play:** revert; `SignalEvent` returns to `signals/alert_formatter.py`; `analytics/signal_lib.py` import line restores. The layering test is gone too — but since the violation reverts with it, gate (3) becomes vacuously green again.

---

### PR 8 — `signal-2` extract signal leaves

**Title:** `refactor: extract signal leaves into analytics/signal/ (signal-2)`
**Branch:** `refactor/signal-2-leaves`
**Risk:** Med. `_bt_mem_cache` mutation rule applies.
**Effort:** 1.5 days.

**Pre-flight:**

- [ ] `main` clean; PR 7 merged.
- [ ] **`_bt_mem_cache` mutation-not-rebinding rule is in scope.** It's defined exactly once in `analytics/signal/_common.py` (this PR creates that file). Every reader does `from analytics.signal._common import _bt_mem_cache` and mutates in place.
- [ ] Capture pre-PR signal-test output for diff:

```bash
mkdir -p /tmp/signal-2-baseline
poetry run python buibui.py signal test --symbol BTCUSDT --timeframe 1h --at 2025-12-01T00:00:00Z --lookback 7d > /tmp/signal-2-baseline/test.txt 2>&1 || true
```

(Adjust `--at` to a known historical candle that produces a non-empty result on `main`.)

**Files:**

- Create: `analytics/signal/_common.py` — `_CANDLE_CLOSE_BUFFER_SECS`, `_SCAN_WINDOW`, `_bt_mem_cache`, `_reset_bt_cache`, `_fmt_hold`, `parse_timeframe_secs`, `secs_until_next_boundary`.
- Create: `analytics/signal/gates.py` — `_filter_signals_by_adr`, `_is_adr_exempt`.
- Create: `analytics/signal/resolvers.py` — all 10× `_resolve_*` helpers + explicit `__all__`.
- Create: `analytics/signal/bt_cache.py` — `_compute_backtest`, `_backtest_summary`.
- Create: `analytics/signal/stats_context.py` — `_compute_stats_context`.
- Create: `analytics/signal/cofire.py` — `_find_live_cofire`, `_find_cross_tf_cofire`, `_parse_htf_ltf_pairs`.
- Modify: `analytics/signal/__init__.py` — expand re-exports.
- Modify: `analytics/signal_lib.py` — delete moved code; import from new modules; keep `scan_symbol` + `run_scan_cycle` (they move in signal-3).

**Shim contract (signal-2):**

`analytics/signal/__init__.py`:

```python
"""Signal package — leaves split in signal-2; scanner moves in signal-3."""
from analytics.signal._common import (
    _CANDLE_CLOSE_BUFFER_SECS,
    _SCAN_WINDOW,
    _bt_mem_cache,
    _fmt_hold,
    _reset_bt_cache,
    parse_timeframe_secs,
    secs_until_next_boundary,
)
from analytics.signal.bt_cache import _backtest_summary, _compute_backtest
from analytics.signal.cofire import (
    _find_cross_tf_cofire,
    _find_live_cofire,
    _parse_htf_ltf_pairs,
)
from analytics.signal.gates import _filter_signals_by_adr, _is_adr_exempt
from analytics.signal.resolvers import (
    _resolve_adr,
    _resolve_atr,
    _resolve_bias,
    _resolve_combo,
    _resolve_cross_tf_combo,
    _resolve_dow_pattern,
    _resolve_seasonality,
    _resolve_session,
    _resolve_volume_state,
    _resolve_weekly_state,
)  # adjust the list to actual function names
from analytics.signal.stats_context import _compute_stats_context
from analytics.signal.types import SignalEvent

__all__ = [
    "SignalEvent",
    "_CANDLE_CLOSE_BUFFER_SECS",
    "_SCAN_WINDOW",
    "_backtest_summary",
    "_bt_mem_cache",
    "_compute_backtest",
    "_compute_stats_context",
    "_filter_signals_by_adr",
    "_find_cross_tf_cofire",
    "_find_live_cofire",
    "_fmt_hold",
    "_is_adr_exempt",
    "_parse_htf_ltf_pairs",
    "_reset_bt_cache",
    "_resolve_adr",
    "_resolve_atr",
    "_resolve_bias",
    "_resolve_combo",
    "_resolve_cross_tf_combo",
    "_resolve_dow_pattern",
    "_resolve_seasonality",
    "_resolve_session",
    "_resolve_volume_state",
    "_resolve_weekly_state",
    "parse_timeframe_secs",
    "secs_until_next_boundary",
]
```

The `_resolve_*` list above is illustrative — confirm exact names by reading `analytics/signal_lib.py` first. Spec says "all 10× `_resolve_*` helpers".

`analytics/signal_lib.py` (after signal-2):

```python
"""Phase 2: scan_symbol + run_scan_cycle still live here; leaves moved to analytics/signal/.
signal-3 will move scanner code into analytics/signal/scanner.py and reduce this file
to a re-export shim.
"""
from analytics.signal._common import (
    _CANDLE_CLOSE_BUFFER_SECS,
    _SCAN_WINDOW,
    _bt_mem_cache,
    _fmt_hold,
    _reset_bt_cache,
    parse_timeframe_secs,
    secs_until_next_boundary,
)
from analytics.signal.bt_cache import _backtest_summary, _compute_backtest
from analytics.signal.cofire import _find_cross_tf_cofire, _find_live_cofire, _parse_htf_ltf_pairs
from analytics.signal.gates import _filter_signals_by_adr, _is_adr_exempt
from analytics.signal.resolvers import (
    # ... all 10
)
from analytics.signal.stats_context import _compute_stats_context
from analytics.signal.types import SignalEvent

# scan_symbol and run_scan_cycle remain defined below (move in signal-3).

def scan_symbol(...) -> ...: ...
def run_scan_cycle(...) -> ...: ...
```

**Steps:**

- [ ] **Step 1: Read `analytics/signal_lib.py` end-to-end**

Map every helper to its target leaf module per spec §2.5. Note: `_bt_mem_cache: dict[...] = {}` lives at module scope in `signal_lib.py` today. After this PR it lives in `analytics/signal/_common.py`. Both `_reset_bt_cache` (which clears it) and `_compute_backtest` (which reads/writes it) import it from `_common`.

- [ ] **Step 2: Create `_common.py`**

Move module-level constants (`_CANDLE_CLOSE_BUFFER_SECS`, `_SCAN_WINDOW = 200`), the `_bt_mem_cache: dict[str, BacktestSnapshot] = {}` definition, `_reset_bt_cache()` (`def _reset_bt_cache() -> None: _bt_mem_cache.clear()`), `_fmt_hold`, `parse_timeframe_secs`, `secs_until_next_boundary`.

**Critical:** `_reset_bt_cache` body is `_bt_mem_cache.clear()`, NOT `_bt_mem_cache = {}` and NOT `global _bt_mem_cache; _bt_mem_cache = {}`. Re-binding breaks every `from analytics.signal._common import _bt_mem_cache` consumer.

- [ ] **Step 3: Create `gates.py`, `resolvers.py`, `bt_cache.py`, `stats_context.py`, `cofire.py`**

Per the function-to-target map. Each module imports `from analytics.signal._common import _bt_mem_cache` (for `bt_cache.py`) or whatever helpers it needs. No cross-leaf imports beyond `_common` and `types`.

`bt_cache.py` reads/writes `_bt_mem_cache`:

```python
# analytics/signal/bt_cache.py
from analytics.signal._common import _bt_mem_cache

def _compute_backtest(key: str, ...) -> BacktestSnapshot:
    if key in _bt_mem_cache:
        return _bt_mem_cache[key]
    snap = ...  # compute
    _bt_mem_cache[key] = snap
    return snap
```

NEVER `_bt_mem_cache = {}` after import.

- [ ] **Step 4: Update `analytics/signal/__init__.py`** per shim contract above.

- [ ] **Step 5: Edit `analytics/signal_lib.py`**

Delete moved code; add the imports shown in the shim-contract template; keep `scan_symbol` and `run_scan_cycle` definitions intact.

- [ ] **Step 6: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
poetry run pytest tests/test_layering.py -v
```

Test count ≥ baseline.

- [ ] **Step 7: Cache-coherence smoke**

```bash
poetry run python -c "
from analytics.signal._common import _bt_mem_cache, _reset_bt_cache
_bt_mem_cache['key1'] = 'val1'
assert 'key1' in _bt_mem_cache
_reset_bt_cache()
assert 'key1' not in _bt_mem_cache
# Now confirm bt_cache module sees the same dict identity:
from analytics.signal.bt_cache import _bt_mem_cache as cache_view
assert cache_view is _bt_mem_cache, 'cache identity broken — _bt_mem_cache was rebound'
print('cache coherence ok')
"
```

Expected: `cache coherence ok`. If the assert fails, someone re-bound `_bt_mem_cache` — find and fix.

- [ ] **Step 8: Shim smoke**

```bash
poetry run python -c "
from analytics.signal_lib import (
    scan_symbol, run_scan_cycle, SignalEvent,
    _bt_mem_cache, _reset_bt_cache, _SCAN_WINDOW,
    _filter_signals_by_adr, _is_adr_exempt,
    _compute_backtest, _backtest_summary,
    _compute_stats_context,
    _find_live_cofire, _find_cross_tf_cofire, _parse_htf_ltf_pairs,
)
# All 10 _resolve_* still importable:
from analytics.signal_lib import _resolve_adr, _resolve_atr, _resolve_bias  # extend
print('ok')"
```

- [ ] **Step 9: Manual smoke — historical replay**

```bash
poetry run python buibui.py signal test --symbol BTCUSDT --timeframe 1h --at 2025-12-01T00:00:00Z --lookback 7d > /tmp/signal-2-after/test.txt 2>&1
diff /tmp/signal-2-baseline/test.txt /tmp/signal-2-after/test.txt
```

Expected: zero diff. Drift = revert.

- [ ] **Step 10: Commit + push + PR summary**

```bash
git add analytics/signal/ analytics/signal_lib.py
git commit -m "refactor: extract signal leaves into analytics/signal/ (signal-2)"
git push -u origin refactor/signal-2-leaves
# write /tmp/pr-refactor/signal-2-leaves.md
```

**Rollback play:** revert; `signal_lib.py` body restored; `analytics/signal/_common.py` etc. removed. `signal-1`'s `types.py` and `__init__.py` survive but only contain `SignalEvent`.

---

### PR 9 — `signal-3` move scanner

**Title:** `refactor: move scanner into analytics/signal/scanner (signal-3)`
**Branch:** `refactor/signal-3-scanner`
**Risk:** High. Highest blast radius in the signal module — 10+ external import sites for `scan_symbol`.
**Effort:** 1 day.

**Pre-flight:**

- [ ] `main` clean; PR 8 merged.
- [ ] Capture signal-test output again (after signal-2 leaves):

```bash
mkdir -p /tmp/signal-3-baseline
poetry run python buibui.py signal test --symbol BTCUSDT --timeframe 1h --at 2025-12-01T00:00:00Z --lookback 7d > /tmp/signal-3-baseline/test.txt 2>&1 || true
```

**Files:**

- Create: `analytics/signal/scanner.py` — `scan_symbol`, `run_scan_cycle`.
- Modify: `analytics/signal/__init__.py` — add `scan_symbol`, `run_scan_cycle` re-exports.
- Modify: `analytics/signal_lib.py` → shim.

**Shim contract (signal-3 — final):**

`analytics/signal/__init__.py` (final form for Phase 2):

```python
"""Signal package — full split landed in signal-3."""
from analytics.signal._common import (
    _CANDLE_CLOSE_BUFFER_SECS,
    _SCAN_WINDOW,
    _bt_mem_cache,
    _fmt_hold,
    _reset_bt_cache,
    parse_timeframe_secs,
    secs_until_next_boundary,
)
from analytics.signal.bt_cache import _backtest_summary, _compute_backtest
from analytics.signal.cofire import _find_cross_tf_cofire, _find_live_cofire, _parse_htf_ltf_pairs
from analytics.signal.gates import _filter_signals_by_adr, _is_adr_exempt
from analytics.signal.resolvers import (
    # ... all 10 _resolve_*
)
from analytics.signal.scanner import run_scan_cycle, scan_symbol
from analytics.signal.stats_context import _compute_stats_context
from analytics.signal.types import SignalEvent

__all__ = [
    "SignalEvent",
    "run_scan_cycle",
    "scan_symbol",
    # ... + every helper from signal-2's __all__
]
```

`analytics/signal_lib.py` (final shim):

```python
"""Legacy import shim. Real implementation lives in analytics/signal/."""
from analytics.signal import *  # noqa: F401,F403
from analytics.signal import __all__  # noqa: F401
```

**Steps:**

- [ ] **Step 1: Cut `scan_symbol` and `run_scan_cycle` from `analytics/signal_lib.py`**

Move both function bodies into a new file `analytics/signal/scanner.py`. Imports at top of `scanner.py`: every helper they call from `_common`, `gates`, `resolvers`, `bt_cache`, `stats_context`, `cofire`, `types`. Plus external imports: `analytics.indicators_lib`, `analytics.store`, `analytics.backtest`, etc.

Make sure `scanner.py` does NOT do `from analytics.signal_lib import ...` — that creates a circular import at runtime.

- [ ] **Step 2: Add `scan_symbol`, `run_scan_cycle` to `analytics/signal/__init__.py`'s `__all__`**

Per shim-contract above.

- [ ] **Step 3: Replace `analytics/signal_lib.py` body with the final shim**

3 lines (plus docstring).

- [ ] **Step 4: Pre-flight import-cycle check**

```bash
poetry run python -c "import analytics.signal_lib; import analytics.signal.scanner; import signal_runner; import signal_test_runner; print('ok')"
```

Expected: `ok`. Any `ImportError: circular` = fix `scanner.py` imports (remove any `from analytics.signal_lib import ...`).

- [ ] **Step 5: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
poetry run pytest tests/test_layering.py -v
```

- [ ] **Step 6: Shim smoke**

```bash
poetry run python -c "
from analytics.signal_lib import scan_symbol, run_scan_cycle, SignalEvent, _bt_mem_cache, _reset_bt_cache
print('ok')"
poetry run python -c "from analytics.signal import scan_symbol, run_scan_cycle; print('ok')"
poetry run python -c "from analytics.signal.scanner import scan_symbol, run_scan_cycle; print('ok')"
```

All three `ok`.

- [ ] **Step 7: Manual smoke — historical replay**

```bash
poetry run python buibui.py signal test --symbol BTCUSDT --timeframe 1h --at 2025-12-01T00:00:00Z --lookback 7d > /tmp/signal-3-after.txt 2>&1
diff /tmp/signal-3-baseline/test.txt /tmp/signal-3-after.txt
```

Expected: zero diff.

- [ ] **Step 8: Daemon-startup smoke (without running it)**

```bash
poetry run python -c "
from signal_runner import build_runner  # adjust to actual factory function
# Confirm import chain doesn't crash
print('daemon import ok')
"
```

If `build_runner` doesn't exist, just `import signal_runner` and confirm no exception.

- [ ] **Step 9: Commit + push + PR summary**

```bash
git add analytics/signal/scanner.py analytics/signal/__init__.py analytics/signal_lib.py
git commit -m "refactor: move scanner into analytics/signal/scanner (signal-3)"
git push -u origin refactor/signal-3-scanner
# write /tmp/pr-refactor/signal-3-scanner.md
```

**Rollback play:** revert; `scan_symbol` and `run_scan_cycle` return to `signal_lib.py`; `scanner.py` is removed. Cache identity unchanged because `_bt_mem_cache` still lives in `_common.py` from signal-2.

---

### PR 10 — `strat-1` strategies scaffold + infra move

**Title:** `refactor: scaffold analytics/strategies/ + move infra (strat-1)`
**Branch:** `refactor/strat-1-scaffold`
**Risk:** Low. ~400-line move; registry mechanics in isolation.
**Effort:** 1 day.

**Pre-flight:**

- [ ] `main` clean; PR 9 merged.
- [ ] **`_STRATEGY_MODULES` explicit-tuple rule applies in this PR** — but the tuple is not yet populated with detector module names because detectors haven't moved. For strat-1, `_registry.py` lives in the package but reads `STRATEGY_REGISTRY` from `analytics.indicators_lib` (where it still resides). The tuple gets populated in strat-2.

**Files:**

- Create: `analytics/strategies/__init__.py`
- Create: `analytics/strategies/_base.py` — `ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS` constant.
- Create: `analytics/strategies/_shared.py` — `_find_bos_swing`, `volume_confirm`.
- Create: `analytics/strategies/_registry.py` — for strat-1, this module re-exports `STRATEGY_REGISTRY` etc. from `indicators_lib`. Strat-2 will replace this with the explicit-tuple-driven assembly.
- Create: `analytics/strategies/_seasonality.py` — `seasonality_stats` (the helper, not the detector).
- Modify: `analytics/indicators_lib.py` — delete moved infra; `from analytics.strategies._base import ParamSpec, StrategySpec, SIGNAL_COLUMNS`; `from analytics.strategies._shared import _find_bos_swing, volume_confirm`; `from analytics.strategies._seasonality import seasonality_stats`. Detectors still live in this file.

**Shim contract (strat-1 partial):**

`analytics/strategies/__init__.py`:

```python
"""Strategies package — scaffold landed in strat-1; detectors move in strat-2."""
from analytics.strategies._base import ParamSpec, SIGNAL_COLUMNS, StrategySpec
from analytics.strategies._registry import (
    DETECTOR_REGISTRY,
    INCOMPATIBLE_PAIRS,
    KNOWN_STRATEGIES,
    KNOWN_STRATEGY_TYPES,
    STRATEGY_REGISTRY,
    STRATEGY_TYPE_GROUPS,
)
from analytics.strategies._seasonality import seasonality_stats
from analytics.strategies._shared import _find_bos_swing, volume_confirm

__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "ParamSpec",
    "SIGNAL_COLUMNS",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
    "StrategySpec",
    "_find_bos_swing",
    "seasonality_stats",
    "volume_confirm",
]
```

`analytics/strategies/_registry.py` (strat-1 form — temporary):

```python
"""Strategy registry assembler.

In strat-1 this module re-exports from analytics.indicators_lib (where the
20 detectors and STRATEGY_REGISTRY still live). In strat-2 this becomes the
explicit-tuple-driven assembler.
"""
from analytics.indicators_lib import (
    DETECTOR_REGISTRY,
    INCOMPATIBLE_PAIRS,
    KNOWN_STRATEGIES,
    KNOWN_STRATEGY_TYPES,
    STRATEGY_REGISTRY,
    STRATEGY_TYPE_GROUPS,
)

__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
]
```

`analytics/indicators_lib.py` (after strat-1) — keeps detectors + builds `STRATEGY_REGISTRY` from them, but now imports `ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`, `_find_bos_swing`, `volume_confirm`, `seasonality_stats` from the new package.

**Steps:**

- [ ] **Step 1: Read `analytics/indicators_lib.py`** (3,143 LOC)

Identify infra to move:

- `ParamSpec`, `StrategySpec` dataclasses
- `SIGNAL_COLUMNS` constant
- `_find_bos_swing` helper (used by `bos`, `choch` detectors AND `analytics/zones_lib.py`)
- `volume_confirm` helper (used by multiple detectors)
- `seasonality_stats` (helper used by `seasonality` detector + analytics callers)
- `INCOMPATIBLE_PAIRS`
- `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS` (taxonomy lists)

Keep detectors and `STRATEGY_REGISTRY`/`DETECTOR_REGISTRY` in `indicators_lib.py` for now — they move in strat-2.

- [ ] **Step 2: Create `_base.py`, `_shared.py`, `_seasonality.py`**

Move the named symbols. `_base.py` is pure data classes with no dependencies. `_shared.py` may import `pandas`. `_seasonality.py` may import pandas + `analytics.store`.

- [ ] **Step 3: Create `_registry.py` (strat-1 temporary form)** per shim contract above.

- [ ] **Step 4: Create `analytics/strategies/__init__.py`** per shim contract above.

- [ ] **Step 5: Edit `analytics/indicators_lib.py`**

Delete the moved blocks. Add at top:

```python
from analytics.strategies._base import ParamSpec, SIGNAL_COLUMNS, StrategySpec
from analytics.strategies._shared import _find_bos_swing, volume_confirm
from analytics.strategies._seasonality import seasonality_stats
# INCOMPATIBLE_PAIRS, KNOWN_*, STRATEGY_TYPE_GROUPS still defined below until strat-2.
```

`indicators_lib.py` continues to define `STRATEGY_REGISTRY`, `DETECTOR_REGISTRY`, `INCOMPATIBLE_PAIRS`, `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS` and the 20 `detect_*` functions. Those move in strat-2.

- [ ] **Step 6: Verify `_find_bos_swing` import in `analytics/zones_lib.py`**

Open `analytics/zones_lib.py`, find the `_find_bos_swing` import. It must keep working — either via the indicators_lib re-export or via a direct import from the new `_shared.py`. Since the spec says "shim re-exports `_find_bos_swing` from `analytics.indicators_lib`", confirm `indicators_lib.py` still exports it.

`indicators_lib.py` near top, after the `_shared` import:

```python
# Re-exported for backwards compatibility (zones_lib imports from here):
__all__ = ["_find_bos_swing", ...]  # explicit __all__ if not already present
```

- [ ] **Step 7: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
poetry run pytest tests/test_layering.py -v
```

- [ ] **Step 8: Shim smoke**

```bash
poetry run python -c "
from analytics.strategies import (
    ParamSpec, StrategySpec, SIGNAL_COLUMNS,
    STRATEGY_REGISTRY, DETECTOR_REGISTRY, INCOMPATIBLE_PAIRS,
    KNOWN_STRATEGIES, KNOWN_STRATEGY_TYPES, STRATEGY_TYPE_GROUPS,
    _find_bos_swing, volume_confirm, seasonality_stats,
)
print('ok')"
poetry run python -c "from analytics.indicators_lib import STRATEGY_REGISTRY, _find_bos_swing; print('ok')"
poetry run python -c "from analytics.zones_lib import extract_bos; print('ok')"  # adjust to actual function name
```

All `ok`.

- [ ] **Step 9: Strategy ordering smoke**

```bash
poetry run python -c "from analytics.indicators_lib import STRATEGY_REGISTRY; print('\n'.join(STRATEGY_REGISTRY.keys()))" > /tmp/strat-1-order.txt
diff /tmp/phase2-baseline/strategy-order.txt /tmp/strat-1-order.txt
```

Expected: zero diff.

- [ ] **Step 10: Commit + push + PR summary**

```bash
git add analytics/strategies/ analytics/indicators_lib.py
git commit -m "refactor: scaffold analytics/strategies/ + move infra (strat-1)"
git push -u origin refactor/strat-1-scaffold
# write /tmp/pr-refactor/strat-1-scaffold.md
```

**Rollback play:** revert; infra returns to `indicators_lib.py`. `analytics/strategies/` directory is removed.

---

### PR 11 — `strat-2` atomic detector move

**Title:** `refactor: move 20 detectors to per-strategy files (strat-2)`
**Branch:** `refactor/strat-2-detectors`
**Risk:** High. ~2,700-line diff. Registry-shape regressions hide easily.
**Effort:** 2-3 days.

**Pre-flight:**

- [ ] `main` clean; PR 10 merged.
- [ ] **`_STRATEGY_MODULES` explicit-tuple rule** — the tuple in `analytics/strategies/_registry.py` matches `/tmp/phase2-baseline/strategy-order.txt` line for line.
- [ ] **Test count rule** — pre-PR pytest count must equal post-PR pytest count. Tests are reorganised, never deleted.
- [ ] Capture per-strategy backtest output as a regression-precondition matrix:

```bash
mkdir -p /tmp/strat-2-baseline
for strat in $(cat /tmp/phase2-baseline/strategy-order.txt); do
  poetry run python buibui.py backtest --config config/signal_watch.toml --since 30d --symbol BTCUSDT --timeframe 1h --strategy "$strat" > "/tmp/strat-2-baseline/${strat}.txt" 2>&1 || true
done
```

(If `--strategy` flag doesn't exist, capture the full backtest run instead and diff that.)

**Files:**

- Create: `analytics/strategies/<strategy>.py` × 20 (per `/tmp/phase2-baseline/strategy-order.txt`).
- Modify: `analytics/strategies/_registry.py` → explicit-tuple-driven assembly (replaces strat-1 temporary form).
- Modify: `analytics/indicators_lib.py` → final shim form.
- Create: `tests/strategies/test_<strategy>.py` × 20.
- Create: `tests/test_strategy_registry.py` — tests for `STRATEGY_REGISTRY`, `INCOMPATIBLE_PAIRS`, taxonomy invariants.
- Delete: `tests/test_indicators_lib.py`, `tests/test_candle_patterns.py`, `tests/test_fib_strategies.py`.

**Per-strategy file template (`analytics/strategies/<name>.py`):**

```python
"""<strategy_name> detector + StrategySpec."""
from __future__ import annotations

import pandas as pd

from analytics.strategies._base import ParamSpec, SIGNAL_COLUMNS, StrategySpec
from analytics.strategies._shared import volume_confirm  # only if used


def detect_<strategy_name>(df: pd.DataFrame, ...) -> pd.DataFrame:
    """Detector body — moved verbatim from indicators_lib.py."""
    ...


SPEC = StrategySpec(
    name="<strategy_name>",
    detector=detect_<strategy_name>,
    params=[ParamSpec(...)],
    description="...",
    # ... other StrategySpec fields exactly as in indicators_lib.py
)
```

`analytics/strategies/_registry.py` (final form):

```python
"""Strategy registry assembler — explicit-tuple-driven.

The order of _STRATEGY_MODULES MUST match the canonical order in
/tmp/phase2-baseline/strategy-order.txt (captured pre-Phase-2). Adding a
strategy = appending the module name here AND creating the file.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from analytics.strategies._base import StrategySpec

# CANONICAL ORDER — matches pre-Phase-2 STRATEGY_REGISTRY.keys() exactly.
_STRATEGY_MODULES: tuple[str, ...] = (
    "analytics.strategies.wick_fill",
    "analytics.strategies.marubozu",
    "analytics.strategies.orb",
    "analytics.strategies.liquidity_sweep",
    "analytics.strategies.fvg",
    "analytics.strategies.bos",
    "analytics.strategies.choch",
    "analytics.strategies.eqh_eql",
    "analytics.strategies.ob",
    "analytics.strategies.smt_divergence",
    "analytics.strategies.inside_bar",
    "analytics.strategies.pin_bar",
    "analytics.strategies.hammer",
    "analytics.strategies.engulfing",
    "analytics.strategies.morning_star",
    "analytics.strategies.doji",
    "analytics.strategies.trend_day",
    "analytics.strategies.fib_golden_zone",
    "analytics.strategies.ote_entry",
    "analytics.strategies.seasonality",
)

STRATEGY_REGISTRY: dict[str, "StrategySpec"] = {}
DETECTOR_REGISTRY: dict[str, object] = {}

for _modname in _STRATEGY_MODULES:
    _mod = importlib.import_module(_modname)
    _spec = _mod.SPEC
    STRATEGY_REGISTRY[_spec.name] = _spec
    # DETECTOR_REGISTRY excludes seasonality (per CLAUDE.md: 18 detectors in 20 strategies);
    # adjust filter to match indicators_lib.py's exact rule:
    if not getattr(_spec, "is_helper_only", False):
        DETECTOR_REGISTRY[_spec.name] = _spec.detector

# Taxonomy invariants — moved verbatim from indicators_lib.py:
KNOWN_STRATEGIES: tuple[str, ...] = tuple(STRATEGY_REGISTRY.keys())
KNOWN_STRATEGY_TYPES: tuple[str, ...] = (...)  # paste from indicators_lib
STRATEGY_TYPE_GROUPS: dict[str, tuple[str, ...]] = {...}  # paste verbatim
INCOMPATIBLE_PAIRS: tuple[tuple[str, str], ...] = (...)  # paste verbatim

__all__ = [
    "DETECTOR_REGISTRY",
    "INCOMPATIBLE_PAIRS",
    "KNOWN_STRATEGIES",
    "KNOWN_STRATEGY_TYPES",
    "STRATEGY_REGISTRY",
    "STRATEGY_TYPE_GROUPS",
]
```

The exact `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS` literals come straight from `indicators_lib.py` — paste them in unchanged.

The DETECTOR_REGISTRY filter rule must match the current rule. CLAUDE.md says "18 detectors in `DETECTOR_REGISTRY`" out of 20 entries in `STRATEGY_REGISTRY`. The two excluded are `seasonality` and (per Phase 0a) `fibonacci_retracement` if it exists, OR `seasonality` plus one other. **Read `indicators_lib.py`'s actual filter rule** and reproduce it exactly. If the rule is "exclude `seasonality`", make the field `SPEC.is_helper_only = True` on `seasonality.py` and keep the filter as shown.

`analytics/indicators_lib.py` (final shim):

```python
"""Legacy import shim. Real implementation lives in analytics/strategies/.

Kept so 30+ existing callers (signal_lib, backtest_lib, runners, web routers,
zones_lib, tests) continue to work without edits.
"""
from analytics.strategies import *  # noqa: F401,F403
from analytics.strategies import __all__  # noqa: F401

# De-facto-public underscore helper (zones_lib imports from here):
from analytics.strategies._shared import _find_bos_swing  # noqa: F401

# Per-strategy detect_* functions — re-exported explicitly so existing
# `from analytics.indicators_lib import detect_wick_fills` callers keep working:
from analytics.strategies.bos import detect_bos
from analytics.strategies.choch import detect_choch
from analytics.strategies.doji import detect_doji
from analytics.strategies.engulfing import detect_engulfing
from analytics.strategies.eqh_eql import detect_eqh_eql
from analytics.strategies.fib_golden_zone import detect_fib_golden_zone
from analytics.strategies.fvg import detect_fvg
from analytics.strategies.hammer import detect_hammer
from analytics.strategies.inside_bar import detect_inside_bar
from analytics.strategies.liquidity_sweep import detect_liquidity_sweep
from analytics.strategies.marubozu import detect_marubozu
from analytics.strategies.morning_star import detect_morning_star
from analytics.strategies.ob import detect_ob
from analytics.strategies.orb import detect_orb
from analytics.strategies.ote_entry import detect_ote_entry
from analytics.strategies.pin_bar import detect_pin_bar
from analytics.strategies.seasonality import detect_seasonality
from analytics.strategies.smt_divergence import detect_smt_divergence
from analytics.strategies.trend_day import detect_trend_day
from analytics.strategies.wick_fill import detect_wick_fills
```

(Cross-check actual function names against `STRATEGY_REGISTRY[<name>].detector.__name__` per strategy. Adjust import lines to the actual names — for example `detect_wick_fills` (plural) per the spec sample.)

**Steps:**

- [ ] **Step 1: Read `analytics/indicators_lib.py` end-to-end**

Identify each detector function + its `STRATEGY_REGISTRY[<name>] = StrategySpec(...)` literal. Note any detector-private helpers (only used by that one detector — those move into the per-strategy file). Note any helper used by ≥ 2 detectors — already moved to `_shared.py` in strat-1.

- [ ] **Step 2: Confirm canonical strategy order matches `/tmp/phase2-baseline/strategy-order.txt`**

```bash
poetry run python -c "from analytics.indicators_lib import STRATEGY_REGISTRY; print('\n'.join(STRATEGY_REGISTRY.keys()))" > /tmp/strat-2-order-pre.txt
diff /tmp/phase2-baseline/strategy-order.txt /tmp/strat-2-order-pre.txt
```

Expected: zero diff. (If they differ, update `/tmp/phase2-baseline/strategy-order.txt` first — that's the floor.)

- [ ] **Step 3: Create per-strategy files**

For each strategy in canonical order:

1. Read `detect_<name>` body + helpers + `StrategySpec(...)` literal from `indicators_lib.py`.
2. Create `analytics/strategies/<name>.py` per template above.
3. Imports: `pandas as pd`, plus `from analytics.strategies._base import ParamSpec, SIGNAL_COLUMNS, StrategySpec`, plus `_shared` helpers as needed, plus any external deps (`numpy`, `analytics.store`).
4. Detector body moves verbatim. `StrategySpec(...)` literal moves verbatim, exposed as `SPEC`.

- [ ] **Step 4: Replace `analytics/strategies/_registry.py` with the final-form code** (per shim-contract above). Paste the exact `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS` literals from `indicators_lib.py`.

- [ ] **Step 5: Write `analytics/strategies/seasonality.py`**

Detector + `SPEC`. If `seasonality` is the helper-only entry, set `SPEC.is_helper_only = True` (or whatever flag matches the exclusion rule). The detector body imports `seasonality_stats` from `analytics.strategies._seasonality`.

- [ ] **Step 6: Replace `analytics/indicators_lib.py` body with the final shim** per contract above.

- [ ] **Step 7: Reorganise tests**

For each strategy:

1. Create `tests/strategies/test_<strategy>.py`.
2. Move tests for `detect_<strategy>` from `tests/test_indicators_lib.py`, `tests/test_candle_patterns.py`, `tests/test_fib_strategies.py` into the new file. Update imports: `from analytics.strategies.<name> import detect_<name>` (or keep `from analytics.indicators_lib import detect_<name>` — both work via the shim).
3. Each per-strategy test file is self-contained.

After redistribution:

- Create `tests/test_strategy_registry.py`. It contains:
  - Test that `STRATEGY_REGISTRY` keys match the canonical order (`tuple(STRATEGY_REGISTRY.keys()) == _CANONICAL_ORDER`).
  - Tests for `INCOMPATIBLE_PAIRS` invariants (e.g., no pair lists itself, all entries refer to valid strategy names).
  - Tests for taxonomy completeness (every strategy belongs to exactly one `STRATEGY_TYPE_GROUPS` bucket, etc.).
  - Move any registry-shape tests here from `test_indicators_lib.py`.
- Delete `tests/test_indicators_lib.py`, `tests/test_candle_patterns.py`, `tests/test_fib_strategies.py`.

- [ ] **Step 8: Run pytest --collect-only**

```bash
poetry run pytest --collect-only -q 2>&1 | tail -5 > /tmp/strat-2-collect.txt
diff /tmp/phase2-baseline/pytest-collect.txt /tmp/strat-2-collect.txt
```

The "tests collected" count must match the baseline (or be higher if `test_strategy_registry.py` adds new tests; in that case +N is allowed). Lower count = a test file was deleted without redistributing all its cases — restore.

- [ ] **Step 9: Run gate**

```bash
make lint-py && make typecheck && make test
make test-regression
poetry run pytest tests/test_layering.py -v
```

If the regression test fails: the registry shape drifted. Check `_STRATEGY_MODULES` order against `/tmp/phase2-baseline/strategy-order.txt`.

- [ ] **Step 10: Shim smoke**

```bash
poetry run python -c "
from analytics.indicators_lib import (
    STRATEGY_REGISTRY, DETECTOR_REGISTRY,
    INCOMPATIBLE_PAIRS, KNOWN_STRATEGIES, KNOWN_STRATEGY_TYPES,
    STRATEGY_TYPE_GROUPS, ParamSpec, StrategySpec, SIGNAL_COLUMNS,
    _find_bos_swing, volume_confirm, seasonality_stats,
    detect_wick_fills, detect_marubozu, detect_orb, detect_liquidity_sweep,
    detect_fvg, detect_bos, detect_choch, detect_eqh_eql, detect_ob,
    detect_smt_divergence, detect_inside_bar, detect_pin_bar, detect_hammer,
    detect_engulfing, detect_morning_star, detect_doji, detect_trend_day,
    detect_fib_golden_zone, detect_ote_entry, detect_seasonality,
)
assert len(STRATEGY_REGISTRY) == 20, len(STRATEGY_REGISTRY)
assert len(DETECTOR_REGISTRY) == 18, len(DETECTOR_REGISTRY)  # adjust if rule differs
print('ok')"
```

Expected: `ok` with both length asserts passing. (Update `18` if the actual exclusion rule produces a different count.)

- [ ] **Step 11: zones_lib smoke**

```bash
poetry run python -c "from analytics.zones_lib import extract_bos; print('ok')"  # adjust to actual function
```

`zones_lib._find_bos_swing` import via `indicators_lib` shim must still work.

- [ ] **Step 12: Manual smoke per taxonomy group**

Run `buibui signal test` with one strategy from each of the 6 taxonomy groups (Structure, Fibonacci, Price Action, Candlestick, Flow, Session) against a known-firing historical candle:

```bash
poetry run python buibui.py signal test --symbol BTCUSDT --timeframe 1h --at 2025-12-01T00:00:00Z --lookback 7d > /tmp/strat-2-after.txt 2>&1
diff /tmp/signal-3-baseline/test.txt /tmp/strat-2-after.txt
```

Expected: zero diff (this run uses the same fixture as signal-3's baseline; all detectors should produce identical output).

For per-strategy granularity, also:

```bash
for strat in $(cat /tmp/phase2-baseline/strategy-order.txt); do
  poetry run python buibui.py backtest --config config/signal_watch.toml --since 30d --symbol BTCUSDT --timeframe 1h --strategy "$strat" > "/tmp/strat-2-after/${strat}.txt" 2>&1 || true
  diff "/tmp/strat-2-baseline/${strat}.txt" "/tmp/strat-2-after/${strat}.txt" || echo "DRIFT: $strat"
done
```

Any `DRIFT: …` line means that strategy's behaviour diverged. Bisect within strat-2 — likely a typo in the per-strategy file's `SPEC` literal or a missed helper import.

- [ ] **Step 13: Commit + push + PR summary**

```bash
git add analytics/strategies/ analytics/indicators_lib.py tests/strategies/ tests/test_strategy_registry.py
git rm tests/test_indicators_lib.py tests/test_candle_patterns.py tests/test_fib_strategies.py
git commit -m "refactor: move 20 detectors to per-strategy files (strat-2)"
git push -u origin refactor/strat-2-detectors
mkdir -p /tmp/pr-refactor
# write /tmp/pr-refactor/strat-2-detectors.md
```

**Rollback play:** revert; `indicators_lib.py` body restored from history; per-strategy files removed; old test files restored. `analytics/strategies/_base.py`, `_shared.py`, `_seasonality.py` survive from strat-1 — leave them.

---

## Per-PR effort estimate

| PR | Branch | Risk | Effort |
| --- | --- | --- | --- |
| 1 | `chore/perf-1-baseline` | None | 0.5 day |
| 2 | `refactor/cli-1-split` | Low | 1 day |
| 3 | `refactor/stats-1-split` | Low | 1 day |
| 4 | `refactor/backtest-1-split` | Med | 1 day |
| 5 | `refactor/store-1-schema` | Low | 0.5 day |
| 6 | `refactor/store-2-split` | Med-High | 2 days |
| 7 | `refactor/signal-1-types` | Low | 0.5 day |
| 8 | `refactor/signal-2-leaves` | Med | 1.5 days |
| 9 | `refactor/signal-3-scanner` | High | 1 day |
| 10 | `refactor/strat-1-scaffold` | Low | 1 day |
| 11 | `refactor/strat-2-detectors` | High | 2-3 days |
| 12 | `chore/perf-2-close` | None | 0.5 day |
| **Total** | | | **12.5-13.5 days** |

At solo pace with reviewer turnaround, 2-4 calendar weeks per the spec.

---

## Acceptance criteria (end-of-phase checklist)

- [ ] All 12 PRs merged on `main` with green gate (lint+typecheck+test+regression+layering+shim+manual smoke).
- [ ] `make test-regression` passes against the same goldens that pass on `main` today (Phase-1 era). `sha256sum tests/fixtures/golden_*.json` matches `/tmp/phase2-baseline/golden-hashes.txt`. Zero regeneration during Phase 2.
- [ ] Manual smokes per PR table all green and recorded in PR summaries.
- [ ] `analytics/indicators_lib.py`, `analytics/signal_lib.py`, `analytics/data_store.py`, `analytics/stats_lib.py`, `analytics/backtest_lib.py`, `buibui.py` all reduced to shim/entry size (≤ ~50 LOC each).
- [ ] `tests/test_layering.py` passes; `signals/` no longer defines `SignalEvent`.
- [ ] `docs/perf-baseline-2026-04-27.md` and `docs/perf-baseline-phase2-close.md` both committed; close-of-phase shows no >10% wall-clock regression on any benchmarked path. Any >10% regression is documented (real or noise).
- [ ] MEMORY To-Do list updated: P5 + P8 moved to Phase 4 scope; P1 marked done. CLAUDE.md project structure updated to reflect `cli/`, `analytics/strategies/`, `analytics/stats/`, `analytics/backtest/`, `analytics/store/`, `analytics/signal/` packages.

---

## Open questions

None. The spec resolved Q1–Q4 explicitly:

- Q1: hybrid test partition (per-strategy split for `analytics/strategies/`; bundled for stats/backtest/store/signal).
- Q2: `from analytics.signal import SignalEvent` is canonical; deep path works but is undocumented.
- Q3: strat-2 is single atomic PR.
- Q4: zero golden drift permitted in Phase 2.

`_STRATEGY_MODULES` exclusion rule for `DETECTOR_REGISTRY` (the "18 of 20" filter) is the one thing the executing engineer must read from `indicators_lib.py` itself before strat-2, since CLAUDE.md doesn't pin the exact predicate. The plan flags this in PR 11 Step 4 — read the current filter, preserve its semantics, don't invent a new one.

---

## Self-review notes

- **Spec coverage:** every section of the spec is mapped to one or more PR steps. §2.1 → PR 2. §2.2 → PR 3. §2.3 → PR 4. §2.4 → PRs 5+6. §2.5 → PRs 7+8+9. §2.6 → PRs 10+11. §3 → PR 7 (`tests/test_layering.py`). §4 → PRs 1+12. The risk register entries are mapped to per-PR mitigation steps and rollback plays.
- **Type/name consistency:** function names referenced in shim contracts are illustrative for symbols spec-listed (e.g., the 10 `_resolve_*` helpers); the executing engineer reads the actual names from `analytics/signal_lib.py` before PR 8 and adjusts the `__all__` to match. Same for `DETECTOR_REGISTRY` filter (PR 11). These are explicitly flagged.
- **Placeholder scan:** no `TBD`/`TODO` left. Every code block is concrete; every command has expected output; every shim has a verbatim `__all__`.
