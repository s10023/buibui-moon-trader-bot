---
name: sanity-check
description: >
  Full project health check across five dimensions: CI hygiene, wiring audit,
  docs sync, skills freshness, architecture review.
  Invoke weekly, after any large refactor or merge, when the user says
  "/sanity-check", or asks "is everything wired up", "do the docs match",
  or "anything stale".
allowed-tools: Bash, Read, Edit
---

# Sanity Check Skill

Run a full periodic health check of the buibui-moon-trader-bot codebase. **Run weekly, or after any large refactor/merge.**

This check covers five dimensions: CI hygiene, wiring audit, documentation sync, skills freshness, and architecture review.

---

⚠ **QUOTE EVERY `grep --include` GLOB — the shell here is zsh.** `grep -rn "x" --include=*.py .`
dies with `no matches found: --include=*.py` before grep ever runs, and a check whose grep
never ran looks *exactly* like a check that passed clean. This produced two silent empty
results on 2026-08-19 and would have shipped as "wiring clean". Write `--include="*.py"`,
and treat any empty grep result in this file as unproven until you have seen it match
something you know exists.

⚠ **Exclude `.cache/` from every repo-wide grep.** A `.cache/cleantree.*/` copy of the whole
tree can exist (CI reproduction runs leave one), so counts silently double and every finding
appears twice.

---

## 1. CI checks (run first — block on failures)

```bash
make lint-py       # ruff format + lint
make typecheck     # mypy strict
make test          # pytest
make lint-md       # markdownlint-cli2
poetry check       # lockfile consistency
```

Report pass/fail for each.

---

## 2. Wiring audit (critical — catches silent failures)

This is the most important section. Wiring bugs cause silent failures (missing signals, 500s in UI, DB never written).

Check all of the following:

### Strategy registry completeness

Every strategy must appear in ALL of these locations or it silently breaks:

- `analytics/strategies/_registry.py` — `STRATEGY_REGISTRY` entry (21)
- `analytics/strategies/_registry.py` — `DETECTOR_REGISTRY` entry (19; excludes `seasonality`, `smt_divergence`, and legacy `fibonacci_retracement` — `smt_divergence` has an explicit branch in `backtest_runner.py`)
- `signals/registry.py` — `SIGNAL_REGISTRY` entry (20; all except `seasonality` and legacy `fibonacci_retracement`)
- `tests/` — at least one test for the detector function

Run this to get the cross-reference (imports the registries directly — robust to dict vs list shape):

```bash
poetry run python -c "
from analytics.strategies._registry import STRATEGY_REGISTRY, DETECTOR_REGISTRY
from signals.registry import SIGNAL_REGISTRY
strat=set(STRATEGY_REGISTRY); det=set(DETECTOR_REGISTRY); sig=set(SIGNAL_REGISTRY)
print('counts STRATEGY=%d DETECTOR=%d SIGNAL=%d' % (len(strat),len(det),len(sig)))
print('STRATEGY-DETECTOR:', sorted(strat-det))   # expect: seasonality, smt_divergence
print('DETECTOR-STRATEGY:', sorted(det-strat))   # expect: []
print('DETECTOR-SIGNAL  :', sorted(det-sig))     # expect: []
print('SIGNAL-DETECTOR  :', sorted(sig-det))     # expect: smt_divergence (explicit branch)
print('STRATEGY-SIGNAL  :', sorted(strat-sig))   # expect: seasonality (not actionable)
"
```

Compare the two lists. Flag any strategy in STRATEGY_REGISTRY but not DETECTOR_REGISTRY (or vice versa), and any in DETECTOR_REGISTRY but not SIGNAL_REGISTRY.

### Config wiring

- Does every `[strategy_params.X]` key in `config/signal_watch.toml` correspond to a real strategy name in `STRATEGY_REGISTRY`?
- Does `backtest_config.py:BacktestSweepConfig` include all flags exposed by `buibui.py` CLI?
- Does `signal_config.py:SignalWatchConfig` include all fields read from the `[backtest]` section of signal_watch.toml?

