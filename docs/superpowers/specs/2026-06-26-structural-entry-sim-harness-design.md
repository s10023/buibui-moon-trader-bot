# Faithful per-strategy structural entry-sim harness — design

**Date:** 2026-06-26
**Status:** approved → implementation
**Lineage:** escalation from the structural level-hold touch-decay kill-test
(`docs/audits/2026-06-26-structural-level-hold-touch-decay.md`, PR #461)

## Problem

The touch-decay kill-test found **DECAY-CONFIRMED** for `fvg/long` (+2.05 ATR), `bos/short`,
and `eqh_eql/short` — first touches of a structural zone run further favorably than repeat
touches, and held-rate first>repeat held across all 8 cells. But the metric was `mfe_atr`:
**gross-of-cost forward price excursion, not realized R through an entry / stop / TP**. A
+2-ATR first-touch MFE premium says the path runs further favorably; it does NOT say a
tradable, cost-netted +R exists. The verdict pre-committed the escalation built here:
*"convert the excursion premium into realized, cost-netted avg_r with real entries/stops,
check parameter + 4h robustness, and remain gated on live-OOS as the ledger grows."*

## Goal & success metric

A read-only harness that simulates real entries/stops/TP on indexed structural-zone touches
and reports **cost-netted realized avg_r**. Success = a pre-committed de-biased gate returns a
**BUILD / NO-EDGE / INSUFFICIENT** verdict per (zone_type × direction), headlining `fvg/long`.

A NO-EDGE result is a legitimate, reportable outcome (the excursion premium was untradeable —
eaten by costs or tight-stop wick-outs) and closes the thread. Only BUILD motivates a real
`structural_touch` detector later. XS-solo stays the deploy core regardless.

## Why an entry-simulation harness (not the existing detectors)

The existing fvg/bos/eqh_eql detectors fire ~once per zone — which is exactly *why* the
parent kill-test had to regenerate touches from geometry. Tagging real detector signals by
touch-index would yield almost no repeat-touch sample, so it cannot measure decay or a
per-touch tradable edge. The faithful-yet-measurable design simulates an entry **at each
indexed touch** with a structural stop and fixed-R target, then nets costs through the live
engine.

## Architecture

Two new pure / read-only / additive modules + tests. No schema change, no golden movement.
Realized-R resolution is **delegated to the production backtest engine** — zero drift from
live cost/SL/TP semantics.

### Reuse map (do not reimplement)

| Component | Source | Role |
| --- | --- | --- |
| `extract_zones`, `index_touches`, `Zone`, `Touch`, `_atr14`, `_two_sample_lift_ci`, `_holm` | `analytics/structural_touch.py` | indexed touches on causal banded zones |
| `run_backtest(ohlcv, signals, …)` | `analytics/backtest/engine.py` | realized-R resolver: enters next-bar open, structural `sl_price`, TP = `tp_r×risk`, nets `net_R = raw − fee − slippage − funding` via `Trade.pnl_r`; all live-parity gates default-off |
| `evaluate_audit_cells`, `research_guards.{dsr,pbo,mintrl,block_bootstrap_ci}` | `analytics/audit_guard.py`, `analytics/research_guards/` | de-biased BUILD gate (bootstrap CI + Holm + DSR/PBO) — same stack as `tools/reference_level_proximity_audit.py` |
| `_build_funding_series_by_symbol` / `get_funding_rates` | `analytics/backtest_runner.py` / store | honest funding-netting |

### New module — `analytics/structural_entry_sim.py`

- `build_touch_signals(zone, touches, bars) -> pd.DataFrame` — one synthetic signal row per
  touch with the columns `run_backtest` consumes (confirmed): `open_time` (signal candle =
  touch `ts_ms`; engine enters next bar's open), `direction` = `zone.bias`, structural
  `sl_price` = far band edge (long→`zone_low`, short→`zone_high`); plus tags `touch_index`,
  `zone_id`. No `tp_price` (engine derives TP from `tp_r`).
- `simulate_cell(bars, zone_type, *, tp_r, sl_model, fee_pct, slippage_pct, funding_series,
  min_sl_atr, …) -> pd.DataFrame` — extract zones → index touches → build signals →
  `run_backtest` → collect `Trade.pnl_r`; join realized R back to `touch_index` by candle time.
  Per-trade rows: symbol, tf, zone_type, direction, touch_index, tp_r, sl_model, pnl_r (net),
  pnl_r_gross.
- `build_realized_table(bars_by_symbol_tf, zone_types, *, tp_r_grid, sl_models, …)` — loops
  symbol × tf × zone_type × tp_r × sl_model; concat per-trade realized-R rows (realized-R
  analogue of `structural_touch.build_touch_table`).
- `StructuralBuildVerdict` (frozen) — zone_type, direction, n_first, first_avg_r, boot_lo,
  boot_hi, holm_p, mintrl, dsr, pbo, decay_lift, decay_lo, decay_holm, time_split_ok,
  decision (`BUILD` | `NO-EDGE` | `INSUFFICIENT`).
- `evaluate_build(table, *, min_n, bar, alpha, n_boot, seed) -> list[StructuralBuildVerdict]`.

### Gate (pre-committed, locked before running)

**Primary BUILD gate** — per (zone_type × direction) cell, first-touch (`touch_index == 1`)
net realized R, family = {tp_r × sl_model × tf}. BUILD iff **all** hold:

- `boot_lo > 0` (bootstrap CI of mean R excludes 0),
- Holm-adjusted `p < alpha` over the cell family,
- `n_first ≥ MinTRL(0.95)`,
- `DSR ≥ 0.95` and `PBO ≤ 0.5` over the param×tf family (guards tp_r / sl-model snooping).

`n_first < min_n` → INSUFFICIENT; otherwise NO-EDGE. This is the house deploy bar.

**Secondary decay corroboration (reported, not sole-gating)** — first−repeat net-R two-sample
bootstrap lift + Holm + early/late time-split, confirming the excursion decay survives
cost-netting.

### SL-model robustness (core faithfulness lever)

A far-edge structural stop is very tight when entry lands near the band, inflating R on
wick-outs — the biggest spurious-R risk. The stop model is a swept parameter folded into the
multiple-testing family: `{structural_far_edge, structural+0.5·ATR floor, fixed 1.0·ATR}`,
driven via `run_backtest`'s `min_sl_pct` / `atr_sl_floor` + `atr_sl_multiplier`. A BUILD
verdict must survive the family, not cherry-pick the tightest stop.

### New driver — `tools/structural_entry_sim_audit.py`

Read-only (`duckdb.connect(..., read_only=True)`), mirrors `structural_touch_decay_audit.py`.
Args: `--db`, `--universe`, `--timeframes` (default `1d`; verdict run `1d 4h`), `--zone-types`
(default `fvg eqh_eql bos`), `--tp-r-grid` (default `1.0 1.5 2.0 3.0`), `--sl-models`,
`--fee-bps` / `--slippage-bps` (from `[backtest]` defaults), `--min-n 30`, `--bar 0.0`,
`--alpha 0.05`, `--n-boot 10000`, `--seed 12345`, `--out`. Writes the markdown verdict to
`docs/audits/2026-06-26-structural-entry-sim-harness.md`: headline BUILD gate table,
tp_r × sl-model sensitivity, per-cell realized first-vs-repeat avg_r, gross-vs-net contrast,
and a live blended-context block (`signal_alert_outcomes`, context-only — the live ledger
cannot isolate first-touch because cooldown removes repeats; live is the OOS confirmation as
the ledger grows). Run all (zone_type × direction) cells present for the de-biased family;
headline the three confirmed cells, lead with `fvg/long`.

### Make target

`buibui-structural-entry-sim-audit` (wraps the driver, default `--timeframes 1d`).

## Testing

TDD (`tests/test_structural_entry_sim.py`): far-edge `sl_price` + `touch_index` tagging; a
hand-built OHLCV with a zone touched twice (touch 1 → TP, touch 2 → SL) yields correct
first-vs-repeat realized `pnl_r` signs; costs reduce `pnl_r` (net < gross); SL-floor widens
risk; gate INSUFFICIENT below `min_n`, BUILD when first-touch R strongly positive with CI
excluding 0, NO-EDGE when CI spans 0; seeded determinism.

DoD: `make lint-py`, `make typecheck`, `make test`, `make test-regression` (goldens unmoved),
`make lint-md`.

## Out of scope

Building the `structural_touch` detector itself (gated behind a BUILD verdict); limit-fill
entry modeling (engine-parity next-open market is the faithful default); partial / trailing
exits; live order routing.
