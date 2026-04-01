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
- `analytics/` — analytics data layer (DuckDB-backed):
  - `data_store.py` — pure DB lib: schema init, upsert (ohlcv/funding/OI/signals/backtest), range queries; `upsert_signals(conn, df)` persists fired signals; `get_signals_history(conn, symbol, tf, start_ms, end_ms)` reads them back; `list_backtest_runs(conn)` returns all saved runs newest-first with a `stars` column JOINed from `confidence_ratings` by `(strategy, tf, day_filter)` — each row gets the calibrated stars matching its own day_filter; `upsert_backtest_run` / `upsert_backtest_trades` persist backtest results; `upsert_confidence_ratings(conn, config_name, ratings, win_rates, day_filter=None)` + `get_confidence_ratings(conn, config_name)` store/load per-config star ratings from the `confidence_ratings` table; `day_filter` stored in `confidence_ratings` enables the JOIN in `list_backtest_runs`
  - `data_fetcher.py` — pure fetch lib: Binance Futures API → DataFrames (no DB concerns)
  - `data_sync.py` — pure orchestration: paginated backfill + incremental sync
  - `analytics_runner.py` — thin wrapper: creates client, opens DB, calls sync lib
  - `indicators_lib.py` — pure strategy signal detection (21 active strategies: seasonality, wick_fill, marubozu, orb, liquidity_sweep, fvg, bos, funding_reversion, smt_divergence, eqh_eql, order_block, cvd_divergence, trend_day, engulfing, pin_bar, inside_bar, hammer_hanging_man, doji, morning_evening_star, fib_golden_zone, ote_entry); `fibonacci_retracement` is legacy/commented-out (superseded by `fib_golden_zone`); also exports `ParamSpec`, `StrategySpec`, `STRATEGY_REGISTRY`, `DETECTOR_REGISTRY`, `KNOWN_STRATEGIES`; `StrategySpec.confidence` is `dict[str, int] | int` — use `get_confidence(tf)` to resolve per-TF rating (falls back to `"default"` key then `3`)
  - `backtest_lib.py` — pure backtest engine: Trade, BacktestResult, run_backtest, format helpers; fee drag uses actual trade risk (`2 * fee_pct * entry / risk`); `min_sl_pct` widens structural SLs that land too close to entry (prevents fee-drag explosion); detectors called once on full OHLCV history — rolling detectors (fib_golden_zone, ote_entry, order_block, eqh_eql, cvd_divergence) generate signals at every historical candle; last-candle-only detectors (candlestick patterns) fire at most once per backtest run; `BacktestResult` exposes per-direction split: `long_closed_trades`, `long_win_count`, `long_win_rate`, `long_avg_r` + short equivalents
  - `backtest_runner.py` — thin wrapper: opens DB, loads OHLCV/funding, calls indicator + backtest libs
  - `signal_lib.py` — pure scan lib: `scan_symbol()` (runs strategies on one symbol/tf), `run_scan_cycle()` (fans out, deduplicates, sends Telegram, persists passing signals to DB via `upsert_signals`); both accept `confidence_override: dict[str, dict[str, int]] | None` — when provided, overrides `indicators_lib.py` star ratings with per-config DB values; `_compute_backtest()` respects `fee_pct`, `day_filter`, and `min_sl_pct` from `BacktestFilterConfig`; `_compute_stats_context()` computes per-symbol `StatsContext` once per cycle and passes it into alert formatting
  - `stats_lib.py` — pure stats lib: `compute_p1p2_daily`, `compute_hourly_extremes` (incl. `peak_high/low_hour_by_dow` per-DOW MODE), `compute_adr`, `compute_dow_patterns` (incl. `avg_return_pct`), `compute_session_breakdown`, `compute_weekly_p1p2`, `compute_weekly_p2_timing` (→ `WeeklyP2Timing` with `low/high_still_ahead_by_dow` + `low/high_flip_risk_by_dow`), `compute_all` → `StatsBundle`; all times in MYT (UTC+8) using `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP`; raises `ValueError` on empty data
  - `signal_runner.py` — thin wrapper: creates client, opens DB, syncs candles, polls `run_scan_cycle` in a loop; all TOML params (`sl_pct`, `cooldown_seconds`, `fee_pct`, `day_filter`) are wired through
  - `signal_config.py` — `BacktestFilterConfig` (includes `fee_pct`, `min_sl_pct`, and `min_avg_r`; each loaded from `[backtest].*` falling back to top-level); hard mode gates on `min_avg_r` (directional avg_r ≥ threshold) — replaces old win-rate `filter_threshold` + `SignalWatchConfig` + `load_signal_config()`; `SymbolOverride` dataclass holds per-symbol tp_r/sl_pct/atr_sl overrides within a strategy block; `StrategyOverride.per_symbol` maps symbol → `SymbolOverride`; resolution order: `symbol+TF → symbol → TF → strategy → global`
  - `backtest_config.py` — `BacktestSweepConfig` (includes `min_sl_pct`, `liq_sweep_use_fib`) + `load_backtest_config()` — TOML config loading for sweep mode; supports `--day-filter` CLI flag; `liq_sweep_use_fib` toggles `liquidity_sweep` between fib-extension mode (default) and pivot-sweep mode for backtest comparison; also exports `SymbolOverride` (same per-symbol override dataclass as `signal_config.py`)
  - `recalibrate_lib.py` — pure recalibration lib: `get_backtest_win_rates(conn)`, `win_rate_to_stars(avg_r, total_trades)`, `compute_recalibrated_ratings(conn, min_trades)` → `dict[str, dict[str, int]]` (per TF), `format_recalibration_report(old, new, win_rates)`; reads `backtest_runs` table, maps avg R → 1–5 stars per `(strategy, tf)`; `get_backtest_win_rates` uses only the latest run per `(strategy, tf, symbol)` — older param-sweep variants excluded; `write_confidence_to_db(conn, config_name, ratings, win_rates, day_filter=None)` persists per-config stars to DB including `day_filter` so Backtest tab can JOIN correct stars per row (preferred); `write_confidence_to_source` patches `indicators_lib.py` source directly (legacy — still works for global fallback)
  - `recalibrate_runner.py` — thin wrapper: opens DB, calls lib, prints diff report; `--config <toml>` derives `day_filter` + `config_name` from TOML; `--apply` with `--config` writes to `confidence_ratings` DB table keyed by config name; `--apply` without `--config` falls back to legacy source patching of `indicators_lib.py`; wired as `buibui.py recalibrate` subcommand and `make buibui-recalibrate`