### API router completeness

- Every router in `web/api/routers/` must be imported and registered in `web/api/main.py`
- Every Pydantic model in `web/api/models/` must be used by at least one router

### Data pipeline

- `data_sync.py` syncs OHLCV — confirm it's wired into `analytics_runner.py` and `signal_runner.py`
- `upsert_signals` in `analytics/store/signals.py` (re-exported via `data_store.py`) — confirm it's called from `analytics/signal/scanner.py:run_scan_cycle()` (`signal_lib.py` is now a 4-line shim)
- `upsert_backtest_run` / `upsert_backtest_trades` — confirm called from `backtest_runner.py` when `SAVE=1`

### Thin wrapper / pure lib boundary

- `*_runner.py` files must NOT contain business logic — only: create client, open DB, call lib, close
- `*_lib.py` files must NOT import `binance_client`, make network calls, or open DB connections at module level

---

## 3. Documentation sync

Check these in parallel:

### README.md

- Does `## Usage` reflect all current `buibui` subcommands? **Do not trust a list written
  here — generate it, because this one was stale for months** (it omitted `brief`, `card`
  and `portfolio` until 2026-08-11):

  ```bash
  poetry run python buibui.py --help | sed -n '/{/,/}/p' | head -3
  ```

  As of 2026-08-11 that prints 12: `monitor`, `signal`, `analytics`, `backtest`, `brief`,
  `card`, `digest`, `param-sweep`, `param-audit`, `portfolio`, `recalibrate`, `web`.
  `signal`, `monitor` and `portfolio` are parent groups (`buibui signal watch`,
  `buibui monitor price`, `buibui portfolio replay`).
  **Count it, do not eyeball it** — README invokes the CLI as
  `poetry run python buibui.py <cmd>` for most subcommands but reaches `brief`, `card`
  and `digest` only through their `make buibui-*` wrappers, so a grep for
  `buibui <cmd>` finds 3 and reads like a catastrophe:

  ```bash
  for c in monitor signal analytics backtest brief card digest param-sweep \
           param-audit portfolio recalibrate web; do
    printf "  %-12s cli:%s make:%s\n" "$c" \
      "$(grep -c "buibui\.py $c" README.md)" "$(grep -c "make buibui-$c" README.md)"
  done
  ```

  **Measured 2026-08-23: 12 of 12 covered** — `brief`, `card`, `digest`, `param-sweep` and
  `param-audit` reach README only through their `make buibui-*` wrappers, which is why the
  `buibui.py <cmd>` half of the loop reads 0 for each. Run both halves; a zero in one
  column is not a gap.
- Does `## Directory Structure` list all current top-level modules?
- Are any sections referencing removed features?

### AGENTS.md

- Does `## Project Structure` match actual files on disk?
- Do the `## Key Commands` / `## CLI` sections still resolve?

### CLAUDE.md

- **Do NOT compare skills against a table in `CLAUDE.md` — there isn't one, deliberately.**
  `CLAUDE.md`'s `## Agent Skills` section says so explicitly: the harness injects every
  skill's name and description into each session, so a duplicate table there could only
  ever drift *behind* the real thing. This check asked for that comparison until
  2026-08-11 and was unrunnable as written.

  What is still worth checking:

  ```bash
  ls .claude/skills/*/SKILL.md
  ```

  Confirm each directory holds a `SKILL.md` with frontmatter, and that the rules
  `CLAUDE.md` *does* carry about skills (the `/frontend-design` pairing, the cadence
  reminders, `/wfo-sweep` as the trusted `tp_r` path, never `&&`-chaining `/card`) still
  match the skills they describe.

### MEMORY.md

Path: `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`

- Is **Current State** up to date with recent changes?
- Are completed items marked ✅ in the To-Do List?
- Are any open questions resolved that should be cleared?

---

## 4. Skills freshness audit

Each skill in `~/.claude/skills/` documents a workflow. Skills can go stale when the codebase evolves. Check:

