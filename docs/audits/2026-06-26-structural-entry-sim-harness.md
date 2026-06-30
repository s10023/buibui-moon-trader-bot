# Faithful per-strategy structural entry-sim harness

**Date:** 2026-06-26  ·  **Status:** read-only measurement (no engine change)

## Headline verdict: **BUILD**

First-touch is a positive-EV, cost-netted tradable entry (boot-CI>0, Holm, n≥MinTRL, DSR/PBO) on: bos/long, bos/short, eqh_eql/long, fvg/long, fvg/short → build a `structural_touch` detector for these cells (live-OOS gated).

Pre-committed BUILD gate (locked before running): on the **headline config** (`tp_r=2.0` × `sl_model=atr_floor` × `tf=1d`), first-touch (`touch_index==1`) net realized R must clear `n_first ≥ 30`, a block-bootstrap CI lower bound `> 0.0`, a Holm-adjusted `p < 0.05` across the (zone_type × direction) family, `n_first ≥ MinTRL(0.95)`, AND `DSR ≥ 0.95 ∧ PBO ≤ 0.5` over the tp_r × sl_model trial family. The first−repeat decay lift is secondary corroboration. Substrate = backtest/OHLCV (the live ledger cannot gate — cooldown removes repeats).

Params: `tfs=['1d', '4h']`  `zone_types=['fvg', 'eqh_eql', 'bos']`  `tp_r_grid=[1.0, 1.5, 2.0, 3.0]`  `sl_models=['structural', 'atr_floor', 'fixed_atr']`  `fee_bps=5.0`  `slippage_bps=2.0`  `n_boot=10000`  `seed=12345`. Resolved trades: **35112730**.

## Primary gate (per zone_type × direction, headline config)

| zone × dir | n_first | n_rep | first_avg_r | boot CI | Holm p | MinTRL | DSR | PBO | decay lift | split | decision |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | :-: | --- |
| bos/long | 2542 | 31166 | +0.218 | [+0.154, +0.285] | 0.000 | 127 | 1.000 | 0.122 | +0.321 | ✓ | **BUILD** |
| bos/short | 2311 | 27856 | +0.188 | [+0.124, +0.253] | 0.000 | 169 | 1.000 | 0.045 | +0.243 | ✓ | **BUILD** |
| eqh_eql/long | 384 | 7599 | +0.407 | [+0.253, +0.556] | 0.000 | 39 | 0.970 | 0.226 | +0.430 | ✓ | **BUILD** |
| fvg/long | 3841 | 48092 | +0.540 | [+0.487, +0.593] | 0.000 | 23 | 1.000 | 0.000 | +0.649 | ✓ | **BUILD** |
| fvg/short | 3718 | 45482 | +0.514 | [+0.465, +0.564] | 0.000 | 25 | 1.000 | 0.000 | +0.550 | ✓ | **BUILD** |
| eqh_eql/short | 317 | 6276 | +0.420 | [+0.248, +0.589] | 0.000 | 37 | 1.000 | 0.541 | +0.407 | ✓ | **NO-EDGE** |

## Robustness — tp_r × sl_model sensitivity (reported, not gate-deciding)

First-touch net avg_r at tf=`1d` (n≥30); gross in parens.

