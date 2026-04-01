---
name: recalibrate
description: "Update strategy star ratings in DB from backtest runs. Run after any make buibui-backtest SAVE=1."
disable-model-invocation: true
---

# Recalibrate Strategy Star Ratings

Update strategy confidence (star) ratings in `indicators_lib.py` based on accumulated backtest results in `analytics.db`.

## What recalibration does

1. Reads the `backtest_runs` table in `analytics.db`
2. Groups by `(strategy, timeframe)` — aggregates win_rate and avg_r across all symbols
3. Maps avg_r → 1–5 stars using fixed thresholds (see below)
4. Dry-run: prints a diff of old vs new ratings (default)
5. `--apply`: patches `confidence=N` values directly in `analytics/indicators_lib.py` source

The patched `confidence` values flow through to:
- `STRATEGY_REGISTRY` → exported to the web API
- Telegram alerts → star display in signal alerts
- UI Backtest tab → star column in strategy table

## Star rating formula

```
avg_r < 0        → 1★
0 <= avg_r < 0.2 → 2★
0.2 <= avg_r < 0.5 → 3★
0.5 <= avg_r < 0.9 → 4★
avg_r >= 0.9     → 5★
```

Strategies with fewer trades than `--min-trades` threshold are skipped (rating unchanged).

## CLI commands

```bash
# Dry-run (default) — shows what would change, no file modifications
buibui recalibrate

# Apply — patches confidence=N in indicators_lib.py
buibui recalibrate --apply

# Adjust minimum trade threshold (default: 10)
buibui recalibrate --min-trades 20 --apply

# Make alias (dry-run)
make buibui-recalibrate
```

## When to run

Run after any backtest that saves results to DB:

```bash
# 1. Run backtest and save
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1

# 2. Dry-run to preview rating changes
buibui recalibrate

# 3. If the changes look correct, apply them
buibui recalibrate --apply

# 4. Restart signal watch to pick up new ratings (if running)
#    The patched file is read at startup — no in-memory-only patch
```

## What the output looks like

```
Strategy Recalibration Report
══════════════════════════════════════════════════════════
  Strategy              TF    Trades   Win%   Avg R   Old★  New★
  ──────────────────────────────────────────────────────────────
  engulfing             1h    328      58%    +0.42R   3★  → 3★  (unchanged)
  fib_golden_zone       4h    34       62%    +1.42R   3★  → 5★  ★ CHANGED
  liquidity_sweep       1h    201      44%    -0.08R   3★  → 1★  ★ CHANGED
  pin_bar               1h    175      61%    +0.51R   4★  → 4★  (unchanged)
  ...
  ──────────────────────────────────────────────────────────────

  Dry-run mode — no changes applied. Use --apply to write ratings to indicators_lib.py.
```

## After applying

```bash
# Verify the patch landed correctly
grep "confidence=" analytics/indicators_lib.py | head -30

# Restart signal watch daemon if running
# (kill + restart — new confidence values take effect on next startup)
```

## Implementation files

| File | Role |
|------|------|
| `analytics/recalibrate_lib.py` | `get_backtest_win_rates()`, `win_rate_to_stars()`, `compute_recalibrated_ratings()`, `write_confidence_to_source()` |
| `analytics/recalibrate_runner.py` | Thin wrapper: opens DB, calls lib, prints report |
| `analytics/indicators_lib.py` | Source of truth for `confidence=N` values (patched by `--apply`) |
| `buibui.py` | `buibui recalibrate [--apply] [--min-trades N]` subcommand |

## Task: run recalibration

When the user asks to recalibrate star ratings or update confidence scores:

1. Confirm backtest data is in DB: `make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1` first if not done
2. Run dry-run: `buibui recalibrate`
3. Review the diff — check for surprising changes (e.g. a strategy dropping from 4★ to 1★ on small sample)
4. If results look correct: `buibui recalibrate --apply`
5. Verify patch: `grep "confidence=" analytics/indicators_lib.py`
6. Note: `--min-trades 10` (default) may be too low for 4h/1d strategies; consider `--min-trades 20` for 15m-heavy configs
