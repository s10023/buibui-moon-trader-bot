---
name: backtest-findings
description: "Interpret backtest sweep tables (ATR/TP/volume/duration) and commit winning params to TOML. Always use this after any sweep run — even when you're just reading a table and not sure what to do with it."
disable-model-invocation: true
---

# Backtest Findings — Interpreting Sweep Output

Workflow for reading a sweep table and translating results into committed TOML config.

## Min-trades thresholds (before trusting any result)

These are the calibrated minimums per TF (from TOML comments, derived from DB p25 directional counts):

| TF | Sweep table (`min_trades_*`) | Signal watch daemon (`[backtest].min_trades_*`) |
|----|------------------------------|------------------------------------------------|
| 15m | 30 | 20 |
| 1h | 20 | 12 |
| 4h | 10 | 5 |
| 1d | 5 | 2 |

Rows below threshold are hidden or should be ignored. Higher thresholds for the daemon's directional filter because it filters per-direction (long or short), not total.

## Reading a TP sweep table

```
  Strategy              TF      1.0R    1.5R    2.0R    2.5R    3.0R
  engulfing             1h    +0.08R  +0.12R  +0.16R  +0.18R  +0.19R
  engulfing             4h    +0.10R  +0.18R  +0.22R  +0.24R  +0.25R
  pin_bar               1h    +0.09R  +0.14R  +0.19R  +0.23R  +0.26R
  liquidity_sweep       1h    -0.10R  -0.08R  -0.05R  -0.03R  -0.04R
```

1. **Peak column** per row = optimal tp_r for that strategy × TF
2. **Monotonically increasing** across all columns → try extending the range (add 3.5R, 4.0R)
3. **Flat or all negative** → TP tuning won't fix this strategy; investigate SL, volume filter, or TF restrictions
4. **Peaks differ by TF** → use TF-specific override keys (`tp_r_4h`, `tp_r_1h`)

## Reading an ATR sweep table

```
  Strategy              TF      0.5×    1.0×    1.5×    2.0×    2.5×
  bos                   1h    +0.08R  +0.22R  +0.31R  +0.28R  +0.19R
```

- Peak column = optimal `atr_sl_multiplier` for that strategy × TF
- Strategies with structural SLs (liquidity_sweep, order_block, eqh_eql) may be flat — ATR only kicks in when structural SL is absent

## Reading the volume split table

Always printed alongside main results (regardless of `volume_suppress` setting):

```
  Strategy              TF    High Vol   Low Vol   Δ
  bos                   1h    +0.31R     +0.10R    +0.21R
  pin_bar               1h    +0.18R     +0.37R    -0.19R
```

- **Positive Δ** (High Vol >> Low Vol): consider `volume_suppress = true` for this strategy
- **Negative Δ** (Low Vol >> High Vol): do NOT suppress — low-vol signals have edge here
- Decision threshold: |Δ| > 0.10R is meaningful; < 0.05R is noise

**A14b findings (current per-strategy tp_r — see `volume-sweep` skill for full table):**
- Suppress: `bos`, `orb`, `fib_golden_zone`, `doji`, `smt_divergence`, `liquidity_sweep`
- Do NOT suppress: `pin_bar`, `hammer_hanging_man`, `marubozu`, `cvd_divergence`, `morning_evening_star`
- Neutral: `engulfing`, `eqh_eql`, `fvg`, `inside_bar`, `order_block`, `trend_day`

Note: A13 findings (at tp_r=2.0) are superseded by A14b. `liquidity_sweep` and `morning_evening_star` reversed direction after per-strategy tp_r was applied. Always re-run the volume split after any tp_r change.

## Reading the duration table

```
  Strategy    TF    Trades   Avg Hold   Median Hold   Max Hold
  engulfing   1h    328      1.2d       16.0h         10.1d
  bos         15m   4356     1.4d       13.0h         39.8d
```

Speed tiers:
- **Fast < 4h median**: marubozu, liq_sweep, smt_div, trend_day (15m) — hits SL/TP quickly
- **Overnight 13–16h**: all candlestick patterns regardless of TF — NOT scalping strategies
- **Multi-day**: bos 1h (2.2d), bos 4h (6.3d) — need patient management

Warning: 15m candlestick patterns have the same hold time as 1h — more signals, same duration = more noise.

## Committing TOML config

### Strategy-wide override
```toml
[strategy_params.engulfing]
tp_r = 3.0              # applies to all TFs
```

### TF-specific override
```toml
[strategy_params.fib_golden_zone]
tp_r_4h = 3.0           # 4h only
tp_r_1h = 2.0           # 1h only
# other TFs fall back to global tp_r
```

### ATR SL override (per-strategy)
```toml
[strategy_params.bos]
atr_sl_multiplier = 1.5
atr_sl_multiplier_1h = 2.0    # TF-specific
```

### Suppressing a TF via strategy_timeframes
```toml
[strategy_timeframes]
engulfing = ["1h", "4h", "1d"]   # removes 15m — high noise, low edge
```

### When to use strategy-wide vs TF-specific
- If the optimal value is the same across all TFs → strategy-wide `tp_r`
- If one TF has a clearly different optimum → TF-specific key
- If one TF is consistently negative → add it to `[strategy_timeframes]` to suppress entirely

## After committing findings

```bash
# Persist winning config to DB
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1

# Optionally recalibrate star ratings from DB
buibui recalibrate          # dry-run — shows diff
buibui recalibrate --apply  # writes to indicators_lib.py
```

## Where findings are stored

- `project_f6_tp_sweep_findings.md` — F6 TP sweep results (weekdays, 200d, 3 symbols)
- `project_a13_volume_findings.md` — Volume impact delta per strategy (A13)
- `config/signal_watch.toml` — Committed `[strategy_params.*]` overrides
- `analytics.db` — All saved `backtest_runs` rows (queryable via DuckDB)

## Task: interpret and commit sweep results

When the user pastes a sweep table or asks to translate findings:

1. Read the table — identify peak column per strategy × TF row (respecting min_trades thresholds)
2. Check volume split: note any strategies where |Δ| > 0.10R
3. Check duration table: categorise strategies into speed tiers
4. Open `config/signal_watch.toml` (or target config) and update `[strategy_params.*]` entries
5. Commit the changes
6. Run `make buibui-backtest CONFIG=<file> SAVE=1`
7. Update `project_f6_tp_sweep_findings.md` (or create new findings file) with key observations
8. Update MEMORY.md with session summary
