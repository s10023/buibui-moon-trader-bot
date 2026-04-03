---
name: config-refresh
description: "Refresh a signal_watch TOML config from a fresh TP sweep — updates strategy_timeframes and tp_r per strategy × TF. Run whenever the config feels stale or after structural changes (new strategies, spam fixes, A18-style detector rewrites)."
disable-model-invocation: true
---

# Config Refresh — Full TOML Update from TP Sweep

End-to-end workflow to bring any `signal_watch_*.toml` up to date:
1. Fix structural gaps in `strategy_timeframes`
2. Run a fresh TP sweep
3. Update `strategy_params` tp_r values from the results
4. Validate and commit

## When to run

- Signal watch config feels stale (hasn't been swept in 30+ days)
- After a detector rewrite (e.g. A18 smt pivot fix, spam fix) — signals change, optimal TP changes
- When adding a new strategy — it needs TF restrictions and tp_r calibration
- When `signal_watch_weekdays.toml` drifts behind `signal_watch.toml`

## Step 0 — Identify structural gaps

Before running the sweep, check `[strategy_timeframes]` for missing entries vs the reference config:

```bash
# Reference config is signal_watch.toml (tue_thu)
# Target config is the one being refreshed (e.g. signal_watch_weekdays.toml)
```

Every strategy in the `strategies = [...]` list should have an entry in `[strategy_timeframes]`
unless it genuinely runs well on ALL timeframes (rare).

**Missing entry = strategy runs on every TF in `timeframes = [...]` → typically fires on 15m noise.**

Cross-reference with the reference config's `[strategy_timeframes]`. If the reference suppresses a TF,
the target should too (unless the day filter changes the distribution enough to unlock it — the sweep will show).

## Step 1 — Uncomment tp_r_values

In the target TOML, uncomment:

```toml
tp_r_values = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
```

This line already exists (commented) in all signal_watch configs.

## Step 2 — Run the sweep

```bash
make buibui-backtest CONFIG=config/signal_watch_weekdays.toml
```

This runs 3 symbols × all active strategy×TF combos × 8 tp_r values. Takes ~2 min.

## Step 3 — Read the TP Ratio Comparison table

```
  Strategy              TF        1.5R    2.0R    2.5R    3.0R    3.5R    4.0R    4.5R    5.0R
  engulfing             1h      +0.04R  +0.09R  +0.14R  +0.15R  +0.19R  +0.23R  +0.17R  +0.19R
                                   44%     38%     34%     30%     28%     26%     22%     21%
```

**Decision rules per row:**

| Pattern | Action |
|---------|--------|
| Clear peak column | Use that tp_r |
| Monotonically increasing across all columns | Use 5.0R; may need wider sweep |
| Peak at 1.5R with high WR then drops | Use 1.5R (fast-exit pattern, not lottery) |
| All negative at every tp_r | Hard mode will suppress; set tp_r to fallback (e.g. 3.0), add comment |
| `n/a` (zero trades) | Exclude TF from `strategy_timeframes` |
| Tiny sample (< min_trades threshold) | Treat as unreliable; use fallback |

**Lottery-bias check:** if avg R peaks at 4.5R–5.0R but WR has dropped to ≤ 25% AND trade count is small (< 15), prefer the lower-TP peak with better WR. High TP + low WR + small sample = lucky outlier, not edge.

**Min-trades thresholds** (ignore rows below these):

| TF | Sweep threshold |
|----|----------------|
| 15m | 30 trades |
| 1h | 20 trades |
| 4h | 10 trades |
| 1d | 5 trades |

## Step 4 — Update strategy_timeframes

Based on the sweep results:

- **Add missing entries** (strategies with no TF restriction that have bad TFs in sweep data)
- **Remove TFs showing `n/a`** — zero trades on that day filter; no point including
- **Remove TFs that are monotonically negative** across all tp_r — hard mode will suppress at runtime, but cleaner to exclude at TF level
- **Do NOT remove TFs** based on marginal negative results alone — hard mode min_avg_r gate handles those

```toml
[strategy_timeframes]
morning_evening_star = ["15m", "1h", "4h"]  # 1d suppressed (negative across all tp_r)
order_block          = ["4h", "1d"]          # 15m/1h suppressed (negative)
smt_divergence       = ["15m", "1h", "4h"]  # 1d excluded: 0% wins
```

## Step 5 — Update strategy_params tp_r

For each strategy × TF where the sweep data is reliable:

```toml
[strategy_params.engulfing]
tp_r_15m = 4.0    # +0.11R
tp_r_1h  = 4.0    # +0.23R
tp_r_4h  = 3.5    # +0.44R
tp_r_1d  = 3.0    # +0.95R (21 trades, 50% WR)

[strategy_params.bos]
tp_r = 3.0        # all TFs negative; hard mode suppresses at runtime
```

**TOML lookup order:** `tp_r_4h` → `tp_r` (strategy-wide) → global `tp_r`

**When to use strategy-wide `tp_r` instead of per-TF keys:**
- All active TFs converge on the same value → single `tp_r`
- All TFs are negative (hard mode suppresses anyway) → single `tp_r` as clean fallback

## Step 6 — Recomment tp_r_values

```toml
# tp_r_values = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
```

## Step 7 — Validation run

Run without `tp_r_values` to confirm the config parses and produces sensible results:

```bash
make buibui-backtest CONFIG=config/signal_watch_weekdays.toml
```

Check that no TOML parse error occurs and the ranked table looks reasonable.

## Step 8 — Commit

```bash
git add config/signal_watch_weekdays.toml
git commit -m "chore(config): refresh signal_watch_weekdays.toml from fresh weekdays TP sweep"
```

## What NOT to change in this workflow

- **ADR gate** (`[bias]` section, `adr_exempt` flags) — validate separately; skip unless explicitly asked
- **Per-symbol overrides** (`[strategy_params.X.BTCUSDT]`) — require per-symbol backtest runs; don't derive from cross-symbol sweep
- **`min_trades` thresholds** — only change if backtest window changes significantly
- **`bos`, `fvg`, `liquidity_sweep 1h`** — consistently negative; only comment needs updating, tp_r fallback stays

## Key behavioural differences between configs

| Config | Day filter | Notable differences vs reference |
|--------|-----------|----------------------------------|
| `signal_watch.toml` | tue_thu | Reference config; most swept |
| `signal_watch_weekdays.toml` | weekdays | Mon/Fri included: smt 1d=0% wins, orb 1d=n/a, cvd 1d=n/a; hammer/mes 4h slightly positive |
| `signal_watch_all.toml` | off | Much higher trade counts; weekend candles drag down reversal strategies |

## Files

| File | Role |
|------|------|
| `config/signal_watch_weekdays.toml` | Primary target for this skill |
| `config/signal_watch.toml` | Reference config (tue_thu) |
| `analytics/backtest_lib.py` | `format_tp_sweep_table()` |
| `analytics/backtest_config.py` | `tp_r_values` on `BacktestSweepConfig` |

## Task: refresh a config

When the user asks to refresh or update a signal_watch TOML:

1. Read the target TOML and the reference `signal_watch.toml`
2. Identify missing `strategy_timeframes` entries (Step 0)
3. Uncomment `tp_r_values` in the target TOML (Step 1)
4. Run the sweep via `ctx_execute` or `ctx_batch_execute` to avoid flooding context (Step 2)
5. Parse the full TP Ratio Comparison table (Step 3) — use `ctx_execute` Python to extract it
6. Apply decision rules: update `strategy_timeframes` first, then `strategy_params` tp_r (Steps 4–5)
7. Recomment `tp_r_values` (Step 6)
8. Validation run (Step 7)
9. Commit (Step 8)
10. Update PR summary MD if on a feature branch
