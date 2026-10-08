---
name: backtest-run
effort: low
description: >
  Quick reference for every `buibui backtest` CLI flag and `make buibui-backtest`
  invocation — sweep, combo, cross-TF, save, since, day-filter, ATR, fees.
  Invoke when the user says "/backtest-run", asks to "run a backtest",
  "what's the flag for X", or wants to plan a sweep / combo / cross-TF run.
allowed-tools: Bash, Read
---

# Backtest Run — Quick Reference

Common `buibui backtest` invocations and `make buibui-backtest` targets.

## Most common invocations

### Full sweep (all symbols × strategies × TFs from config)

```bash
make buibui-backtest CONFIG=config/signal_watch.toml

# With specific config variants
make buibui-backtest CONFIG=config/signal_watch_weekdays.toml
make buibui-backtest CONFIG=config/signal_watch_all.toml
```

### Full sweep + save results to DB

```bash
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1
```

Saves to `backtest_runs` and `backtest_trades` tables in `analytics.db`. Required before `buibui recalibrate` can update star ratings.

### Single symbol + strategy + TF

```bash
buibui backtest --symbol BTCUSDT --strategy engulfing --interval 1h
buibui backtest --symbol ETHUSDT --strategy pin_bar --interval 4h --tp-r 3.0
buibui backtest --symbol BTCUSDT --strategy bos --interval 15m --atr-sl-multiplier 1.5
```

### Single strategy, all symbols

```bash
buibui backtest --config config/signal_watch.toml --strategy engulfing
```

### Day filter (suppress Mon + Fri signals)

```bash
buibui backtest --config config/signal_watch.toml --day-filter tue_thu

# Options: off | weekdays | mon_fri | tue_thu | weekend | no_monfi (default from TOML: tue_thu)
```

### TP sweep (TOML only — no CLI flag for multi-value sweep)

```toml
# config/signal_watch.toml
tp_r_values = [1.0, 1.5, 2.0, 2.5, 3.0]
```

```bash
make buibui-backtest CONFIG=config/signal_watch.toml
```

### ATR SL sweep

```bash
# Via TOML — needs both keys (floor is required; without it the sweep is a no-op for structural strategies)
# atr_sl_multiplier_values = [0.5, 1.0, 1.5, 2.0, 2.5]
# atr_sl_floor = true
make buibui-backtest CONFIG=config/signal_watch.toml

# Via CLI — always pass --atr-sl-floor
buibui backtest --config config/signal_watch.toml --atr-sl-floor --atr-sl-values 0.5 1.0 1.5 2.0 2.5
```

### Stable anchored window (recommended for saved runs)

```bash
buibui backtest --config config/signal_watch.toml --since 2025-09-12 --save
```

### Custom lookback window

```bash
buibui backtest --symbol BTCUSDT --strategy fib_golden_zone --interval 4h --days 365
# Or anchored:
buibui backtest --symbol BTCUSDT --strategy fib_golden_zone --interval 4h --since 2025-09-12
```

## All CLI flags

