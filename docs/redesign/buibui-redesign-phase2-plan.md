# v2 Phase 2 — Live regime gate (soft-mode wiring plan)

**Branch:** `feat/v2-phase2-regime-gate`
**Status:** PLAN — pending user sign-off before code lands.
**Replaces (eventually):** Phase 2 step 1 of `docs/redesign/buibui-redesign.md` §10.

## Goal

Wire `analytics.regime.classify_series` into the live signal scanner as a
suppress/allow gate keyed by **(strategy_type × regime)**. Ship in **soft mode
first**, observe for ~2 weeks, then flip to hard. Same rollout shape as F8
(PR #346).

The redesign §6 enablement matrix is written in terms of the v2 unified
`setup_type` model (`sweep_reversion` / `smt_reversion` / `ote_continuation` /
`session_sweep`). Those don't exist yet — they're built in Phase 2 step 2.
**This branch ships the gate first** using the existing
`STRATEGY_TYPE_GROUPS` taxonomy (`structural`, `fib`, `price_action`,
`candlestick`, `flow`, `session`, `trend`) as a v1 bridge. Step 2 swaps the
mapping when the unified model lands; the gate code stays.

## Three design calls

### 1. Strategy-type → regime enablement matrix (v1 bridge)

Per redesign §6 the **direction** of the rules is clear; we just need a
mapping from current types until the v2 setup_types ship.

| Regime | Continuation-style (`trend`, `fib`-cont) | Reversion-style (`flow`, `structural`, `price_action`, `candlestick`) | Session (`session`) |
| --- | --- | --- | --- |
| `trend` | enabled (this is its regime) | **with-trend only** (counter-trend dropped) | with-trend only |
| `range` | **dropped** | **enabled both directions** | enabled |
| `high_vol` | **dropped** (whippy) | enabled, flag size-halve TODO | enabled, flag size-halve TODO |
| `unknown` | fall open (warmup / missing data) | fall open | fall open |

- **Continuation** = `ema` (trend), `ote_entry` (fib), `fib_golden_zone` (fib), `bos` (structural-but-continuation).
- **Reversion** = everything else under `flow` / `structural` (minus bos) / `price_action` / `candlestick`.
- **Session** = `orb`.
- "with-trend only" reuses the F8 slope cache (already computed each cycle) — no new HTF fetch.
- `high_vol` size-halving is **deferred** to the Phase 4 risk layer; gate logs the flag but does not yet halve.

### 2. Wiring location

- **Step −1 of bias gate** (runs before F8). Regime is the coarsest decision; F8 then refines counter-trend in `trend` regime.
- Pre-compute `regime_cache: dict[str, Regime]` once per `run_scan_cycle` from 4h candles (per redesign §6 — 4h is the regime TF regardless of signal TF). One DB fetch per symbol, then `classify_series(...).iloc[-2]` on closed candles only (mirrors F8 "drop in-progress bar" rule).
- Cache miss / `unknown` → allow.
- Gate fn lives in `analytics/signal/gates.py` next to `_apply_htf_ema_gate`.

### 3. Soft / hard mode + config surface

Mirror F8 precisely. New `[bias.regime]` block in `config/strategy_params.toml`
(inherited by all 3 main configs):

```toml
[bias.regime]
enabled = true
mode = "soft"                 # "soft" = log only; "hard" = drop suppressed
htf_tf = "4h"                 # regime classification TF
# Strategy-type → list of regimes where the type is enabled.
# Suppressed regimes drop the signal in hard mode, log in soft.
[bias.regime.enabled_regimes]
trend         = ["trend"]                       # continuation only in trend
fib           = ["trend"]                       # ote/fib_golden — continuation
flow          = ["range", "high_vol"]           # reversion-style
structural    = ["range", "high_vol"]           # reversion + bos special-case
price_action  = ["range", "high_vol"]
candlestick   = ["range", "high_vol"]
session       = ["trend", "range", "high_vol"]  # orb works everywhere
# Strategies overridden out of their type's group:
[bias.regime.per_strategy]
bos           = ["trend"]                       # bos is continuation despite type=structural
```

Counter-trend behaviour in `trend` regime is left to F8 (already strict there
once §6's deadband=0.001 lands; we'll tighten F8's deadband from 0.003 →
0.001 only when `regime == trend` — small, targeted change, separate commit).

## Implementation order (small commits)

1. **Tests + fixtures** — `tests/test_regime_gate.py` covering: enablement
   matrix matches table; soft mode keeps signal but logs; hard mode drops;
   `unknown` regime falls open; bos override path.
2. **Config schema** — extend `BiasConfig` with `regime_enabled`,
   `regime_mode`, `regime_htf_tf`, `regime_enabled_regimes`,
   `regime_per_strategy`. Parse in `signal_config.py`. Round-trip tests.
3. **Gate fn** — `_apply_regime_gate(events, bias_cfg, regime_cache, symbol, tf)`
   in `analytics/signal/gates.py`. Pure; logs identical shape to F8.
4. **Cycle wiring** — pre-compute `regime_cache` once per cycle in
   `run_scan_cycle` (mirrors `htf_slope_cache` block at scanner.py:347–375).
   Insert as Step −1 of bias gate at scanner.py:765.
5. **TOML enable in soft mode** — add `[bias.regime]` block to
   `config/strategy_params.toml` with `mode = "soft"`. Other 3 main configs
   inherit automatically.
6. **Lint + typecheck + test + regression**. No `db-update` needed (live-scan
   only — backtests don't run bias gates, fixtures unchanged).
7. **PR draft** with 2-week soft-mode observation plan.

## Validation criteria

- **Cycle wall-clock**: ≤ +50ms vs pre-branch (F8 added ~30ms; regime fetch is
  one extra 4h slice per symbol → cheap).
- **Soft-mode log shape**: every suppressed signal logs once with
  `(symbol, tf, strategy, regime, direction)` so we can grep counts after 2
  weeks.
- **No DB writes added.**
- **Regression goldens unchanged** (gate is live-only).
- **3/3 main configs** inherit cleanly from `strategy_params.toml`.

## Hard-mode flip criteria (future, ~2 weeks out)

- Soft logs cover ≥ 500 suppressed candidates.
- Backtest the suppressed subset in isolation: avg_r should be **≤ 0** to
  justify dropping (same threshold F8 used).
- If passes, single-line TOML edit `mode = "soft"` → `"hard"`. No code change.

## What this branch is NOT

- ❌ Not the unified `SignalCandidate` model (Phase 2 step 2).
- ❌ Not a deletion of F8/ADR/DOW yet — those stay until §6 enablement is
  validated end-to-end and we can prove the gate subsumes them.
- ❌ Not the high_vol risk-halving (Phase 4 risk layer).
- ❌ Not a config TOML restructure (`mode_a_swing.toml` / `mode_b_session.toml`
  consolidation is a separate Phase 2 sub-task).