- `signals/` — signal detection daemon package:
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (20 actionable strategies; seasonality + legacy fibonacci_retracement excluded); `confidence` field removed — resolved per-TF at dispatch time via `STRATEGY_REGISTRY[name].get_confidence(tf)`
  - `cooldown_store.py` — two-layer dedup: candle watermark per `(symbol, tf, strategy)` + cooldown timer per `(symbol, strategy, direction)`; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent` + `StatsContext` dataclasses; `format_signal_alert()` → Markdown Telegram message with SL/TP levels + optional 2-line stats footer (line 1: `📐 bull%, direction-aware P1 text, ADR`; line 2: `⏰ per-DOW peak hour, weekly P2 timing`); `_format_stats_line(ctx, direction)` is direction-aware (LONG vs SHORT)
  - `DEFAULT_DB_PATH` lives in `data_store.py` — import from there, do not redefine in runners
  - `_upsert` uses explicit `conn.register` / `conn.unregister` in a try/finally — do NOT switch to the implicit `conn.execute("... FROM df")` replacement scan; it holds a raw C pointer without Py_INCREF and causes malloc heap corruption at `conn.close()` after multiple batches. Do NOT drop the try/finally; unregister must always run or the stale registration causes the same crash.
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
- `web/` — web layer (Phase 4 + 5):
  - `api/` — FastAPI backend: `main.py` (app + StaticFiles mount), `deps.py` (`require_token`, `require_token_sse` for SSE query-param auth), `routers/` (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats), `models/` (Pydantic models for each router); backtest router exposes `GET /api/backtest/runs` (all saved runs) and `POST /api/backtest` (run + auto-save); `BacktestRunSummary` has `stars: int | None` (resolved per-row via `confidence_ratings` JOIN) and validators to coerce pandas NaN → None for nullable columns; config router exposes `GET /api/strategies?config=<name>` to return confidence values overridden with per-config DB ratings; stats router exposes `GET /api/stats/{symbol}?days=180` (cached daily per symbol in `stats_cache` table)
  - `ui/` — Svelte 5 + Vite frontend: `src/api.ts` (typed client), `src/stores/` (config, strategies, prices SSE, positions SSE), `src/pages/` (Chart, Backtest, SignalFeed, Positions, Prices, Stats), `src/components/` (Nav, CandleChart, BacktestResult, …). Build: `make web-build` → `web/ui/dist/` served by FastAPI StaticFiles. Backtest page: DB-backed sortable/filterable table loads on mount; collapsible run form; stars per row from `BacktestRunSummary.stars` (JOINed by `day_filter` at query time — no manual config selector needed); long/short win rate and avg R columns with sort and filter. Stats page: 8-card grid (P1/P2, ADR, hourly distribution, DOW patterns, session breakdown, weekly P1/P2, avg return by day, weekly P2 timing with flip risk). Default lookback: 365d.
- `tests/` — pytest suite; tests import from lib modules and pass mock dependencies directly
- `config/coins.json` — per-symbol leverage and stop-loss config

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

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames)
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

Skills live in `.claude/skills/` (project-specific, committed to repo) and are invoked with `/skill-name`. Each encapsulates a recurring workflow so you don't need to re-explain it. Use them proactively.

| Skill | Invoke | When to use | Cadence |
| ----- | ------ | ----------- | ------- |
| `sanity-check.md` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness | Weekly or after any large refactor |
| `atr-sweep.md` | `/atr-sweep` | Find optimal ATR SL multiplier per strategy × TF; translates to `atr_sl_multiplier` TOML overrides | After any SL-related change or when backtests show high fee drag |
| `tp-sweep.md` | `/tp-sweep` | Find optimal TP ratio per strategy × TF; translates to `tp_r` TOML overrides | After adding a new strategy or TF; re-run after any entry logic change |
| `backtest-findings.md` | `/backtest-findings` | Interpret any sweep table (ATR/TP/volume/duration) and commit winners to TOML | After every sweep run |
| `param-sweep-apply.md` | `/param-sweep-apply` | Auto-apply WFO param-sweep/param-audit results: parse pasted tables, pick best tp_r per strategy × TF, edit TOML, run backtest + recalibrate | Paste results and invoke — replaces the manual `/backtest-findings` loop |
| `recalibrate.md` | `/recalibrate` | Update strategy star ratings in `indicators_lib.py` from DB backtest runs | After any `make buibui-backtest SAVE=1` adds new runs |
| `volume-sweep.md` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy.md` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (indicators_lib, DETECTOR_REGISTRY, signals/registry, tests) | Every time a new strategy is added |
| `backtest-run.md` | `/backtest-run` | Quick reference for all `buibui backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `signal-watch.md` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary.md` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch.md` | `/post-branch` | Check CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml for needed updates | After every branch — run automatically without being asked |
| `stats-dashboard.md` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
