---
name: confluence-backtest
description: >
  Run cross-TF and same-TF co-firing confluence backtests via `--cross-tf` and
  `--combo` modes. Measures uplift when 2+ strategies agree within a window —
  feeds the live signal-watch combo gate and the D10 confluence research.
  Invoke when the user says "/confluence-backtest", asks to run "cross-TF" or
  "combo" or "co-firing" backtests, mentions HTF/LTF pairs, `window_hours`,
  `D10`, or wants to measure confluence uplift.
allowed-tools: Bash, Read
---

# Confluence Backtest — Cross-TF + Same-TF Co-Firing

Two related backtest modes that measure uplift when multiple strategies agree:

- `--combo` — **same-TF** co-firing: two strategies fire on the same symbol +
  TF within a candle window. Powers the live `[combo]` gate in
  `signal_watch.toml`.
- `--cross-tf` — **cross-TF** co-firing: an HTF context strategy + an LTF
  entry strategy. Powers Card 12 (`query_cross_tf_combos`) and the D10
  confluence layer roadmap.

Both write to dedicated combo tables in `analytics.db` and are surfaced in the
web UI Backtest tab and the digest queries.

## Same-TF combo (`--combo`)

```bash
# Full co-firing sweep across all (strategy_a, strategy_b, symbol, tf) pairs
make buibui-combo-backtest CONFIG=config/signal_watch.toml SAVE=1

# With anchored window for comparable runs:
make buibui-combo-backtest CONFIG=config/signal_watch.toml SINCE=2025-09-12 SAVE=1

# Tighter / wider co-firing window (in candles)
make buibui-combo-backtest CONFIG=config/signal_watch.toml WINDOW=2 SAVE=1
```

Direct CLI:
```bash
buibui backtest --combo --config config/signal_watch.toml --save
buibui backtest --combo --symbols BTCUSDT --timeframes 15m --window 2
```

Key flags: `--window N` (candles between co-firing signals; default tuned per
config), `--workers N` (parallel pairs), `--day-filter`, `--min-trades N`,
`--fee-pct`.

## Cross-TF co-firing (`--cross-tf`)

```bash
# All 5 canonical HTF:LTF pairs
make buibui-cross-tf-backtest CONFIG=config/signal_watch.toml SAVE=1

# Specific pairs only
make buibui-cross-tf-backtest \
  CONFIG=config/signal_watch.toml \
  HTF_LTF="4h:15m 4h:1h 1h:15m" \
  SAVE=1

# Sweep window_hours (the LTF lookback for HTF context)
make buibui-cross-tf-backtest CONFIG=config/signal_watch.toml WINDOW_HOURS=2.0 SAVE=1
make buibui-cross-tf-backtest CONFIG=config/signal_watch.toml WINDOW_HOURS=8.0 SAVE=1
```

Direct CLI:
```bash
buibui backtest --cross-tf --config config/signal_watch.toml --save
buibui backtest --cross-tf --htf-ltf 4h:15m 1h:15m --window-hours 4.0
```

Key flags: `--htf-ltf "HTF:LTF ..."` (default: 5 canonical pairs),
`--window-hours FLOAT` (default `4.0` — how far back from each LTF candle to
search for an HTF signal), `--workers N`, `--day-filter`, `--min-trades`,
`--fee-pct`.

## Reading the output

Both modes print per-pair tables with: `trades`, `win%`, `avg_r`, `lift_R`
(uplift vs the LTF / strategy_b baseline). What to look for:

- **`lift_R > 0`** — confluence helps; promote the pair to live config.
- **`lift_R ≈ 0`** — neutral; sample is probably too small or the pair is
  redundant.
- **`lift_R < 0`** — confluence hurts; the second signal is selecting worse
  setups (or co-firing is rare and noisy). Suppress.

Rule of thumb: only act on pairs with `trades ≥ 30` (combo) or `trades ≥ 20`
(cross-TF). Smaller samples are noise.

## Wiring confluence back into the live config

Same-TF combos: edit `[combo]` in `config/signal_watch.toml` (or the variant
config) — list pair allowlists / suppress rules. The signal daemon refreshes
the combo lookup every 10 cycles.

Cross-TF: there is **no live gate yet** — results currently inform the D10
roadmap and Card 12 in the digest UI. When wiring lands, it will live in
`signal_lib.run_scan_cycle()`'s Phase 3 step (HTF-first ordering — see
2026-04-22 fix in MEMORY).

After updating the config, `/db-update` (or at minimum
`make buibui-recalibrate`) so star ratings reflect the new gate.

## When to run

- After adding a new strategy — both modes, to find which existing strategies
  it pairs well with.
- After changing entry / SL logic on any strategy — combo lift is sensitive
  to per-trade R distribution.
- Before promoting an experimental config — cross-TF sweep across multiple
  `--window-hours` values to pick the optimum.
- Quarterly, alongside `/wfo-sweep`, to catch drift in pair edges.

## Implementation files

| File | Role |
|------|------|
| `analytics/backtest_lib.py` | `run_combo_backtest()`, cross-TF combo runner, D10 result types |
| `analytics/backtest_runner.py` | `run_combo_backtest_cmd()`, `run_cross_tf_combo_backtest_cmd()` |
| `analytics/signal_config.py` | `ComboConfig` (`[combo]` section parser) |
| `analytics/signal_lib.py` | Live combo detection in `run_scan_cycle()` Phase 3 |
| `analytics/digest_lib.py` | Card 12 `query_cross_tf_combos` |
| `analytics/data_store.py` | Combo result tables; `confluence_ratings` join |
| `buibui.py` | `--combo`, `--cross-tf`, `--htf-ltf`, `--window`, `--window-hours` flags |
| `Makefile` | `buibui-combo-backtest`, `buibui-cross-tf-backtest` targets |

## Related

- `/backtest-run` — general backtest invocations.
- `/wfo-sweep` — single-strategy tp_r refresh; run before confluence sweeps so
  baselines are fair.
- D10 in MEMORY To-Do — full confluence-layer roadmap; cross-TF results feed
  steps 3–5.
