---
name: wfo-sweep
description: >
  Full automated Walk-Forward Optimization chain for a config TOML —
  `param-audit` → `param-sweep` → apply `tp_r` to TOML → backtest →
  recalibrate → commit. One invocation runs the whole chain end-to-end.
  Invoke when the user says "/wfo-sweep", calls a config "stale", asks to
  "refresh tp_r" or "rerun WFO", or after adding a new strategy that needs
  validated `tp_r` values. This is the trusted production path for tp_r
  (`/config-refresh` covers non-tp_r dimensions only).
allowed-tools: Bash, Read, Edit, Write
---

# WFO Sweep — Full Automated Parameter Refresh

Runs the complete Walk-Forward Optimization chain on a TOML config without any manual pasting or intermediate steps.

## What it does

1. Reads the target config TOML → extracts symbols, TFs, fee_pct, day_filter, current tp_r values
2. **Phase 1 (Audit)**: runs `buibui param-audit` per TF on BTC — fast pass to identify which strategies have OOS edge
3. **Phase 2 (Sweep)**: for strategies with edge, runs `buibui param-sweep` per strategy × symbol
4. **Phase 3 (Apply)**: applies decision rules → updates TOML with new tp_r values
5. **Phase 4 (Validate)**: runs backtest + recalibrate, shows diff, asks to apply stars

⚠ **Every `tp_r` currently in the TOMLs was fitted BEFORE ST128 (2026-09-09), on a
book with no stop floor paying no slippage.** The first corrected sweep will move
cells, and that movement is the fix landing rather than drift — so do not read it as
instability, and do not run it in the same change as ST122's recalibrate or neither
movement is attributable.

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
- `min_sl_pct` / `slippage_bps` — **NOT extracted**; since ST128 `param-sweep` and
  `param-audit` read both from `config/strategy_params.toml` themselves, so the
  invocations below get production's stop floor and slippage without passing them.
  Override with `--min-sl-pct` / `--slippage-bps` only for a deliberate experiment.
- `day_filter` — passed as `--day-filter` to every `param-audit` and `param-sweep` call so WFO runs on the correct trade population for this config. ⚠ **This line was FALSE for 2 of the 3 configs until 2026-09-10 (SoT ST132)** — `cli/param.py` validated the flag against a restated `["off","weekdays","tue_thu"]`, so `mon_fri` and `weekend` were argparse ERRORS and `/wfo-sweep all` could not sweep those two on their own population at all. **No available choice isolated either**: `weekdays` and `tue_thu` both contain zero weekend days, and `weekdays` carries Mon+Fri plus three days the `mon_fri` config never runs — a substitution is a DIFFERENT BOOK, not an approximation. Fixed; `DAY_FILTER_MODES` now has one home beside `_day_filter_to_weekdays`
- Current tp_r per strategy — read from `[strategy_params.<name>]` blocks

### Step 2: Phase 1 — Audit (all TFs, BTC only)

For each TF in config's `timeframes`:

```bash
poetry run python buibui.py param-audit \
  --symbol BTCUSDT \
  --timeframe <TF> \
  --since 2025-09-12 \
  --fee-pct <fee_pct> \
  --day-filter <day_filter>
```

Optional F9 joint-sweep flags (when auditing strategies that emit
structural SL prices and you want the floor applied during scoring):
`--atr-sl-floor --atr-sl-multiplier <N>`. Without the floor the ATR
branch is dead for every active strategy — leave it off for a vanilla
audit.

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
  --since 2025-09-12 \
  --fee-pct <fee_pct> \
  --day-filter <day_filter> \
  --top-n 10
```

Optional joint `tp_r × atr_sl_multiplier` sweep: append
`--atr-sl-floor --atr-sl-multiplier <N>` to score every tp_r in the grid
with the F9 floor on at multiplier `N`. Useful for follow-up after an
ATR-sweep winner — e.g. `--atr-sl-multiplier 2.0 --atr-sl-floor` plus
`--param tp_r=1.0:5.0:0.5` finds the best tp_r at that multiplier. See
`memory/project_f9_joint_sweep_findings.md` for the methodology.

Collect results per (strategy, TF, symbol): best tp_r, OOS avg_r, OOS n, flag.

### Step 4: Phase 3 — Decision rules

For each strategy × TF:

**Commit gate (P0a-2) — check FIRST, hard refusal:**
Every `param-sweep` output ends with a `COMMIT-GATE` line for its recommended config
(Deflated Sharpe + PBO + Minimum Track Record Length — the overfitting guard).

- `✓ COMMIT-GATE: PASS` → proceed to pick tp_r below.
- `✗ COMMIT-GATE: DO-NOT-COMMIT` → **skip the cell, never write its tp_r.** Note the failing
  metric (e.g. "DSR 0.81 < 0.95"). This is the project's multiple-testing correction; an
  in-sample winner that fails here is an overfit mirage.
- `⚠ COMMIT-GATE: INSUFFICIENT` → **skip the cell.** Too few trades/trials to evaluate.
- Override only on explicit user instruction ("override the gate for X").

**Picking best tp_r:** (only for cells that PASSED the commit gate)

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

**Cross-config sync — ⛔ REVOKED 2026-09-10. Sweep each config on its OWN `day_filter`
and apply its results to THAT config only. No propagation, in any direction.**

The rule used to say: *"For TFs active in weekdays config, apply the same tp_r changes (they
share the same market)"*, while refusing the same propagation into `signal_watch_all.toml`
for *"different day_filter distribution"*. But the three populations are **disjoint by
construction** — `tue_thu` = Tue–Thu, `mon_fri` = Mon+Fri, `weekend` = Sat+Sun — so the
reason it gave for refusing one propagation applies exactly to the one it permitted. "Same
market" is true of the symbol and false of the population, which is the whole reason these
are three files.

⚠ **This is a LEAD for how the `mon_fri` config got `tp_r` values the CLI could not
produce** (ST132: `--day-filter mon_fri` was an argparse error, and that config's own
comments read `# WFO OOS (weekdays)` — a filter that is neither its own nor a valid
isolation of it). Filed as a lead, not an established finding: what is established is that
this rule said to do it and that a direct sweep was impossible.

Revoked by `docs/superpowers/specs/2026-09-10-st128-wfo-resweep-preregistration.md` §2.

### Step 5: Apply changes to TOML

Edit the config file(s) with new tp_r values. Add inline comment with WFO evidence:

```toml
tp_r = 2.5  # WFO OOS: +0.31R, n=47 (2026-04-05)
```

Show a summary table before writing:

```text
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

### Step 7: Update golden files

After recalibration, regenerate the regression golden files to capture the new metrics:

```bash
make regression-update
```

Review what changed:

```bash
git diff tests/fixtures/golden_*.json
```

### Step 8: Commit

Stage and commit:

```bash
git add config/signal_watch.toml config/signal_watch_weekdays.toml tests/fixtures/golden_*.json
git commit -m "chore(config): WFO sweep — update tp_r per strategy × TF (2026-04-XX)"
```

## Output format

Final summary:

```text
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
- **Never write a `tp_r` for a cell whose `param-sweep` output stamped `DO-NOT-COMMIT` or
  `INSUFFICIENT`** — the commit gate (DSR/PBO/MinTRL) is a hard pre-condition that runs before
  the OOS-avg_r selection. Report skipped cells with their failing metric.
- Always pass `--day-filter <day_filter>` to both `param-audit` and `param-sweep` — the WFO must run on the same trade population the config will see in production (`tue_thu` for `signal_watch.toml`, `mon_fri` for `signal_watch_weekdays.toml`, `weekend` for `signal_watch_all.toml`)
