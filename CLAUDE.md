# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Project Overview

Buibui Moon Trader Bot — a crypto trading bot for Binance Futures. Live price + position monitoring, an analytics/backtest stack (DuckDB), a 20-strategy signal engine with Telegram alerts, and a FastAPI + Svelte web UI. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** Python code change:

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite
```

For Markdown changes: `make lint-md`.

For UI / API changes: `make web-build` (production bundle) or `make web-dev` (Vite dev server).

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update`).

## CLI

`buibui.py` is the single CLI entry point with subcommands:

- `buibui monitor price | position` — live price / position monitor
- `buibui signal watch | test` — live signal daemon / historical replay
- `buibui analytics backfill | sync` — OHLCV ingestion
- `buibui backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `buibui digest` — pre-canned analytics queries
- `buibui param-audit | param-sweep` — WFO parameter tools
- `buibui recalibrate` — refresh star ratings
- `buibui web` — start FastAPI backend

Each Makefile `buibui-*` target wraps the equivalent CLI invocation.

## Project Structure

- `buibui.py` — thin CLI entry shim (delegates to `cli.main:main`)
- `cli/` — argparse subcommand package: `main.py` builds the top-level parser and dispatches to per-subcommand modules (`monitor.py`, `signal.py`, `analytics.py`, `backtest.py`, `digest.py`, `param.py`, `recalibrate.py`, `web.py`); `_common.py` shared helpers
- `monitor/` — monitor modules split into thin wrappers and pure logic libs:
  - `price_monitor.py` / `position_monitor.py` — thin wrappers (create client, load config, call lib)
  - `price_lib.py` / `position_lib.py` — pure business logic with dependency injection (no module-level side effects)
  - `live_price.py` — WebSocket + Rich live mode for price monitor
  - `live_position.py` — WebSocket + Rich live mode for position monitor
- `analytics/` — analytics data layer (DuckDB-backed). See `.claude/context/analytics.md` for full module API reference.
  - `store/` — DB layer split into 8 modules: `schema.py` (`init_schema`, `DEFAULT_DB_PATH`), `market_data.py` (OHLCV / funding / OI upsert), `signals.py` (`upsert_signals`, `get_signals_history`, `upsert_signal_outcome`), `backtest_runs.py` (`upsert_backtest_run`, `upsert_backtest_trades`, `list_backtest_runs`, `get_win_rate_by_strategy`), `backtest_cache.py` (`BacktestSnapshot`, `get/put/prune_backtest_cache`), `confidence.py` (`upsert_confidence_ratings`, combined + directional getters), `combos.py` (combo + cross-TF combo upsert/list/lookup), `stats_cache.py`. `_common.py` holds the sealed `_upsert` register/unregister helper. `data_store.py` is a thin re-export shim for the 30+ external import sites.
  - **CRITICAL**: `_upsert` (in `store/_common.py`) uses explicit `conn.register`/`conn.unregister` in try/finally — never switch to implicit replacement scan (causes malloc heap corruption). Never drop the try/finally.
  - `data_fetcher.py` / `data_sync.py` / `analytics_runner.py` — fetch, sync orchestration, thin runner
  - `strategies/` — strategy signal detection package (one file per `detect_*` function): 21 detector modules (`wick_fills.py`, `marubozu_retest.py`, `orb_breakout.py`, `liquidity_sweep.py`, `fvg.py`, `market_structure.py` = `bos`, `funding_extreme.py`, `smt_divergence.py`, `eqh_eql.py`, `order_block.py`, `cvd_divergence.py`, `trend_day.py`, `engulfing.py`, `pin_bar.py`, `inside_bar.py`, `hammer_hanging_man.py`, `doji.py`, `morning_evening_star.py`, `fibonacci_retracement.py` legacy, `fib_golden_zone.py`, `ote_entry.py`); `_base.py` (`ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`); `_shared.py` (`_find_bos_swing`, `volume_confirm`, `_empty_signals`, `_signals_to_df`, `_fmt_time`); `_seasonality.py` (`seasonality_stats`, `SEASONALITY_COLUMNS`); `_registry.py` (explicit-tuple-driven assembler holding `STRATEGY_REGISTRY` (20 entries), `DETECTOR_REGISTRY` (18; `seasonality` / `smt_divergence` / legacy `fibonacci_retracement` excluded), `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS`, `patch_confidence_scores`); `__init__.py` eager re-exports. `indicators_lib.py` is a re-export shim for the 30+ external import sites.
  - `backtest/` — backtest engine split into 6 modules: `engine.py` (`Trade`, `BacktestResult`, `run_backtest`, `_compute_atr14`), `gates.py` (`_is_low_volume`, `_is_volume_spike`, `filter_signals_by_day`), `combo.py` (`ComboBacktestResult`, `run_combo_backtest`), `cross_tf.py` (`CrossTfComboBacktestResult`, `run_cross_tf_combo_backtest`), `formatters.py` (10× `format_*` helpers + `_tf_sort_key`). `backtest_lib.py` is a thin re-export shim.
  - `backtest_runner.py` / `backtest_config.py` — thin runner + TOML config loader for sweep mode
  - `param_sweep.py` — WFO sweep lib; `run_param_sweep` / `run_strategy_audit`; parallelized via `ProcessPoolExecutor`
  - `digest_lib.py` — 12 pre-canned SQL queries; `run_digest`; `DigestScope`; powers `buibui digest` + analysis API
  - `cme_gap_lib.py` — CME gap detection + alert warning helper
  - `zones_lib.py` — structural zone extraction (geometry only): FVG, OB, EQH/EQL, BOS, Fib, OTE, swing points
  - `signal/` — signal scanner split into 8 modules: `scanner.py` (`scan_symbol` + `run_scan_cycle` 3-phase fan-out), `types.py` (`SignalEvent`, `StatsContext`, `ConfluenceData`), `gates.py` (`_filter_signals_by_adr`, `_is_adr_exempt`), `resolvers.py` (9× `_resolve_*` helpers), `bt_cache.py` (`_compute_backtest`, `_backtest_summary`), `stats_context.py` (`_compute_stats_context`), `cofire.py` (live + cross-TF co-fire detection), `_common.py` (`_bt_mem_cache`, `_reset_bt_cache`, timeframe parsing). `signal_lib.py` is a 4-line re-export shim.
  - `signal_config.py` — `SignalWatchConfig`, `BacktestFilterConfig`, `BiasConfig`, `ComboConfig`; TOML `extends` support
  - `stats/` — stats package split per dimension: `bundle.py` (top-level `compute_all` orchestrator), `p1p2.py`, `adr.py`, `dow.py`, `hourly.py`, `session.py`, `daily_distance.py`, `weekly_state.py`, `weekly_p1p2.py`, `weekly_p2_timing.py`, `weekly_flip_risk.py`, `weekly_wick.py`. `_common.py` shared helpers; live fields injected by `bundle._inject_live_fields()`. `stats_lib.py` is a re-export shim.
  - `signal_runner.py` — daemon thin wrapper; OHLCV cache; combo lookup refresh every 10 cycles
  - `signal_test_runner.py` — historical replay: no DB writes, no cooldown; `--at` / `--lookback`
  - `recalibrate_lib.py` / `recalibrate_runner.py` — compute + write star ratings to DB or source
  - `perf_timer.py` — `timed(label)` context manager
- `signals/` — signal detection daemon package (alerting + dedup only — detection lives in `analytics/`). See `.claude/context/signals.md` for full reference.
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (19 actionable strategies; `seasonality` / `fibonacci_retracement` excluded)
  - `cooldown_store.py` — two-layer dedup: candle watermark + cooldown timer; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent`, `StatsContext`, `ConfluenceData`; 6-section alert layout; W1–W8 candle warnings
  - `DEFAULT_DB_PATH` lives in `analytics/store/schema.py` (re-exported via `analytics.data_store`) — import from either, do not redefine in runners
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
- `web/` — web layer (Phase 4 + 5). See `.claude/context/web.md` for full API + UI reference.
  - `api/` — FastAPI: routers (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones); `GET /api/active-config`, `GET /api/zones`, `GET /api/backtest/analysis`; stats live fields via `_inject_live_fields()`
  - `ui/` — Svelte 5 + Vite; pages: Chart, Backtest, SignalFeed, Positions, Prices, Stats; build: `make web-build`
