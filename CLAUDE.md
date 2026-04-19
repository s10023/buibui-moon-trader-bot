# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Project Overview

Buibui Moon Trader Bot — a crypto trading bot for Binance Futures with live price monitoring and position tracking. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** code changes, always run these checks:

```bash
# Format Python code
make lint-py

# Type check
make typecheck

# Run tests
make test
```

For Markdown changes:

```bash
make lint-md
```

## Project Structure

- `buibui.py` — CLI entry point (argparse)
- `monitor/` — monitor modules split into thin wrappers and pure logic libs:
  - `price_monitor.py` / `position_monitor.py` — thin wrappers (create client, load config, call lib)
  - `price_lib.py` / `position_lib.py` — pure business logic with dependency injection (no module-level side effects)
  - `live_price.py` — WebSocket + Rich live mode for price monitor
  - `live_position.py` — WebSocket + Rich live mode for position monitor
- `analytics/` — analytics data layer (DuckDB-backed). See `.claude/context/analytics.md` for full module API reference.
  - `data_store.py` — DB schema, upsert/query helpers, `confidence_ratings`, combo tables, `DEFAULT_DB_PATH`; `BacktestSnapshot` duck-type; `backtest_cache` table with `get/put/prune_backtest_cache`
  - `data_fetcher.py` / `data_sync.py` / `analytics_runner.py` — fetch, sync orchestration, thin runner
  - `indicators_lib.py` — 21 active strategies; `STRATEGY_REGISTRY`, `DETECTOR_REGISTRY`, `StrategySpec`, `INCOMPATIBLE_PAIRS`
  - `backtest_lib.py` — `Trade`, `BacktestResult`, `run_backtest`; volume tiers, directional splits, D10 combo results
  - `backtest_runner.py` / `backtest_config.py` — thin runner + TOML config loader for sweep mode
  - `param_sweep.py` — WFO sweep lib; `run_param_sweep` / `run_strategy_audit`; parallelized via `ProcessPoolExecutor`
  - `digest_lib.py` — 12 pre-canned SQL queries; `run_digest`; `DigestScope`; powers `buibui digest` + analysis API
  - `cme_gap_lib.py` — CME gap detection + alert warning helper
  - `zones_lib.py` — structural zone extraction (geometry only): FVG, OB, EQH/EQL, BOS, Fib, OTE, swing points
  - `signal_lib.py` — `scan_symbol` + `run_scan_cycle` (3-phase fan-out); ADR/volume gates; co-fire detection; L1→L2→compute backtest cache (`_bt_mem_cache`, `_reset_bt_cache`)
  - `signal_config.py` — `SignalWatchConfig`, `BacktestFilterConfig`, `BiasConfig`, `ComboConfig`; TOML `extends` support
  - `stats_lib.py` — P1/P2, ADR, DOW, session, weekly stats; live fields via `_inject_live_fields()`
  - `signal_runner.py` — daemon thin wrapper; OHLCV cache; combo lookup refresh every 10 cycles
  - `signal_test_runner.py` — historical replay: no DB writes, no cooldown; `--at` / `--lookback`
  - `recalibrate_lib.py` / `recalibrate_runner.py` — compute + write star ratings to DB or source
  - `perf_timer.py` — `timed(label)` context manager