| zone × dir | tp_r | sl_model | n | net avg_r | gross |
| --- | ---: | --- | ---: | ---: | ---: |
| bos/long | 1.0 | atr_floor | 2545 | +0.136 | +0.167 |
| bos/long | 1.0 | fixed_atr | 2546 | +0.138 | +0.164 |
| bos/long | 1.0 | structural | 2545 | +0.158 | +0.216 |
| bos/long | 1.5 | atr_floor | 2543 | +0.205 | +0.240 |
| bos/long | 1.5 | fixed_atr | 2543 | +0.167 | +0.197 |
| bos/long | 1.5 | structural | 2543 | +0.227 | +0.288 |
| bos/long | 2.0 | atr_floor | 2542 | +0.218 | +0.257 |
| bos/long | 2.0 | fixed_atr | 2538 | +0.159 | +0.193 |
| bos/long | 2.0 | structural | 2542 | +0.238 | +0.303 |
| bos/long | 3.0 | atr_floor | 2534 | +0.235 | +0.282 |
| bos/long | 3.0 | fixed_atr | 2532 | +0.114 | +0.158 |
| bos/long | 3.0 | structural | 2534 | +0.267 | +0.340 |
| bos/short | 1.0 | atr_floor | 2317 | +0.078 | +0.105 |
| bos/short | 1.0 | fixed_atr | 2320 | +0.135 | +0.153 |
| bos/short | 1.0 | structural | 2318 | +0.175 | +0.247 |
| bos/short | 1.5 | atr_floor | 2313 | +0.149 | +0.172 |
| bos/short | 1.5 | fixed_atr | 2313 | +0.173 | +0.185 |
| bos/short | 1.5 | structural | 2314 | +0.238 | +0.306 |
| bos/short | 2.0 | atr_floor | 2311 | +0.188 | +0.206 |
| bos/short | 2.0 | fixed_atr | 2310 | +0.188 | +0.194 |
| bos/short | 2.0 | structural | 2311 | +0.266 | +0.329 |
| bos/short | 3.0 | atr_floor | 2305 | +0.211 | +0.222 |
| bos/short | 3.0 | fixed_atr | 2306 | +0.166 | +0.162 |
| bos/short | 3.0 | structural | 2307 | +0.275 | +0.332 |
| eqh_eql/long | 1.0 | atr_floor | 384 | +0.252 | +0.286 |
| eqh_eql/long | 1.0 | fixed_atr | 384 | +0.268 | +0.297 |
| eqh_eql/long | 1.0 | structural | 384 | +0.057 | +0.302 |
| eqh_eql/long | 1.5 | atr_floor | 384 | +0.361 | +0.400 |
| eqh_eql/long | 1.5 | fixed_atr | 384 | +0.330 | +0.367 |
| eqh_eql/long | 1.5 | structural | 384 | +0.154 | +0.404 |
| eqh_eql/long | 2.0 | atr_floor | 384 | +0.407 | +0.453 |
| eqh_eql/long | 2.0 | fixed_atr | 384 | +0.359 | +0.406 |
| eqh_eql/long | 2.0 | structural | 384 | +0.199 | +0.456 |
| eqh_eql/long | 3.0 | atr_floor | 384 | +0.480 | +0.542 |
| eqh_eql/long | 3.0 | fixed_atr | 383 | +0.316 | +0.379 |
| eqh_eql/long | 3.0 | structural | 384 | +0.285 | +0.557 |
| eqh_eql/short | 1.0 | atr_floor | 317 | +0.226 | +0.249 |
| eqh_eql/short | 1.0 | fixed_atr | 317 | +0.274 | +0.287 |
| eqh_eql/short | 1.0 | structural | 317 | +0.225 | +0.287 |
| eqh_eql/short | 1.5 | atr_floor | 317 | +0.323 | +0.341 |
| eqh_eql/short | 1.5 | fixed_atr | 317 | +0.376 | +0.380 |
| eqh_eql/short | 1.5 | structural | 317 | +0.311 | +0.368 |
| eqh_eql/short | 2.0 | atr_floor | 317 | +0.420 | +0.429 |
| eqh_eql/short | 2.0 | fixed_atr | 317 | +0.415 | +0.410 |
| eqh_eql/short | 2.0 | structural | 317 | +0.387 | +0.435 |
| eqh_eql/short | 3.0 | atr_floor | 317 | +0.501 | +0.502 |
| eqh_eql/short | 3.0 | fixed_atr | 316 | +0.422 | +0.405 |
| eqh_eql/short | 3.0 | structural | 317 | +0.436 | +0.476 |
| fvg/long | 1.0 | atr_floor | 3842 | +0.424 | +0.466 |
| fvg/long | 1.0 | fixed_atr | 3839 | +0.288 | +0.321 |
| fvg/long | 1.0 | structural | 3842 | +0.445 | +0.505 |
| fvg/long | 1.5 | atr_floor | 3842 | +0.503 | +0.550 |
| fvg/long | 1.5 | fixed_atr | 3833 | +0.356 | +0.394 |
| fvg/long | 1.5 | structural | 3842 | +0.573 | +0.637 |
| fvg/long | 2.0 | atr_floor | 3841 | +0.540 | +0.593 |
| fvg/long | 2.0 | fixed_atr | 3831 | +0.409 | +0.453 |
| fvg/long | 2.0 | structural | 3841 | +0.680 | +0.748 |
| fvg/long | 3.0 | atr_floor | 3838 | +0.594 | +0.657 |
| fvg/long | 3.0 | fixed_atr | 3829 | +0.482 | +0.538 |
| fvg/long | 3.0 | structural | 3839 | +0.802 | +0.881 |
| fvg/short | 1.0 | atr_floor | 3731 | +0.452 | +0.483 |
| fvg/short | 1.0 | fixed_atr | 3723 | +0.318 | +0.334 |
| fvg/short | 1.0 | structural | 3732 | +0.497 | +0.548 |
| fvg/short | 1.5 | atr_floor | 3727 | +0.496 | +0.525 |
| fvg/short | 1.5 | fixed_atr | 3708 | +0.329 | +0.343 |
| fvg/short | 1.5 | structural | 3729 | +0.594 | +0.643 |
| fvg/short | 2.0 | atr_floor | 3718 | +0.514 | +0.540 |
| fvg/short | 2.0 | fixed_atr | 3705 | +0.374 | +0.385 |
| fvg/short | 2.0 | structural | 3722 | +0.678 | +0.726 |
| fvg/short | 3.0 | atr_floor | 3696 | +0.560 | +0.582 |
| fvg/short | 3.0 | fixed_atr | 3690 | +0.343 | +0.350 |
| fvg/short | 3.0 | structural | 3707 | +0.798 | +0.844 |