For each skill, verify the **key claims** are still true:

| Skill | What to verify |
| ------- | --------------- |
| `atr-sweep` | `--atr-sl-values` CLI flag exists in `cli/backtest.py`; `format_atr_sl_sweep_table` exported from `analytics/backtest/` (re-exported via `backtest_lib.py` shim) |
| `volume-sweep` | `volume_suppress` field in `BacktestSweepConfig`; `effective_volume_suppress(strategy)` on `BacktestSweepConfig` |
| `backtest-findings` | Min-trades thresholds still match `recalibrate_lib.py` defaults |
| `recalibrate` | `buibui recalibrate` subcommand wired in `buibui.py`; `--config` + `--apply` flags present; `confidence_ratings` DB table exists |
| `new-strategy` | 4-file checklist still accurate; `DETECTOR_REGISTRY` is still the single source of truth |
| `signal-watch` | `buibui signal watch` subcommand exists; TOML field names match `signal_config.py`; `min_avg_r` (not `filter_threshold`) in the `[backtest]` section — **of the inherited base `config/strategy_params.toml`, NOT of the three day configs**, which carry no `[backtest]` section at all and reach it via `extends` |
| `pr-summary` | Template sections match what's in the skill body |
| `backtest-run` | All CLI flags listed match what `buibui backtest --help` outputs |
| `stats-dashboard` | Card count matches actual Stats.svelte; live vs cached split still accurate |
| `investigate-strategy` | `make buibui-signal-test` Makefile target exists; `--at` UTC interpretation still correct |

Flag any stale claims and update the skill file.

---

## 5. Architecture review (use code-reviewer agent)

Launch a `feature-dev:code-reviewer` agent with this checklist:

- **Dead code**: Unused imports, functions, variables, or orphaned files not referenced anywhere?
- **Duplicate logic**: Any logic duplicated between modules that should be shared?
- **Type annotations**: All public functions annotated (including `-> None` for tests)?
- **Hardcoded values**: Magic numbers/strings that should be constants or config?
- **TODO/FIXME markers**: Any stale markers to clean up?

```bash
grep -rn "TODO\|FIXME" --include="*.py" . | grep -v ".venv"
```

---

## Output format

Report results as a table with one row per check:

| # | Dimension | Check | Status | Action needed |
| --- | ----------- | ------- | -------- | --------------- |
| 1 | CI | lint-py | ✅ | — |
| 2 | CI | typecheck | ✅ | — |
| 3 | CI | test | ✅ | — |
| 4 | CI | lint-md | ✅ | — |
| 5 | CI | poetry check | ✅ | — |
| 6 | Wiring | Strategy registry cross-ref | ✅/❌ | ... |
| 7 | Wiring | Config fields | ✅/❌ | ... |
| 8 | Wiring | API router registration | ✅/❌ | ... |
| 9 | Wiring | Thin wrapper boundary | ✅/❌ | ... |
| 10 | Docs | README subcommands | ✅/❌ | ... |
| 11 | Docs | AGENTS.md structure | ✅/❌ | ... |
| 12 | Docs | MEMORY.md current state | ✅/❌ | ... |
| 13 | Skills | Skill files vs CLAUDE.md table | ✅/❌ | ... |
| 14 | Skills | Stale claims audit | ✅/❌ | ... |
| 15 | Arch | Dead code / duplicates | ✅/❌ | ... |

At the end:

- List all ❌ items with concrete next steps
- Update MEMORY.md: add today's sanity check date and any open findings
- **Stamp the cadence marker — final step, not optional:**

  ```bash
  date -u +%FT%TZ > docs/plans/task-marks/sanity-check
  ```

  `daily_check.py` reads this to decide whether the weekly cadence is overdue.
  **Skipping the stamp is indistinguishable from never running the check** — a
  missing marker reads as overdue by design, so the only cost of forgetting is a
  false alarm, and the only cost of stamping without running is a real alarm
  suppressed. Stamp *after* the work, never before.