- `signals/` — signal detection daemon package. See `.claude/context/signals.md` for full reference.
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (19 actionable strategies; `seasonality`/`funding_reversion`/`fibonacci_retracement` excluded)
  - `cooldown_store.py` — two-layer dedup: candle watermark + cooldown timer; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent`, `StatsContext`, `ConfluenceData`; 6-section alert layout; W1–W8 candle warnings
  - `DEFAULT_DB_PATH` lives in `data_store.py` — import from there, do not redefine in runners
  - **CRITICAL**: `_upsert` uses explicit `conn.register`/`conn.unregister` in try/finally — never switch to implicit replacement scan (causes malloc heap corruption). Never drop the try/finally.
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
- `web/` — web layer (Phase 4 + 5). See `.claude/context/web.md` for full API + UI reference.
  - `api/` — FastAPI: routers (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones); `GET /api/active-config`, `GET /api/zones`, `GET /api/backtest/analysis`; stats live fields via `_inject_live_fields()`
  - `ui/` — Svelte 5 + Vite; pages: Chart, Backtest, SignalFeed, Positions, Prices, Stats; build: `make web-build`
- `tests/` — pytest suite; tests import from lib modules and pass mock dependencies directly
- `config/coins.json` — per-symbol leverage and stop-loss config
- `config/strategy_params.toml` — shared base config inherited by the three main signal_watch configs via `extends = "strategy_params.toml"`; contains `[smt_pairs]`, `[bias]`, `[backtest]` defaults, per-strategy `volume_suppress`/`volume_spike_boost` flags, and `tp_r_long`/`tp_r_short` directional overrides for `morning_evening_star`, `pin_bar`, `inside_bar` (Gate 3 phase 1, 200d WFO); `conservative`/`scalping`/`swing` do not extend this (different `[bias]`/`[backtest]` values)

## Code Style

- **Linter + Formatter**: ruff (replaces black; handles linting, import sorting, and formatting)
- **Type checker**: mypy (strict — `disallow_untyped_defs = true`)
- **All functions must have type annotations** including return types (`-> None` for test methods)
- **Markdown linter**: markdownlint-cli2
- Use `from typing import Any` for mock parameters in tests

## Testing

- Framework: pytest + unittest.mock
- Tests must not make real network calls — lib functions accept a `client` parameter; tests pass a `MagicMock` directly
- Analytics tests use `duckdb.connect(":memory:")` for full DB isolation — never touch the real `analytics.db`
- Run: `make test` or `poetry run pytest tests/ -v`
- **Regression tests**: `make test-regression` — compares backtest pipeline output to golden JSON files in `tests/fixtures/`; skips if fixture parquets are absent; run `make regression-update` to regenerate golden files after intentional changes

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames), `pyarrow` (parquet fixture I/O)
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Session Memory Protocol

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Do not wait to be asked.

Fields to keep current:

- Last session summary (one line: what changed)
- Open questions / pending decisions (or "none")

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (project-specific, committed to repo) and are invoked with `/skill-name`. Each encapsulates a recurring workflow so you don't need to re-explain it. Use them proactively.

| Skill | Invoke | When to use | Cadence |
| ----- | ------ | ----------- | ------- |
| `sanity-check` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness | Weekly or after any large refactor |
| `atr-sweep` | `/atr-sweep` | Find optimal ATR SL multiplier per strategy × TF; translates to `atr_sl_multiplier` TOML overrides | After any SL-related change or when backtests show high fee drag |
| `wfo-sweep` | `/wfo-sweep` | **Full automated WFO chain**: param-audit → param-sweep → apply → backtest → recalibrate → commit. One command to refresh all tp_r for a config. | When a config feels stale or after any major strategy/detector change |
| `config-refresh` | `/config-refresh` | Full TOML refresh: fix strategy_timeframes gaps, run TP sweep, update tp_r per strategy × TF, commit | When a signal_watch config feels stale, after detector rewrites, or when weekdays config drifts behind signal_watch.toml |
| `backtest-findings` | `/backtest-findings` | Interpret any sweep table (ATR/TP/volume/duration) and commit winners to TOML | After every sweep run |
| `param-sweep-apply` | `/param-sweep-apply` | Auto-apply WFO param-sweep/param-audit results: parse pasted tables, pick best tp_r per strategy × TF, edit TOML, run backtest + recalibrate | Paste results and invoke — use when running sweeps manually outside `/wfo-sweep` |
| `recalibrate` | `/recalibrate` | Update strategy star ratings in `indicators_lib.py` from DB backtest runs | After any `make buibui-backtest SAVE=1` adds new runs |
| `volume-sweep` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (indicators_lib, DETECTOR_REGISTRY, signals/registry, tests) | Every time a new strategy is added |
| `backtest-run` | `/backtest-run` | Quick reference for all `buibui backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `investigate-strategy` | `/investigate-strategy` | Debug why a strategy did/didn't fire on a specific candle using `buibui signal test` | When asked to investigate, diagnose, or replay a signal |
| `signal-watch` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch` | `/post-branch` | Check CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml for needed updates | After every branch — run automatically without being asked |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
