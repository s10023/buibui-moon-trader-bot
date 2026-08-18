# Structural entry-sim under confirmation causality — the ST31 correction

**Date:** 2026-08-18  ·  **Status:** read-only measurement (no engine change)
**Corrects:** `docs/audits/2026-06-26-structural-entry-sim-harness.md` (BUILD, withdrawn)
**Spec:** the 2026-08-18 structural-touch confirmation-causality design (pre-registered)

## Headline verdict: **NO-EDGE**

No powered cell on any requested tf (1d) clears the de-biased BUILD bar — XS-solo stays the deploy core.

Pre-committed BUILD gate (locked before running): on the **headline config** (`tp_r=2.0` × `sl_model=atr_floor`), evaluated SEPARATELY for each tf below (no cross-tf pooling), first-touch (`touch_index==1`) net realized R must clear `n_first ≥ 30`, a block-bootstrap CI lower bound `> 0.0`, a Holm-adjusted `p < 0.05` across that tf's (zone_type × direction) family, `n_first ≥ MinTRL(0.95)`, AND `DSR ≥ 0.95 ∧ PBO ≤ 0.5` over that tf's tp_r × sl_model trial family. The first−repeat decay lift is secondary corroboration. Substrate = backtest/OHLCV (the live ledger cannot gate — cooldown removes repeats).

Params: `tfs=['1d']`  `zone_types=['fvg', 'eqh_eql', 'bos']`  `tp_r_grid=[1.0, 1.5, 2.0, 3.0]`  `sl_models=['structural', 'atr_floor', 'fixed_atr']`  `fee_bps=5.0`  `slippage_bps=2.0`  `n_boot=10000`  `seed=12345`  `touch_geometry=confirmed`. Resolved trades (all tfs): **2000945**.

## What changed, and why the sign flips

The 2026-06-26 harness indexed a zone's touches from its **formation** bar. For every zone type
the formation bar precedes the bar at which the zone is knowable from closed bars, so the harness
traded touches no live detector could have seen. `_zone_from_dict` dropped the extractors'
`active` / `close_ms` fields and `index_touches` admitted any bar after `start_ms`; nothing in the
chain carried a confirmation concept.

**The inflation is a selection effect, not an edge.** Each zone type is *defined* by a condition on
the bars after its formation bar, so entering inside that window means entering with a guarantee:

- **FVG** — a bullish gap exists iff `low[i+1] > high[i-1]`. The band is `[high[i-1], low[i+1]]`,
  the long stop is its far edge `high[i-1]`, and the harness entered at **bar `i+1`'s open**. Bar
  `i+1`'s low *is* the top of that band by construction, so bar `i+1` cannot reach the stop. One
  guaranteed-unstoppable bar, and a TP landing there is a risk-free win.
- **BOS** — a swing high at `i` exists iff no high in `i+1…i+5` exceeds it. A short entered at
  `i+1` with a stop above it gets **five** guaranteed-unstoppable bars.
- **EQH/EQL** — the same shape, keyed on the second swing's confirmation.

You only get to take the trade in the worlds where price did not move against you, because those
are precisely the worlds in which the zone exists.

Measured on 5 symbols × 1d before the fix: **100.0% of 2,148 `fvg` first touches sat exactly 1 bar
after formation** (mean lag 1.00, no spread), and applying a per-type confirmation offset, the
first *tradable* touch carried old `touch_index` 1 for **0.0%** of `fvg` zones, 60.8% of `bos` and
57.2% of `eqh_eql`. The correction is therefore not a filter — `n_first` moves only a few percent
because each zone still contributes exactly one first touch — it **replaces which bar that touch
is**, for essentially every zone. Old `fvg` meant "buy the bar after the impulse"; corrected `fvg`
means "buy the retest when price returns to the gap".

### Control: the same data under both geometries

`--legacy-touch-geometry` re-runs the original indexing on today's DB, so the comparison below
isolates the geometry from the seven extra weeks of bars. The legacy arm reproduces the filed
verdict structure exactly — same five BUILD cells, same lone `eqh_eql/short` NO-EDGE, every
`avg_r` within 0.011R of its published value — which is what makes the corrected arm attributable
to the correction alone rather than to a broken harness.

