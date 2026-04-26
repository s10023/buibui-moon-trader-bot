---
name: atr-sweep
description: >
  Find the optimal ATR SL multiplier per strategy × timeframe by running a sweep
  and translating the winner into `atr_sl_multiplier` TOML overrides.
  Invoke when the user says "/atr-sweep", asks about "atr_sl_multiplier",
  "ATR-based stops", "fee drag", or any stop-loss sizing tune — and after
  any SL-related code change.
allowed-tools: Bash, Read, Edit, Write
---

# ATR SL Multiplier Sweep

Run an ATR SL multiplier sweep to find the optimal `atr_sl_multiplier` value per strategy × TF.
Prints a comparison table (like `tp_r_values`) showing avg R at each multiplier value.

## How it works

- **Signal detection runs once** — the ATR multiplier only affects SL placement, not which signals fire.
- For each multiplier value, every signal's SL = `N × ATR14` at the signal candle.
- `min_sl_pct` still applies — widens any ATR-derived SL that lands too close to entry.
- SL priority per trade: structural SL (e.g. pivot low from `liquidity_sweep`) → ATR-based → fixed `sl_pct`.
- Per-strategy `tp_r` overrides from `[strategy_params]` still apply during the sweep.

## Config / TOML

Add to any backtest TOML (e.g. `config/signal_watch.toml`):

```toml
# ATR SL sweep — prints comparison table (like tp_r_values)
atr_sl_multiplier_values = [0.5, 1.0, 1.5, 2.0, 2.5]

# Once you've found the winner, commit it:
# atr_sl_multiplier = 2.0
```

Per-strategy override (goes inside `[strategy_params.STRATEGY]`):
```toml
[strategy_params.liquidity_sweep]
atr_sl_multiplier = 1.2        # strategy-wide
atr_sl_multiplier_1h = 0.8     # 1h-specific override
```

## CLI

```bash
# Sweep via config
make buibui-backtest CONFIG=config/signal_watch.toml

# Sweep via CLI flags (no TOML needed)
buibui backtest --config config/signal_watch.toml --atr-sl-values 0.5 1.0 1.5 2.0 2.5

# Single fixed multiplier
buibui backtest --config config/signal_watch.toml --atr-sl-multiplier 2.0

# Single-combo mode
buibui backtest --symbol BTCUSDT --strategy bos --interval 1h --atr-sl-multiplier 1.5
```

## Output format

```
ATR SL Multiplier Comparison (aggregated across symbols)
══════════════════════════════════════════════════════════
  Strategy              TF      0.5×    1.0×    1.5×    2.0×    2.5×
  ──────────────────────────────────────────────────────────────────
  bos                   15m   -0.05R  +0.12R  +0.18R  +0.14R  +0.09R
                        1h    +0.08R  +0.22R  +0.31R  +0.28R  +0.19R
  liquidity_sweep       1h    +0.15R  +0.38R  +0.45R  +0.41R  +0.33R
  ...
  ──────────────────────────────────────────────────────────────────
  Pick the multiplier column where avg R peaks per strategy × TF row.
```

## Reading the results

1. Find the column where avg R peaks for each strategy × TF row — that's your optimal multiplier.
2. Strategies with structural SLs (liquidity_sweep, order_block, eqh_eql) may see ATR override the structural SL only when the structural SL is absent — check trade count changes across columns.
3. If avg R is flat across multipliers, the strategy likely has mostly structural SLs; ATR isn't the binding constraint.
4. After finding winners, set `atr_sl_multiplier` (or per-strategy override) in TOML and re-run with `SAVE=1`:
   ```bash
   make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1
   ```

## Implementation files

| File | What changed |
|------|-------------|
| `analytics/backtest_lib.py` | `format_atr_sl_sweep_table()` — comparison table formatter |
| `analytics/backtest_lib.py` | `_compute_atr14()` — ATR14 at signal candle index |
| `analytics/backtest_lib.py` | `run_backtest()` — ATR SL path (structural → ATR → sl_pct) |
| `analytics/backtest_runner.py` | `atr_sweep_mode` branch in `run_backtest_sweep()` |
| `analytics/backtest_config.py` | `atr_sl_multiplier_values: list[float]` on `BacktestSweepConfig` |
| `analytics/signal_config.py` | `atr_sl_multiplier` on `SignalWatchConfig` + `StrategyOverride` |
| `buibui.py` | `--atr-sl-values` and `--atr-sl-multiplier` CLI flags |

## Which config to sweep?

Two production signal_watch configs exist:
- `config/signal_watch.toml` — `day_filter = "tue_thu"` (Tue–Thu only)
- `config/signal_watch_weekdays.toml` — `day_filter = "weekdays"` (Mon–Fri)

**Default: sweep `signal_watch.toml` only.** ATR sizing is a volatility question, not a day-of-week question — one sweep gives the baseline. The two configs already diverge in their `[strategy_params]` tp_r overrides (calibrated separately in F6), which absorbs most of the Mon/Fri difference.

**Run both when:** the user explicitly asks, OR after live use shows weekdays underperforming tue_thu (Mon/Fri candles are wider/more volatile and may warrant a different multiplier).

When sweeping both: run sequentially, produce separate findings, and write per-strategy `atr_sl_multiplier` overrides into each TOML independently.

## Task: run ATR sweep

When the user asks to run an ATR sweep or find optimal ATR multipliers:

1. Ask: "Which config — `signal_watch.toml` (tue_thu), `signal_watch_weekdays.toml`, or both?" Default to `signal_watch.toml` if not specified.
2. Suggest range: `[0.5, 1.0, 1.5, 2.0, 2.5]` for swing/1h+; `[0.3, 0.5, 0.8, 1.0, 1.5]` for scalping
3. Add `atr_sl_multiplier_values = [...]` to the chosen TOML (or use `--atr-sl-values` CLI flag to avoid editing the file)
4. Run: `make buibui-backtest CONFIG=<file>`
5. Read the output table — identify peak column per strategy × TF
6. Translate findings into `atr_sl_multiplier` values in `[strategy_params.X]` of the same TOML
7. Re-run with `SAVE=1` to persist the winning config to DB
8. If sweeping both configs: repeat steps 3–7 for the second config independently
