---
name: volume-sweep
description: "Test volume_suppress flag per strategy — compare High Vol vs Low Vol avg R. Use when adding a strategy or after entry logic changes."
disable-model-invocation: true
---

# Volume Suppression Testing

Test whether filtering low-volume candles improves strategy performance. Uses the `volume_suppress` flag in TOML.

## What volume_suppress does

Suppresses signals where the signal candle's volume is below 1.5× the 20-candle rolling mean. Low-volume candles are considered noise — signal fires during illiquid conditions where follow-through is less reliable.

The "High Vol" vs "Low Vol" split table is **always printed** in backtest output regardless of whether `volume_suppress` is enabled. This lets you assess impact before committing to the filter.

## How to test

### Method 1: Read the volume split table (no config change needed)

Run any backtest and look for the "Volume Split" section in the output:

```bash
make buibui-backtest CONFIG=config/signal_watch.toml
```

The split table shows:
```
  Strategy              TF    High Vol   Low Vol   Δ (High - Low)
  bos                   1h    +0.31R     +0.10R    +0.21R   ← suppress
  pin_bar               1h    +0.18R     +0.37R    -0.19R   ← do NOT suppress
  engulfing             1h    +0.19R     +0.17R    +0.02R   ← neutral
```

Decision threshold: |Δ| > 0.10R is meaningful; < 0.05R is noise.

### Method 2: Enable globally and compare

```toml
# config/signal_watch.toml — temporary test, revert after
[backtest]
volume_suppress = true
```

Then re-run and compare avg R vs without suppression.

## Where to set volume_suppress

### Global sweep (backtest only)
```toml
# Top-level in backtest TOML (affects all strategies in the sweep)
volume_suppress = true
```

### Live daemon (signal watch)
```toml
# Under [backtest] sub-table in signal_watch TOML
[backtest]
volume_suppress = true   # suppresses low-vol signals before sending alerts
```

### Per-strategy (NOT YET IMPLEMENTED — A14b pending)

Per-strategy `volume_suppress` in `[strategy_params.X]` is not yet implemented as of 2026-03-28. Currently only global suppression is available. A14b will add `volume_suppress: bool | None` to `StrategyOverride`.

## Key A13 findings

Tested at global `tp_r=2.0`, weekdays + tue_thu configs, 200d, 3 symbols:

**Suppress — clear benefit (Δ > +0.10R):**
| Strategy | Benefit |
|----------|---------|
| bos | +0.21R / +0.18R |
| orb | +0.18R / +0.18R |
| fib_golden_zone | +0.04R / +0.19R |
| morning_evening_star | +0.10R / +0.05R |
| trend_day | +0.08R / +0.03R |

**Do NOT suppress (low-vol signals outperform):**
| Strategy | Penalty |
|----------|---------|
| pin_bar | -0.19R / -0.02R |
| hammer_hanging_man | -0.17R / -0.16R |
| liquidity_sweep | -0.11R / -0.01R |

**Neutral:** engulfing, doji, inside_bar, eqh_eql, smt_divergence

**Note:** These findings used global `tp_r=2.0`. The A14 per-strategy tp_r changes may shift the deltas — re-run before enabling. See `project_a13_volume_findings.md`.

## Workflow

```bash
# 1. Run sweep to see volume split table
make buibui-backtest CONFIG=config/signal_watch.toml

# 2. Review High Vol vs Low Vol Δ per strategy × TF

# 3a. If testing global suppression, temporarily add to TOML and re-run
# 3b. For production: wait for A14b per-strategy suppression (not yet implemented)

# 4. Once decided, commit TOML change
# For now, only global suppression under [backtest] is available
```

## Implementation files

| File | Role |
|------|------|
| `analytics/backtest_lib.py` | `format_volume_split()` — volume split table; `run_backtest()` — `volume_suppress` flag filters signals |
| `analytics/backtest_config.py` | `volume_suppress: bool` on `BacktestSweepConfig` |
| `analytics/signal_config.py` | `volume_suppress: bool` on `BacktestFilterConfig` (daemon) |
| `config/signal_watch.toml` | `[backtest].volume_suppress` — set here for live daemon |

## Task: run volume suppression analysis

When the user asks whether to enable volume suppression:

1. Run: `make buibui-backtest CONFIG=config/signal_watch.toml`
2. Read the "Volume Split" section — note strategies where |Δ| > 0.10R
3. Cross-reference with A13 findings in `project_a13_volume_findings.md`
4. Note: findings at old tp_r=2.0 may differ from current per-strategy tp_r — validate
5. For now, only global `volume_suppress = true` under `[backtest]` is available
6. Per-strategy suppression requires A14b implementation first
