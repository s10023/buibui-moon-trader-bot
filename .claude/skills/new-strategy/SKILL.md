---
name: new-strategy
description: "Guided 4-file checklist for adding a new trading strategy (indicators_lib, signals/registry, backtest_runner, tests)."
disable-model-invocation: true
---

# New Strategy Wiring Checklist

Guided workflow for adding a new trading strategy to buibui. All 4 locations must be updated together or the web UI will 500 on the strategy.

## The 4 mandatory files

### 1. `analytics/indicators_lib.py` — detector + registry

```python
# 1a. Add detector function
def detect_my_strategy(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Returns df with columns: open_time (int), direction (str), reason (str), sl_price (float), context (str)."""
    ...

# 1b. Add to STRATEGY_REGISTRY (at top of file, alphabetical or grouped)
STRATEGY_REGISTRY: dict[str, StrategySpec] = {
    ...
    "my_strategy": StrategySpec(
        name="my_strategy",
        description="One-line description for UI display.",
        params=[...],       # ParamSpec entries, or empty list
        confidence=3,       # 1–5 editorial score; recalibrate --apply updates this
    ),
}

# 1c. Add to DETECTOR_REGISTRY (at bottom of file)
DETECTOR_REGISTRY: dict[str, Callable[..., pd.DataFrame]] = {
    ...
    "my_strategy": detect_my_strategy,
}
# KNOWN_STRATEGIES is auto-built from STRATEGY_REGISTRY.keys() — no manual update needed.
```

### 2. `signals/registry.py` — live signal daemon

```python
from analytics.indicators_lib import detect_my_strategy, STRATEGY_REGISTRY

SIGNAL_REGISTRY: dict[str, SignalPlugin] = {
    ...
    "my_strategy": SignalPlugin(
        name="my_strategy",
        description=STRATEGY_REGISTRY["my_strategy"].description,
        detector=detect_my_strategy,
        confidence=STRATEGY_REGISTRY["my_strategy"].confidence,
    ),
}
```

### 3. `tests/test_indicators.py` (or new `tests/test_my_strategy.py`)

```python
import duckdb
from unittest.mock import MagicMock
from analytics.indicators_lib import detect_my_strategy

def test_my_strategy_long() -> None:
    conn = duckdb.connect(":memory:")
    # Build synthetic OHLCV DataFrame
    ohlcv = pd.DataFrame({...})
    result = detect_my_strategy(ohlcv)
    assert not result.empty
    assert result["direction"].iloc[0] == "LONG"

def test_my_strategy_no_signal() -> None:
    # Test edge case: flat market → no signals
    ...
```

Rules:
- Use `duckdb.connect(":memory:")` — never touch `analytics.db`
- Pass `MagicMock` client via dependency injection (lib functions accept `client` param)
- No real network calls

## Strategy function signature

```python
def detect_X(
    ohlcv: pd.DataFrame,
    # Optional: extra params your strategy needs
) -> pd.DataFrame:
```

Returns DataFrame with columns:
- `open_time` (`int`) — Unix ms timestamp of the signal candle
- `direction` (`str`) — `"LONG"` or `"SHORT"`
- `reason` (`str`) — human-readable description for alerts
- `sl_price` (`float`) — structural SL price (0.0 if using fixed sl_pct instead)
- `context` (`str`) — extra context for Telegram alert formatting

## Strategies needing extra data (NOT in DETECTOR_REGISTRY)

Strategies that require funding rates or a secondary symbol get explicit `if strategy == ...` branches in `backtest_runner.py` instead of being auto-wired:

- `funding_reversion` — needs funding rate data from `get_funding_rates(conn, ...)`
- `smt_divergence` — needs a secondary symbol OHLCV from `get_ohlcv(conn, secondary_symbol, ...)`

For these, also update `backtest_runner.detect_signals_for_strategy()` with a new branch, and exclude from `DETECTOR_REGISTRY`.

## After adding the strategy

```bash
# Run tests
make test

# Typecheck (all functions must be annotated)
make typecheck

# Format
make lint-py

# Run a quick single-symbol backtest to confirm signals fire
buibui backtest --symbol BTCUSDT --strategy my_strategy --interval 1h

# Run full sweep and save to DB
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1

# Recalibrate star ratings from DB results
buibui recalibrate --apply
```

## Adding to active signal watch config

After confirming the strategy has positive avg R in backtest:

```toml
# config/signal_watch.toml
strategies = [
    ...,
    'my_strategy',
]

# Optionally restrict to specific TFs
[strategy_timeframes]
my_strategy = ["1h", "4h"]

# Optionally set optimal tp_r from sweep
[strategy_params.my_strategy]
tp_r = 3.0
```

## Implementation files reference

| File | What to update |
|------|----------------|
| `analytics/indicators_lib.py` | `detect_X()`, `STRATEGY_REGISTRY`, `DETECTOR_REGISTRY` |
| `signals/registry.py` | `SIGNAL_REGISTRY` entry |
| `tests/test_indicators.py` | Unit tests for the new detector |
| `analytics/backtest_runner.py` | Only for strategies needing funding/secondary data |

## Task: add a new strategy

When the user asks to add a new strategy:

1. Ask for: strategy name, signal logic (entry condition, SL logic, direction), any extra data requirements
2. Write `detect_<name>()` in `indicators_lib.py` — pure function, no side effects
3. Add `StrategySpec` entry to `STRATEGY_REGISTRY` (confidence=3 default)
4. Add to `DETECTOR_REGISTRY` (or explicit branch in `backtest_runner.py` if needs extra data)
5. Add `SignalPlugin` entry to `signals/registry.py`
6. Write at least 2 tests: one that fires a signal, one edge case that produces no signal
7. Run `make lint-py && make typecheck && make test`
8. Run quick backtest: `buibui backtest --symbol BTCUSDT --strategy <name> --interval 1h`
9. If positive results, add to `config/signal_watch.toml` strategies list