## Live context — blended structural avg_r

Blended over ALL touches (live cooldown removes repeats — cannot isolate first-touch; context only).

| strategy | dir | n | avg_r |
| --- | --- | ---: | ---: |
| bos | short | 153 | +0.577 |
| bos | long | 103 | -0.388 |
| fvg | long | 31 | -0.387 |
| order_block | short | 30 | -0.011 |
| order_block | long | 27 | -0.778 |
| fvg | short | 24 | -0.641 |
| eqh_eql | long | 22 | -0.376 |
| eqh_eql | short | 17 | +0.773 |
| liquidity_sweep | short | 9 | +0.889 |
| fib_golden_zone | long | 9 | +0.672 |
| fib_golden_zone | short | 3 | +0.500 |
| liquidity_sweep | long | 3 | +0.833 |

## Interpretation & caveats (always read before acting)

- **Judge robustness by the WIDER stops, not the tightest.** `structural` (far-edge) stops are the tightest and inflate R-multiples; an edge is only real if it survives `atr_floor` (0.5·ATR min risk) and `fixed_atr` (1·ATR) — read the sensitivity table for the conservative rows.
- **BUILD here is NOT live-confirmed.** This is the de-biased *backtest* substrate. The live ledger cannot isolate first touches (cooldown removes repeats) AND fires a different, filtered population — the blended live rows above can even point the other way at tiny n. Any detector built from a BUILD cell stays gated on live-OOS as the ledger grows.
- **First touches are not iid.** Overlapping zones across 25 symbols share market-wide moves; the block bootstrap mitigates serial correlation but DSR/PBO at large n read as *well-powered*, not *risk-free* — a DSR of 1.000 / PBO of 0.000 is mostly sample size.
- **Entry is unconditional.** Every first touch is taken — no regime / trend / level-quality filter. A deployable detector needs entry filters and may behave differently (better or worse) than this unconditional average.
- **Resolution = SL/TP, no max-hold.** Realized R closes on the first SL or TP touch; still-open touches are excluded from the resolved population.

---

*Realized R through real next-bar-open entries, structural / ATR stops, and `tp_r × risk` targets, net of fees + slippage + funding via the production engine. A BUILD verdict motivates a `structural_touch` detector (still live-OOS gated); NO-EDGE closes the thread. ob / fib zone types and the 4h robustness pass are opt-in via `--zone-types` / `--timeframes`.*