- `trade/open_trades.py` — Binance Futures order opener (manual/CLI use; wired via `make buibui-open-trades`). No automation hooked into the signal daemon yet.
- `tests/` — pytest suite; tests import from lib modules and pass mock dependencies directly
- `.claude/context/` — long-form module references (`analytics.md`, `signals.md`, `web.md`) split out to keep this file lean
- `config/coins.json` — per-symbol leverage and stop-loss config (gitignored; see `coins.json.example`)
- `config/strategy_params.toml` — shared base config inherited via `extends = "strategy_params.toml"` by `signal_watch.toml`, `signal_watch_all.toml`, `signal_watch_weekdays.toml`. Contains `[smt_pairs]`, `[bias]`, `[backtest]` defaults, per-strategy `volume_suppress` / `volume_spike_boost` flags, and `tp_r_long` / `tp_r_short` directional overrides. `conservative.toml` / `scalping.toml` / `swing.toml` do **not** extend it — they carry their own `[bias]` / `[backtest]` values.

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

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Do not wait to be asked.

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
| `recalibrate` | `/recalibrate` | Update strategy star ratings in the `confidence_ratings` DB table from accumulated backtest runs (feeds Backtest UI stars, Telegram alerts, live signal-watch quality gate) | After any `make buibui-backtest SAVE=1` adds new runs |
| `volume-sweep` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (`analytics/strategies/<name>.py`, `_registry.py`, `signals/registry.py`, tests) | Every time a new strategy is added |
| `backtest-run` | `/backtest-run` | Quick reference for all `buibui backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `investigate-strategy` | `/investigate-strategy` | Debug why a strategy did/didn't fire on a specific candle using `buibui signal test` | When asked to investigate, diagnose, or replay a signal |
| `signal-watch` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch` | `/post-branch` | Behaviour-gated docs sweep: diff branch changes against CLAUDE.md / README.md / MEMORY.md / Makefile / docker-compose.yml / `.claude/context/`, propose targeted edits, append "Documentation updates" to PR body. Skips for pure refactors. | Immediately after `gh pr create`, before reporting the PR URL |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |
| `db-update` | `/db-update` | Routine `make db-update`: backtest (3 configs) → recalibrate → regression golden refresh | After any detector / strategy / config change that affects ratings or fixtures |
| `data-backfill` | `/data-backfill` | OHLCV ingestion via `buibui analytics backfill` / `sync` | First-time setup, wiped DB, new symbol or timeframe, filling a data gap |
| `confluence-backtest` | `/confluence-backtest` | Cross-TF (`--cross-tf`) and same-TF (`--combo`) co-firing backtests; HTF/LTF pair sweeps | After adding a strategy, changing entry logic, or tuning the live `[combo]` gate |
| `frontend-svelte` | `/frontend-svelte` | Svelte 5 + Vite UI workflow for `web/ui/` — pages, stores, lightweight-charts, dev/build commands | Any work under `web/ui/`; pair with `/frontend-design` for visual work |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
