---
name: recalibrate
description: "Update strategy star ratings from backtest results. Always use this after any `make buibui-backtest SAVE=1` run — star ratings won't reflect new data until recalibration runs. Also use when the user asks about confidence scores, star ratings, or strategy quality, or wants to apply recalibration."
disable-model-invocation: true
---

# Recalibrate Strategy Star Ratings

Updates strategy confidence (star) ratings in the `confidence_ratings` DB table based on accumulated
backtest results in `analytics.db`. Ratings feed the Backtest UI stars, Telegram alerts, and
the live signal filter's quality gate.

## What recalibration does

1. Reads `backtest_runs` table — aggregates avg_r per `(strategy, timeframe)` across all symbols
2. Maps avg_r → 1–5 stars (see thresholds below) — combined, long, and short directions
3. Dry-run (default): prints a diff of old vs new ratings
4. `--apply` with `--config`: writes combined + directional (long/short) stars to `confidence_ratings` DB table, keyed by `(config_name, strategy, tf, direction)`
5. `--apply` without `--config`: legacy fallback — patches `confidence=N` directly in `indicators_lib.py`

**Prefer `--config` path** — it keeps ratings per-config and doesn't touch source code.

Stars flow through to:
- Backtest UI tab → `stars`, `long_stars`, `short_stars` columns (JOINed from `confidence_ratings` at query time)
- `GET /api/strategies?config=<name>` → per-config star overrides served to the UI
- Telegram alerts → star display in signal alerts
- `signal_lib` hard mode gate → suppresses signals below `min_avg_r` threshold

## Star rating thresholds

```
avg_r < 0          → 1★
0   ≤ avg_r < 0.2  → 2★
0.2 ≤ avg_r < 0.5  → 3★
0.5 ≤ avg_r < 0.9  → 4★
avg_r ≥ 0.9        → 5★
```

Strategies with fewer trades than `--min-trades` are skipped (rating unchanged).
Default `--min-trades`: 10 combined; 5 directional (splits have fewer trades per direction).

## CLI commands

```bash
# Dry-run (default) — shows diff, no changes
buibui recalibrate --config config/signal_watch.toml

# Apply — writes to confidence_ratings DB table keyed by config_name
buibui recalibrate --config config/signal_watch.toml --apply

# Legacy apply (no --config) — patches confidence=N in indicators_lib.py source
buibui recalibrate --apply

# Adjust minimum trade threshold
buibui recalibrate --config config/signal_watch.toml --min-trades 20 --apply

# Make alias (dry-run, no --config)
make buibui-recalibrate
```

## Standard workflow

```bash
# 1. Run backtest and save
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1

# 2. Dry-run to preview changes
buibui recalibrate --config config/signal_watch.toml

# 3. Apply if the diff looks correct
buibui recalibrate --config config/signal_watch.toml --apply

# 4. Restart signal watch daemon to pick up new ratings
```

## What the output looks like

```
Strategy Recalibration Report
══════════════════════════════════════════════════════════
  Strategy              TF    Trades  Win%  Avg R   Old★  New★  L★  S★
  ──────────────────────────────────────────────────────────────────────
  engulfing             1h       328   58%  +0.42R   3★  → 3★    3   3  (unchanged)
  fib_golden_zone       4h        34   62%  +1.42R   3★  → 5★    5   4  ★ CHANGED
  liquidity_sweep       1h       201   44%  -0.08R   3★  → 1★    1   2  ★ CHANGED
  pin_bar               1h       175   61%  +0.51R   4★  → 4★    4   3  (unchanged)
  ...

  Dry-run mode — no changes applied. Use --apply to write to confidence_ratings table.
```

## Implementation files

| File | Role |
|------|------|
| `analytics/recalibrate_lib.py` | `compute_recalibrated_ratings()`, `compute_directional_ratings()`, `write_confidence_to_db()`, `write_confidence_to_source()` (legacy) |
| `analytics/recalibrate_runner.py` | Thin wrapper: opens DB, calls lib, prints report; `--config` derives `config_name`, `day_filter`, `adr_suppress_threshold` |
| `analytics/data_store.py` | `confidence_ratings` table: PK `(config_name, strategy, tf, direction)` |
| `buibui.py` | `buibui recalibrate [--config FILE] [--apply] [--min-trades N]` |
