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
  - `data_store.py` — pure DB lib: schema init, upsert (ohlcv/funding/OI/signals), range queries; `upsert_signals(conn, df)` persists fired signals; `get_signals_history(conn, symbol, tf, start_ms, end_ms)` reads them back
  - `data_fetcher.py` — pure fetch lib: Binance Futures API → DataFrames (no DB concerns)
  - `data_sync.py` — pure orchestration: paginated backfill + incremental sync
  - `analytics_runner.py` — thin wrapper: creates client, opens DB, calls sync lib
  - `indicators_lib.py` — pure strategy signal detection (22 strategies: seasonality, wick_fill, marubozu, orb, liquidity_sweep, fvg, bos, funding_reversion, smt_divergence, eqh_eql, order_block, cvd_divergence, trend_day, engulfing, pin_bar, inside_bar, hammer_hanging_man, doji, morning_evening_star, fibonacci_retracement, fib_golden_zone, ote_entry); also exports `ParamSpec`, `StrategySpec`, `STRATEGY_REGISTRY` (param metadata for all strategies) and `KNOWN_STRATEGIES`
  - `backtest_lib.py` — pure backtest engine: Trade, BacktestResult, run_backtest, format helpers
  - `backtest_runner.py` — thin wrapper: opens DB, loads OHLCV/funding, calls indicator + backtest libs
  - `signal_lib.py` — pure scan lib: `scan_symbol()` (runs strategies on one symbol/tf), `run_scan_cycle()` (fans out, deduplicates, sends Telegram, persists passing signals to DB via `upsert_signals`)
  - `signal_runner.py` — thin wrapper: creates client, opens DB, syncs candles, polls `run_scan_cycle` in a loop
  - `backtest_config.py` — `BacktestSweepConfig` + `load_backtest_config()` — TOML config loading for sweep mode
- `signals/` — signal detection daemon package:
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (21 actionable strategies; seasonality excluded)
  - `cooldown_store.py` — two-layer dedup: candle watermark per `(symbol, tf, strategy)` + cooldown timer per `(symbol, strategy, direction)`; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent` dataclass + `format_signal_alert()` → Markdown Telegram message with SL/TP levels
  - `DEFAULT_DB_PATH` lives in `data_store.py` — import from there, do not redefine in runners
  - `_upsert` uses explicit `conn.register` / `conn.unregister` in a try/finally — do NOT switch to the implicit `conn.execute("... FROM df")` replacement scan; it holds a raw C pointer without Py_INCREF and causes malloc heap corruption at `conn.close()` after multiple batches. Do NOT drop the try/finally; unregister must always run or the stale registration causes the same crash.
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
- `web/` — web layer (Phase 4 + 5):
  - `api/` — FastAPI backend: `main.py` (app + StaticFiles mount), `deps.py` (`require_token`, `require_token_sse` for SSE query-param auth), `routers/` (config, ohlcv, fib, signals, backtest, positions, prices, stream), `models/` (Pydantic models for each router)
  - `ui/` — Svelte 5 + Vite frontend: `src/api.ts` (typed client), `src/stores/` (config, strategies, prices SSE, positions SSE), `src/pages/` (Chart, Backtest, SignalFeed, Positions, Prices), `src/components/` (Nav, CandleChart, BacktestResult, …). Build: `make web-build` → `web/ui/dist/` served by FastAPI StaticFiles.
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

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
