---
name: sanity-check
effort: high
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

It quotes no performance figure, so the era rule other skills carry (`analytics.eras`)
does not apply here.

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

Start the suite FIRST, in the background (~5 min on Linux, ~10 on the Windows laptop), then run
the rest beside it:

```bash
make test                      # background it — pytest
poetry run ruff format --check .
poetry run ruff check .
make typecheck                 # mypy strict
make lint-md                   # markdownlint-cli2
poetry check                   # lockfile consistency
```

⚠ **Use the ruff `--check` forms here, not `make lint-py`.** `lint-py` runs `ruff format .`, which
REWRITES files, and AGENTS.md forbids editing the Python tree while a suite runs: the suite imports
modules at collection time, so a green run can describe a tree that no longer exists. The check forms
read and never write.

Report pass/fail for each. On the Windows host, compare `make test`'s failures **by name** against the
known list in memory `windows-host-migration.md`. Read the names, never the count.

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
PYTHONPATH=. poetry run python - <<'EOF'
from analytics.strategies._registry import STRATEGY_REGISTRY, DETECTOR_REGISTRY
from signals.registry import SIGNAL_REGISTRY
strat=set(STRATEGY_REGISTRY); det=set(DETECTOR_REGISTRY); sig=set(SIGNAL_REGISTRY)
print('counts STRATEGY=%d DETECTOR=%d SIGNAL=%d' % (len(strat),len(det),len(sig)))
print('STRATEGY-DETECTOR:', sorted(strat-det))   # expect: seasonality, smt_divergence
print('DETECTOR-STRATEGY:', sorted(det-strat))   # expect: []
print('DETECTOR-SIGNAL  :', sorted(det-sig))     # expect: []
print('SIGNAL-DETECTOR  :', sorted(sig-det))     # expect: smt_divergence (explicit branch)
print('STRATEGY-SIGNAL  :', sorted(strat-sig))   # expect: seasonality (not actionable)
print('REGISTRY-CHECK-DONE')
EOF
```

⛔ **No `REGISTRY-CHECK-DONE` line means the check never ran — not that it passed.** The earlier
`poetry run python -c "…"` form printed NOTHING and exited quietly under Git Bash on Windows (measured
2026-09-28, SoT ST149), which read exactly like a clean cross-reference. The heredoc feeds the same code
on stdin, and the sentinel turns silence into a visible failure.

Compare the two lists. Flag any strategy in STRATEGY_REGISTRY but not DETECTOR_REGISTRY (or vice versa), and any in DETECTOR_REGISTRY but not SIGNAL_REGISTRY.

### Config wiring

- Does every `[strategy_params.X]` key in `config/signal_watch.toml` correspond to a real strategy name in `STRATEGY_REGISTRY`?
- Does `backtest_config.py:BacktestSweepConfig` include all flags exposed by `buibui.py` CLI?
- Does `signal_config.py:SignalWatchConfig` include all fields read from the `[backtest]` section of signal_watch.toml?

### API router completeness

- Every router in `web/api/routers/` must be imported and registered in `web/api/main.py`
- Every Pydantic model in `web/api/models/` must be referenced by a router OR by another model —
  most are nested response fields, so a routers-only grep reports ~29 false "unused" models

### Data pipeline

- `data_sync.py` syncs OHLCV — confirm it's wired into `analytics/analytics_runner.py` and
  `analytics/signal_runner.py` (the runners live under `analytics/`, not the repo root)
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

  `signal`, `monitor` and `portfolio` are parent groups (`buibui signal watch`,
  `buibui monitor price`, `buibui portfolio replay`).

  ⛔ **No count is written here, deliberately — DERIVE the list, never restate it.**
  This section carried *"As of 2026-08-11 that prints 12"* plus a loop over those twelve
  names, three paragraphs after telling you not to trust a written list. By 2026-09-06 the
  CLI printed **14** — `card-place` and `card-orders` landed with ST37 (#715) — and the loop
  had never once checked either. **Bumping the number to 14 would only re-arm the trap on
  the next subcommand**, which is why it is gone rather than corrected.

  **Count it, do not eyeball it** — README invokes the CLI as
  `poetry run python buibui.py <cmd>` for most subcommands but reaches several only
  through their `make buibui-*` wrappers, so the CLI half alone reads like a catastrophe:

  ```bash
  for c in $(poetry run python buibui.py --help 2>&1 \
             | sed -n 's/.*{\([a-z,-]*\)}.*/\1/p' | head -1 | tr ',' ' '); do
    printf "  %-12s cli:%s make:%s\n" "$c" \
      "$(grep -c "buibui\.py $c" README.md)" "$(grep -c "make buibui-$c" README.md)"
  done
  ```

  **Measured 2026-09-06: 14 of 14 covered** — `brief`, `card`, `card-place`, `card-orders`,
  `digest`, `param-sweep` and `param-audit` reach README only through their `make buibui-*`
  wrappers, which is why the `buibui.py <cmd>` half reads 0 for each. Run both halves; a
  zero in one column is not a gap.
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

Path: `$(PYTHONPATH=. poetry run python tools/memory_dir.py)/MEMORY.md` — ⚠ **resolve it, never hardcode it.** Both the config root
and the project slug vary by host; `tools/memory_dir.py` is the one resolver.

- Is **Current State** up to date with recent changes?
- Does it carry any open work — a to-do, open question or pending decision? Since
  2026-10-01 (#865) those are Issues (`question` label for questions and decisions), never
  MEMORY.md lines: file each one, then delete the line.

---

## 4. Skills freshness audit

Each skill in `~/.claude/skills/` documents a workflow. Skills can go stale when the codebase evolves. Check:

For each skill, verify the **key claims** are still true:

| Skill | What to verify |
| ------- | --------------- |
| `atr-sweep` | `--atr-sl-values` CLI flag exists in `cli/backtest.py`; `format_atr_sl_sweep_table` exported from `analytics/backtest/` (re-exported via `backtest_lib.py` shim) |
| `volume-sweep` | `volume_suppress` field in `BacktestSweepConfig`; `effective_volume_suppress(strategy)` on `BacktestSweepConfig` |
| `backtest-findings` | Min-trades thresholds still match `recalibrate_lib.py` defaults |
| `recalibrate` | `buibui recalibrate` subcommand wired in **`cli/main.py`** (`buibui.py` is a thin shim delegating to `cli.main:main` and has 0 hits — this row said `buibui.py` until 2026-09-06 and sent the grep to the wrong file); `--config` + `--apply` flags present; `confidence_ratings` DB table exists |
| `new-strategy` | 4-file checklist still accurate; `DETECTOR_REGISTRY` is still the single source of truth |
| `signal-watch` | `buibui signal watch` subcommand exists; TOML field names match `signal_config.py`; `min_avg_r` (not `filter_threshold`) in the `[backtest]` section — **of the inherited base `config/strategy_params.toml`, NOT of the three day configs**, which carry no `[backtest]` section at all and reach it via `extends` |
| `pr-summary` | Still loads `mattpocock-skills:pr` for the body shape; its output-path derivation matches the path `/post-branch` screens |
| `backtest-run` | All CLI flags listed match what `buibui backtest --help` outputs |
| `stats-dashboard` | Card count matches actual Stats.svelte; live vs cached split still accurate |
| `investigate-strategy` | `make buibui-signal-test` Makefile target exists; `--at` UTC interpretation still correct |

Flag any stale claims and update the skill file.

---

## 5. Architecture review

Run the concrete probes; each one has a tool behind it. (This section used to name a
`feature-dev:code-reviewer` agent, which is not installed here, so the step could not run as written.)

- **Unused imports / variables / annotations** — already covered by §1: `ruff check` (F401, F841) and
  `make typecheck` (mypy strict, `disallow_untyped_defs`).
- **Dead config surface** — declared cells that never fire, and ratings nobody declares. Point it at a
  snapshot copy, never the live DB:

  ```bash
  make buibui-dead-surface-check DB=<snapshot copy>
  ```

  Read every ❌ against memory `project_dead_cells_1d.md` before calling it new: it diagnoses each known
  dead cell, and `_KNOWN_DEAD_CELLS` is empty, so the same cells re-report on every run.
- **TODO/FIXME markers**, with a positive control so an empty result is proven rather than assumed:

  ```bash
  grep -rn -E "\b(TODO|FIXME)\b" --include="*.py" . | grep -v -E "^\./(\.venv|\.cache)/"
  grep -rln "DETECTOR_REGISTRY" --include="*.py" analytics | head -1   # control: MUST print a path
  ```

- **Duplicate logic and hardcoded values** have no tool. Judge them by reading the diff since the last
  run (`git log --since=<last marker> --stat`) rather than the whole tree.
- **Shallow modules and missing seams** are a deeper survey than this sweep should run. If the diff
  since the last marker shows friction (one concept spread over many small modules, logic untestable
  through its interface), recommend the operator run `/improve-codebase-architecture`; it is
  user-invoked, so this skill cannot call it.

---

## Output format

Report results as a table with one row per check:

| # | Dimension | Check | Status | Action needed |
| --- | ----------- | ------- | -------- | --------------- |
| 1 | CI | ruff format + check (`--check` forms) | ✅ | — |
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
| 13 | Skills | Every skill dir has `SKILL.md` + frontmatter | ✅/❌ | ... |
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
