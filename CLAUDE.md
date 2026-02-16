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
- `monitor/` — price_monitor.py, position_monitor.py (both have module-level side effects: Binance client init, coins.json read)
- `utils/` — config_validation.py, telegram.py
- `tests/` — pytest suite with conftest.py that patches Binance client at import time
- `config/coins.json` — per-symbol leverage and stop-loss config

## Code Style

- **Linter + Formatter**: ruff (replaces black; handles linting, import sorting, and formatting)
- **Type checker**: mypy (strict — `disallow_untyped_defs = true`)
- **All functions must have type annotations** including return types (`-> None` for test methods)
- **Markdown linter**: markdownlint-cli2
- Use `from typing import Any` for mock parameters in tests

## Testing

- Framework: pytest + unittest.mock
- Tests must not make real network calls — all Binance API access is mocked in `tests/conftest.py` at import time
- Run: `make test` or `poetry run pytest tests/ -v`

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