| zone × dir | legacy geometry (same data) | corrected | Δ | filed 2026-06-26 |
| --- | ---: | ---: | ---: | ---: |
| fvg/long | +0.529 **BUILD** | **−0.056** | −0.585 | +0.540 |
| fvg/short | +0.511 **BUILD** | **−0.002** | −0.513 | +0.514 |
| eqh_eql/long | +0.405 **BUILD** | **−0.163** | −0.568 | +0.407 |
| eqh_eql/short | +0.404 NO-EDGE | **−0.287** | −0.691 | +0.420 |
| bos/long | +0.224 **BUILD** | **−0.156** | −0.380 | +0.218 |
| bos/short | +0.198 **BUILD** | **−0.217** | −0.415 | +0.188 |

All six cells flip sign; five BUILD verdicts become zero. Five of the six corrected cells have a
bootstrap CI excluding zero on the **negative** side, so this is a confirmed-negative rather than a
powered null — the exception is `fvg/short` at [−0.058, +0.051], which is genuinely
indeterminate. The decay lift collapses the same way (`fvg/long` +0.637 → +0.056, and negative for
`bos` and `eqh_eql`): once the first touch has to be one a detector could see, "first touches run
further" largely evaporates.

### Consequences

1. **ST31 closes. No `structural_touch` detector is built.** The board's only cleared BUILD is
   withdrawn. `/new-strategy` was not invoked.
2. **The parent touch-decay audit inherits the defect.** `build_touch_table` calls the same
   `index_touches`, so `docs/audits/2026-06-26-structural-level-hold-touch-decay.md` measured the
   same formation-time population; its excursion result is partly impulse-continuation rather than
   first-visit-vs-repeat. Both tools now carry `--legacy-touch-geometry` so filed runs stay
   reproducible. **Re-running it was out of scope here — its numbers stand as superseded but
   unrecomputed.**
3. **A green causality test was blind to this.**
   `test_build_touch_table_is_causal_no_lookahead` perturbs only the **final** bar, which proves no
   distant-future leakage into already-formed rows and is structurally incapable of catching a
   1-bar *local* look-ahead. That is why this survived seven weeks as the only cleared BUILD.
4. **Not yet separated:** the correction bundles two switches (skip pre-confirmation bars; require
   a genuine return from outside the band). Their individual contributions are unattributed. The
   flags exist to decompose it, and doing so would answer whether the impulse-continuation trade is
   real when entered legally at `i+2` — a *new* hypothesis, not a rebuilt one.

## Per-timeframe breakdown

### tf=`1d` — headline: **NO-EDGE**

No powered (zone_type × direction) cell clears the de-biased BUILD bar in realized R — the excursion premium does not survive entry/stop/cost. XS-solo stays the deploy core.

#### Primary gate (per zone_type × direction, tf=`1d`)

| zone × dir | n_first | n_rep | first_avg_r | boot CI | Holm p | MinTRL | DSR | PBO | decay lift | split | decision |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | :-: | --- |
| bos/long | 2527 | 30813 | -0.156 | [-0.216, -0.094] | 0.000 | — | — | 0.000 | -0.051 | · | **NO-EDGE** |
| bos/short | 2264 | 27497 | -0.217 | [-0.277, -0.156] | 0.000 | — | — | 0.004 | -0.167 | · | **NO-EDGE** |
| eqh_eql/long | 356 | 3837 | -0.163 | [-0.305, -0.021] | 0.049 | — | — | 0.149 | -0.074 | · | **NO-EDGE** |
| eqh_eql/short | 285 | 2939 | -0.287 | [-0.428, -0.142] | 0.001 | — | — | 0.471 | -0.228 | · | **NO-EDGE** |
| fvg/long | 3668 | 45751 | -0.056 | [-0.106, -0.006] | 0.047 | — | — | 0.088 | +0.056 | ✓ | **NO-EDGE** |
| fvg/short | 3591 | 43305 | -0.002 | [-0.058, +0.051] | 0.917 | — | — | 0.071 | +0.035 | · | **NO-EDGE** |

#### Robustness — tp_r × sl_model sensitivity (tf=`1d`, reported, not gate-deciding)

First-touch net avg_r at tf=`1d` (n≥30); gross in parens.

