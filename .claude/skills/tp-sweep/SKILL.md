---
name: tp-sweep
description: "Find optimal TP ratio per strategy × TF. Use after adding a new strategy or TF, or after entry logic changes."
disable-model-invocation: true
---

# TP Ratio Sweep

Run a `tp_r_values` sweep to find the optimal take-profit ratio per strategy × TF.
Prints a "TP Ratio Comparison" table — strategy × TF rows, tp_r columns, avg R cells.

## How it works

- **Signal detection runs once** — tp_r only affects when TP is hit, not which signals fire.
- For each tp_r value, the same set of detected signals is re-run through backtest simulation.
- Per-strategy `tp_r` overrides from `[strategy_params.X]` are **ignored** in sweep mode — the sweep explores tp_r space globally. `sl_pct` and `atr_sl_multiplier` overrides still apply.
- Sweep is TOML-only: there is no `--tp-r-values` CLI flag. Use `--tp-r` for a single fixed value.

## Config / TOML

Add to any backtest TOML (e.g. `config/signal_watch.toml`):

```toml
# TP sweep — prints comparison table instead of ranked sweep
tp_r_values = [1.0, 1.5, 2.0, 2.5, 3.0]

# Once you've found the winner, commit it globally or per-strategy:
# tp_r = 2.0                              # global default
# [strategy_params.engulfing]
# tp_r = 3.0                              # strategy-wide override
# tp_r_4h = 2.5                           # TF-specific override
```

## CLI

```bash
# Sweep via config (only way to run a sweep)
make buibui-backtest CONFIG=config/signal_watch.toml

# Single fixed tp_r (no sweep)
buibui backtest --config config/signal_watch.toml --tp-r 3.0

# Single-combo mode with fixed tp_r
buibui backtest --symbol BTCUSDT --strategy engulfing --interval 1h --tp-r 3.0

# After finding winners — re-run and save to DB
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1
```

## Output format

```
TP Ratio Comparison (aggregated across symbols)
══════════════════════════════════════════════════════════
  Strategy              TF      1.0R    1.5R    2.0R    2.5R    3.0R
  ──────────────────────────────────────────────────────────────────
  engulfing             15m   +0.01R  +0.03R  +0.05R  +0.05R  +0.06R
                        1h    +0.08R  +0.12R  +0.16R  +0.18R  +0.19R
                        4h    +0.10R  +0.18R  +0.22R  +0.24R  +0.25R
  pin_bar               1h    +0.09R  +0.14R  +0.19R  +0.23R  +0.26R
  ...
  ──────────────────────────────────────────────────────────────────
  Pick the tp_r column where avg R peaks per strategy × TF row.
```

## Reading the results

1. Find the column where avg R peaks for each strategy × TF row — that's your optimal tp_r.
2. Strategies where avg R is monotonically increasing across all columns probably need values beyond 3.0R — consider extending the sweep range.
3. Strategies where avg R is flat or negative across all tp_r values are unlikely to benefit from TP tuning alone; check volume suppression or TF restrictions instead.
4. Respect min_trades thresholds before trusting a result: 15m→30, 1h→20, 4h→10, 1d→5 (from TOML).
5. After finding winners, set `tp_r` overrides in `[strategy_params.X]` and re-run with `SAVE=1`.

## Per-strategy TOML override syntax

Lookup order: TF-specific key → strategy-wide key → global `tp_r`.

```toml
[strategy_params.engulfing]
tp_r = 3.0          # applies to all TFs unless overridden below

[strategy_params.fib_golden_zone]
tp_r_4h = 3.0       # 4h only; other TFs fall back to global tp_r
tp_r_1h = 2.0
```

## Key F6 findings (stored in memory)

Full table: `project_f6_tp_sweep_findings.md`

Quick reference (weekdays config, 200d, BTCUSDT/ETHUSDT/SOLUSDT):
- **Most candlestick patterns peak at 3.0R** — but only at 1h and 4h, not 15m
- **15m is a dead zone** for candlestick patterns: barely positive even at 3.0R
- `fib_golden_zone 4h`: +1.42R at 3.0R (34 trades) — monotonically increases
- `bos 4h`: peaks at 2.5R, drops at 3.0R
- `liquidity_sweep`, `marubozu`, `smt_divergence`, `orb`: TP tuning doesn't help much

## Implementation files

| File | Role |
|------|------|
| `analytics/backtest_lib.py` | `format_tp_sweep_table()` — comparison table formatter |
| `analytics/backtest_lib.py` | `run_backtest()` — uses tp_r per trade |
| `analytics/backtest_runner.py` | `tp_sweep_mode` branch in sweep logic |
| `analytics/backtest_config.py` | `tp_r_values: list[float]` on `BacktestSweepConfig` |
| `config/signal_watch.toml` | `[strategy_params.*]` — committed F6 findings |

## Task: run TP sweep

When the user asks to run a TP sweep or find optimal tp_r values:

1. Confirm which config file to use (default: `config/signal_watch.toml`)
2. Add `tp_r_values = [1.0, 1.5, 2.0, 2.5, 3.0]` to the TOML (or check if already present)
3. Run: `make buibui-backtest CONFIG=<file>`
4. Read the output table — identify peak column per strategy × TF
5. Translate findings into `tp_r` values in `[strategy_params.X]`
6. Re-run with `SAVE=1` to persist the winning config to DB
7. Optionally run `buibui recalibrate --apply` to update star ratings
