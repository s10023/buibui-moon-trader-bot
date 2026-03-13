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
- `analytics/` — analytics data layer (DuckDB-backed):
  - `data_store.py` — pure DB lib: schema init, upsert (ohlcv/funding/OI), range queries
  - `data_fetcher.py` — pure fetch lib: Binance Futures API → DataFrames (no DB concerns)
  - `data_sync.py` — pure orchestration: paginated backfill + incremental sync
  - `analytics_runner.py` — thin wrapper: creates client, opens DB, calls sync lib
  - `indicators_lib.py` — pure strategy signal detection (9 strategies: seasonality, wick_fill, marubozu, orb, liquidity_sweep, fvg, bos, funding_reversion, smt_divergence)
  - `backtest_lib.py` — pure backtest engine: Trade, BacktestResult, run_backtest, format helpers
  - `backtest_runner.py` — thin wrapper: opens DB, loads OHLCV/funding, calls indicator + backtest libs
  - `DEFAULT_DB_PATH` lives in `data_store.py` — import from there, do not redefine in runners
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
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