| zone × dir | tp_r | sl_model | n | net avg_r | gross |
| --- | ---: | --- | ---: | ---: | ---: |
| bos/long | 1.0 | atr_floor | 2535 | -0.195 | -0.164 |
| bos/long | 1.0 | fixed_atr | 2535 | -0.135 | -0.112 |
| bos/long | 1.0 | structural | 2535 | -0.214 | -0.046 |
| bos/long | 1.5 | atr_floor | 2531 | -0.181 | -0.148 |
| bos/long | 1.5 | fixed_atr | 2532 | -0.169 | -0.143 |
| bos/long | 1.5 | structural | 2531 | -0.194 | -0.024 |
| bos/long | 2.0 | atr_floor | 2527 | -0.156 | -0.121 |
| bos/long | 2.0 | fixed_atr | 2528 | -0.200 | -0.172 |
| bos/long | 2.0 | structural | 2527 | -0.173 | -0.001 |
| bos/long | 3.0 | atr_floor | 2522 | -0.173 | -0.134 |
| bos/long | 3.0 | fixed_atr | 2520 | -0.216 | -0.183 |
| bos/long | 3.0 | structural | 2522 | -0.150 | +0.027 |
| bos/short | 1.0 | atr_floor | 2266 | -0.265 | -0.229 |
| bos/short | 1.0 | fixed_atr | 2266 | -0.161 | -0.142 |
| bos/short | 1.0 | structural | 2266 | -0.095 | +0.017 |
| bos/short | 1.5 | atr_floor | 2265 | -0.243 | -0.211 |
| bos/short | 1.5 | fixed_atr | 2266 | -0.145 | -0.131 |
| bos/short | 1.5 | structural | 2265 | -0.089 | +0.019 |
| bos/short | 2.0 | atr_floor | 2264 | -0.217 | -0.188 |
| bos/short | 2.0 | fixed_atr | 2261 | -0.150 | -0.140 |
| bos/short | 2.0 | structural | 2264 | -0.072 | +0.034 |
| bos/short | 3.0 | atr_floor | 2259 | -0.197 | -0.173 |
| bos/short | 3.0 | fixed_atr | 2256 | -0.179 | -0.176 |
| bos/short | 3.0 | structural | 2259 | -0.063 | +0.039 |
| eqh_eql/long | 1.0 | atr_floor | 357 | -0.205 | -0.171 |
| eqh_eql/long | 1.0 | fixed_atr | 357 | -0.205 | -0.176 |
| eqh_eql/long | 1.0 | structural | 357 | -0.187 | -0.098 |
| eqh_eql/long | 1.5 | atr_floor | 356 | -0.180 | -0.143 |
| eqh_eql/long | 1.5 | fixed_atr | 357 | -0.254 | -0.223 |
| eqh_eql/long | 1.5 | structural | 356 | -0.146 | -0.055 |
| eqh_eql/long | 2.0 | atr_floor | 356 | -0.163 | -0.124 |
| eqh_eql/long | 2.0 | fixed_atr | 356 | -0.265 | -0.233 |
| eqh_eql/long | 2.0 | structural | 356 | -0.141 | -0.048 |
| eqh_eql/long | 3.0 | atr_floor | 354 | -0.175 | -0.130 |
| eqh_eql/long | 3.0 | fixed_atr | 355 | -0.260 | -0.223 |
| eqh_eql/long | 3.0 | structural | 354 | -0.116 | -0.017 |
| eqh_eql/short | 1.0 | atr_floor | 285 | -0.198 | -0.158 |
| eqh_eql/short | 1.0 | fixed_atr | 285 | -0.106 | -0.081 |
| eqh_eql/short | 1.0 | structural | 285 | -0.060 | +0.011 |
| eqh_eql/short | 1.5 | atr_floor | 285 | -0.230 | -0.193 |
| eqh_eql/short | 1.5 | fixed_atr | 285 | -0.135 | -0.114 |
| eqh_eql/short | 1.5 | structural | 285 | -0.090 | -0.021 |
| eqh_eql/short | 2.0 | atr_floor | 285 | -0.287 | -0.253 |
| eqh_eql/short | 2.0 | fixed_atr | 285 | -0.114 | -0.095 |
| eqh_eql/short | 2.0 | structural | 285 | -0.144 | -0.077 |
| eqh_eql/short | 3.0 | atr_floor | 285 | -0.275 | -0.242 |
| eqh_eql/short | 3.0 | fixed_atr | 285 | -0.147 | -0.130 |
| eqh_eql/short | 3.0 | structural | 285 | -0.111 | -0.046 |
| fvg/long | 1.0 | atr_floor | 3671 | -0.095 | -0.056 |
| fvg/long | 1.0 | fixed_atr | 3670 | -0.041 | -0.013 |
| fvg/long | 1.0 | structural | 3667 | -0.042 | +0.120 |
| fvg/long | 1.5 | atr_floor | 3670 | -0.072 | -0.030 |
| fvg/long | 1.5 | fixed_atr | 3667 | -0.028 | +0.004 |
| fvg/long | 1.5 | structural | 3666 | -0.020 | +0.144 |
| fvg/long | 2.0 | atr_floor | 3668 | -0.056 | -0.011 |
| fvg/long | 2.0 | fixed_atr | 3660 | -0.019 | +0.016 |
| fvg/long | 2.0 | structural | 3664 | +0.001 | +0.168 |
| fvg/long | 3.0 | atr_floor | 3663 | -0.036 | +0.016 |
| fvg/long | 3.0 | fixed_atr | 3647 | -0.008 | +0.035 |
| fvg/long | 3.0 | structural | 3660 | +0.017 | +0.189 |
| fvg/short | 1.0 | atr_floor | 3600 | -0.061 | -0.026 |
| fvg/short | 1.0 | fixed_atr | 3595 | +0.011 | +0.029 |
| fvg/short | 1.0 | structural | 3599 | -0.008 | +0.181 |
| fvg/short | 1.5 | atr_floor | 3595 | -0.012 | +0.022 |
| fvg/short | 1.5 | fixed_atr | 3590 | +0.031 | +0.045 |
| fvg/short | 1.5 | structural | 3596 | +0.050 | +0.238 |
| fvg/short | 2.0 | atr_floor | 3591 | -0.002 | +0.029 |
| fvg/short | 2.0 | fixed_atr | 3582 | +0.029 | +0.039 |
| fvg/short | 2.0 | structural | 3591 | +0.074 | +0.261 |
| fvg/short | 3.0 | atr_floor | 3585 | +0.018 | +0.045 |
| fvg/short | 3.0 | fixed_atr | 3552 | +0.006 | +0.011 |
| fvg/short | 3.0 | structural | 3584 | +0.085 | +0.270 |

