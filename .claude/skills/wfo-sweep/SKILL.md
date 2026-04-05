---
name: wfo-sweep
description: "Full automated WFO sweep: param-audit → param-sweep → apply → backtest → recalibrate → commit. One command to refresh all strategy params for a config."
disable-model-invocation: true
---

# WFO Sweep — Full Automated Parameter Refresh

Runs the complete Walk-Forward Optimization chain on a TOML config without any manual pasting or intermediate steps.

## What it does

1. Reads the target config TOML → extracts symbols, TFs, fee_pct, day_filter, current tp_r values
2. **Phase 1 (Audit)**: runs `buibui param-audit` per TF on BTC — fast pass to identify which strategies have OOS edge
3. **Phase 2 (Sweep)**: for strategies with edge, runs `buibui param-sweep` per strategy × symbol
4. **Phase 3 (Apply)**: applies decision rules → updates TOML with new tp_r values
5. **Phase 4 (Validate)**: runs backtest + recalibrate, shows diff, asks to apply stars

## Input

User invokes `/wfo-sweep` with an optional config argument:
- `/wfo-sweep` — defaults to `config/signal_watch.toml`
- `/wfo-sweep config/signal_watch_weekdays.toml`
- `/wfo-sweep all` — runs on all 3 active TOMLs sequentially

## Step-by-step execution

### Step 1: Parse config

Read the target TOML:
```bash
cat config/signal_watch.toml
```

Extract:
- `timeframes` — list of TFs to sweep (e.g. `["15m", "1h", "4h", "1d"]`)
- `symbols` — if set; otherwise default to `["BTCUSDT", "ETHUSDT", "SOLUSDT"]`
- `fee_pct` — from `[backtest].fee_pct` or top-level, default `0.0005`
- `day_filter` — for context/notes only (WFO respects full history)
- Current tp_r per strategy — read from `[strategy_params.<name>]` blocks

### Step 2: Phase 1 — Audit (all TFs, BTC only)

For each TF in config's `timeframes`:
```bash
poetry run python buibui.py param-audit \
  --symbol BTCUSDT \
  --timeframe <TF> \
  --days 180 \
  --fee-pct <fee_pct>
```

Parse the audit table output. For each strategy × TF, note:
- `Best OOS avg_r` — positive = edge exists
- `OOS n` — trade count on OOS portion
- Whether OOS avg_r > current tp_r-implied edge

Strategies to skip (never sweep):
- `seasonality` — not in SIGNAL_REGISTRY
- Strategies where OOS avg_r < 0 AND current TOML already has a reasonable tp_r

Build a **candidate list**: strategies where OOS avg_r > 0 and OOS n ≥ min_trades threshold.

Min trades by TF: `15m→20, 1h→12, 4h→5, 1d→2`

### Step 3: Phase 2 — Deep sweep (candidates only)

For each (strategy, TF) in candidate list, run on each symbol in config:
```bash
poetry run python buibui.py param-sweep \
  --strategy <strategy> \
  --symbol <symbol> \
  --timeframe <TF> \
  --days 180 \
  --fee-pct <fee_pct> \
  --top-n 10
```

Collect results per (strategy, TF, symbol): best tp_r, OOS avg_r, OOS n, flag.

### Step 4: Phase 3 — Decision rules

For each strategy × TF:

**Picking best tp_r:**
1. Filter: keep only rows with `flag = ok` (drop `⚠ OVERFIT`)
2. Filter: OOS n ≥ min_trades threshold for that TF
3. Filter: OOS avg_r > 0
4. Pick: highest OOS avg_r row → that tp_r is the winner
5. If all overfit → skip, note "fully overfit — keep current"
6. If no rows pass OOS n → skip, note "insufficient trades"

**Global vs per-symbol:**
- If all symbols agree within 0.5 step → use global strategy-level `tp_r`
- If any symbol diverges by > 0.5 → use TF-specific key (e.g. `tp_r_1h`) or per-symbol override in `[strategy_params.<name>.per_symbol.<SYMBOL>]`

**When to update TOML:**
- Update if new tp_r differs from current by ≥ 0.5
- Update if OOS avg_r improvement > +0.05R vs current
- Leave unchanged if marginal (< 0.5 step AND < 0.05R improvement)
- Never add a strategy to `strategy_timeframes` based on sweep alone — only update existing entries

**Cross-config sync:**
- If sweeping `signal_watch.toml` AND `signal_watch_weekdays.toml` exists:
  - For TFs active in weekdays config, apply the same tp_r changes (they share the same market)
  - Do NOT apply to `signal_watch_all.toml` — different day_filter distribution

### Step 5: Apply changes to TOML

Edit the config file(s) with new tp_r values. Add inline comment with WFO evidence:
```toml
tp_r = 2.5  # WFO OOS: +0.31R, n=47 (2026-04-05)
```

Show a summary table before writing:
```
Changes to apply:
  strategy            TF    old tp_r → new tp_r   OOS avg_r  OOS n
  ─────────────────────────────────────────────────────────────────
  liq_sweep           1h    2.0 → 2.5             +0.31R     47
  morning_evening_star 15m  3.0 → 3.5             +0.34R     162

Skipped:
  pin_bar   15m — fully overfit
  doji      1h  — marginal (+0.02R, 38 trades)
```

Ask: "Apply these changes? (y/n)"

### Step 6: Phase 4 — Validate

After applying TOML changes, run backtest + recalibrate:
```bash
make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1
```

Then dry-run recalibrate to see star changes:
```bash
poetry run python buibui.py recalibrate --config config/signal_watch.toml
```

Show diff. Ask: "Apply star ratings? (y/n)"

If yes:
```bash
poetry run python buibui.py recalibrate --config config/signal_watch.toml --apply
```

### Step 7: Commit

Stage and commit:
```bash
git add config/signal_watch.toml config/signal_watch_weekdays.toml
git commit -m "chore(config): WFO sweep — update tp_r per strategy × TF (2026-04-XX)"
```

## Output format

Final summary:
```
WFO sweep complete — config/signal_watch.toml
  Updated: N strategies across M TFs
  Backtest saved. Stars: K changed, L unchanged.
  Commit: <hash>
```

## Notes

- `param-audit` is cheap (all strategies, one symbol, per TF) — always run this first as a filter
- `param-sweep` is expensive (full grid per strategy × symbol × TF) — only run for candidates
- If config has no symbols set, default to BTC/ETH/SOL (the 3 most-backtested)
- WFO split default: 70% IS / 30% OOS — do not change unless history < 90d
- Never commit a config where any strategy has OOS avg_r < 0 after the update