```text
buibui backtest
  --config FILE            TOML config file; CLI flags override TOML values
  --symbol SYMBOL          Single symbol (e.g. BTCUSDT)
  --strategy STRATEGY      Single strategy name
  --interval TF            Timeframe: 15m | 1h | 4h | 1d
  --days N                 Lookback in days (default: 200; floating window)
  --since YYYY-MM-DD       Anchor start date — use for saved/comparable runs (e.g. 2025-09-12)
  --tp-r FLOAT             Take-profit ratio (e.g. 2.0)
  --sl-pct FLOAT           Stop-loss % (e.g. 0.02)
  --atr-sl-multiplier N    ATR-based SL: N × ATR14
  --atr-sl-values N...     Multi-value ATR sweep (space-separated)
  --atr-sl-floor           Widen structural SLs by max(structural, N × ATR14) — required for ATR sweep to bite on structural strategies
  --day-filter MODE        off | weekdays | mon_fri | tue_thu | weekend | no_monfi
  --save                   Persist results to DB (same as SAVE=1)
  --min-trades N           Hide combos below N trades
  --secondary-symbol SYM   Secondary symbol for smt_divergence
  --fee-pct FLOAT          Taker fee per side, decimal (default 0.0; 0.0005 = 0.05%)
  --min-sl-pct FLOAT       Minimum SL distance as a fraction of price (0.005 = 0.5%)

  # Plural overrides — sweep a set, ignoring the config's own lists
  --symbols SYM...         Symbols to sweep (overrides --config)
  --strategies NAME...     Strategies to sweep (overrides --config)
  --timeframes TF...       Timeframes to sweep (overrides --config)

  # Confluence modes — see /confluence-backtest for how to read the output
  --combo                  Co-firing backtests across all strategy pairs
  --window N               Co-firing window: +/-N candles (default 5)
  --cross-tf               Cross-TF co-firing (HTF context + LTF entry)
  --htf-ltf PAIR...        HTF:LTF pairs, e.g. '4h:15m 4h:1h'
  --window-hours N         Cross-TF lookback in hours
  --workers N              Parallel workers for combo (default min(4, cpu-1); 1 = serial)

  # Live-parity gates — the backtest reproduces what the DAEMON would have done
  --live-parity            Master switch: enable EVERY live-only gate
  --with-<gate>            Add one gate on top of --live-parity
  --without-<gate>         Cancel one gate from --live-parity
      gates: regime | direction-filter | f8-htf-ema | adr-bias
             | conflict-resolver | cooldown
```

⚠ **`--min-sl-pct` reaches BOTH modes since 2026-09-08 — the block above used to say it
was not a flag at all, and that is now false.** It was declared only on `buibui signal`
while `cli/backtest.py` read it behind a `hasattr` the backtest parser could never
satisfy, so **single-combo ran pinned at 0.0 however it was invoked**. Sweep mode was
never affected: it builds its config through `load_backtest_config` and reads the TOML.
⚠ **Read any single-combo result predating that fix as having had NO stop floor** — the
condition ST104 measured at 0.215R → 0.427R mean drag — and do not re-derive the impact
from those runs, since they were measured through the defect. Sweep still takes the TOML
value unless the flag overrides it; single-combo still defaults to 0 = disabled.

⚠ **Gate flags change what a run MEANS, not just its speed.** A bare sweep has every
live gate OFF, so its avg_r is not comparable to a `--live-parity` run or to the live
ledger; the 2026-08-18 `bos` causality fix cut trade count ~80% under live parity alone.
Say which mode a table came from whenever you report one.

**Era rule.** One run is one code version, so a table from a run you just made needs
no era line. Comparing saved rows from different runs does: each was produced under
the code and TOMLs of its own `run_at_ms`, never its `entry_time`, which is simulated
market time. For the rated pool, `buibui recalibrate` prints that era check. For an
ad hoc set of rows, say the `run_at_ms` span before comparing them.

## Config files

| File | Description |
| ------ | ------------- |
| `config/signal_watch.toml` | Tue–Thu (`day_filter = "tue_thu"`); per-strategy tp_r from F6 sweep |
| `config/signal_watch_weekdays.toml` | Mon + Fri only (`day_filter = "mon_fri"`) |
| `config/signal_watch_all.toml` | Sat + Sun only (`day_filter = "weekend"`) |

## Viewing saved runs

Saved runs are stored in `analytics.db` in the `backtest_runs` table. View them via the web UI Backtest tab, or query directly:

```bash
# Via web API (if server is running)
curl http://localhost:8000/api/backtest/runs

# Via DuckDB CLI
duckdb analytics.db "SELECT strategy, timeframe, symbol, avg_r, closed_trades FROM backtest_runs ORDER BY created_at DESC LIMIT 20"
```

## After running

```bash
# Update star ratings from saved DB results
buibui recalibrate          # dry-run
buibui recalibrate --apply  # apply to analytics/strategies/_registry.py
```

## Task: run a backtest

When the user asks to run a backtest:

1. Confirm scope: single combo vs full sweep?
2. Confirm config: which TOML file? (`signal_watch.toml` is the default)
3. Confirm whether to save results: add `SAVE=1` if persisting to DB
4. Run the appropriate command above
5. If sweep output has TP/ATR tables, use `/backtest-findings` workflow to interpret
6. If saving: consider running `buibui recalibrate` after