## Live context — blended structural avg_r

Blended over ALL touches (live cooldown removes repeats — cannot isolate first-touch; context only).

| strategy | dir | n | avg_r |
| --- | --- | ---: | ---: |
| bos | short | 161 | +0.509 |
| bos | long | 116 | -0.408 |
| eqh_eql | short | 93 | -0.291 |
| order_block | short | 40 | -0.242 |
| fvg | long | 31 | -0.387 |
| order_block | long | 30 | -0.551 |
| fvg | short | 24 | -0.641 |
| eqh_eql | long | 24 | -0.407 |
| fib_golden_zone | long | 14 | +0.679 |
| liquidity_sweep | short | 11 | +0.502 |
| fib_golden_zone | short | 8 | +0.615 |
| liquidity_sweep | long | 3 | +0.833 |

## Interpretation & caveats (always read before acting)

- **Judge robustness by the WIDER stops, not the tightest.** `structural` (far-edge) stops are the tightest and inflate R-multiples; an edge is only real if it survives `atr_floor` (0.5·ATR min risk) and `fixed_atr` (1·ATR) — read the sensitivity table for the conservative rows.
- **BUILD here is NOT live-confirmed.** This is the de-biased *backtest* substrate. The live ledger cannot isolate first touches (cooldown removes repeats) AND fires a different, filtered population — the blended live rows above can even point the other way at tiny n. Any detector built from a BUILD cell stays gated on live-OOS as the ledger grows.
- **First touches are not iid.** Overlapping zones across 25 symbols share market-wide moves; the block bootstrap mitigates serial correlation but DSR/PBO at large n read as *well-powered*, not *risk-free* — a DSR of 1.000 / PBO of 0.000 is mostly sample size.
- **Entry is unconditional.** Every first touch is taken — no regime / trend / level-quality filter. A deployable detector needs entry filters and may behave differently (better or worse) than this unconditional average.
- **Resolution = SL/TP, no max-hold.** Realized R closes on the first SL or TP touch; still-open touches are excluded from the resolved population.

---

*Realized R through real next-bar-open entries, structural / ATR stops, and `tp_r × risk` targets, net of fees + slippage + funding via the production engine. A BUILD verdict motivates a `structural_touch` detector (still live-OOS gated), scoped to the tf(s) that actually clear the gate; NO-EDGE on a tf closes the thread for that tf. ob / fib zone types are opt-in via `--zone-types`.*
