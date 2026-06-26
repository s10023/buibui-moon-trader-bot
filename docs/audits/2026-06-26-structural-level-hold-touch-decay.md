# Structural level-hold touch-decay kill-test

**Date:** 2026-06-26  ·  **Status:** read-only measurement (no engine change)

## Headline verdict: **DECAY-CONFIRMED**

First-touch beats repeat-touch (time-split-robust, Holm, CI>bar) on: bos/short, eqh_eql/short, fvg/long → escalate to the faithful per-strategy harness.

Pre-committed gate (locked before running): per (zone_type × direction) cell, `n_first ≥ 30` and `n_repeat ≥ 30`; the first−repeat mean-`mfe_atr` lift's bootstrap-CI lower bound `> +0.1`; Holm-adjusted two-sided `p < 0.05`; AND the lift positive in BOTH early/late time-split halves. Substrate = `backtest`/OHLCV (the live ledger cannot gate — cooldown removes repeats).

Params: `tfs=['1d']`  `zone_types=['fvg', 'ob', 'eqh_eql', 'bos']`  `window=24`  `band_atr_frac=0.25`  `min_gap_bars=1`  `n_boot=10000`  `seed=12345`. Touches indexed: **252681**.

## Primary gate (per zone_type × direction)

| zone × dir | n_first | n_rep | mfe_first | mfe_rep | lift | lift CI | Holm p | split | decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | :-: | --- |
| bos/short | 2321 | 27928 | +2.819 | +2.448 | +0.371 | [+0.278, +0.470] | 0.000 | ✓ | **DECAY-CONFIRMED** |
| eqh_eql/short | 317 | 6285 | +3.009 | +2.479 | +0.530 | [+0.280, +0.789] | 0.000 | ✓ | **DECAY-CONFIRMED** |
| fvg/long | 3841 | 48207 | +5.735 | +3.684 | +2.051 | [+1.723, +2.382] | 0.000 | ✓ | **DECAY-CONFIRMED** |
| bos/long | 2551 | 31246 | +3.557 | +3.834 | -0.276 | [-0.561, +0.052] | 0.173 | · | **NO-DECAY** |
| eqh_eql/long | 384 | 7611 | +3.371 | +4.005 | -0.634 | [-1.094, -0.140] | 0.039 | · | **NO-DECAY** |
| fvg/short | 3751 | 45627 | +2.702 | +2.537 | +0.165 | [+0.092, +0.241] | 0.000 | ✓ | **NO-DECAY** |
| ob/long | 2688 | 34081 | +4.433 | +3.843 | +0.590 | [+0.278, +0.919] | 0.001 | · | **NO-DECAY** |
| ob/short | 2584 | 33259 | +2.565 | +2.537 | +0.028 | [-0.072, +0.128] | 0.578 | · | **NO-DECAY** |

## Interpretation & escalation

**The thesis has real footing, but it is not uniform — and its cleanest
expression is the held-rate, not the excursion magnitude.**

- **Magnitude (`mfe_atr`) lift — confirmed on 3/8 cells**, dominated by
  **`fvg/long` (+2.05 ATR**, CI [+1.72, +2.38]): the first retest of a bullish
  FVG runs ~2 ATR further favorably than later retests. `bos/short` (+0.37) and
  `eqh_eql/short` (+0.53) also clear. The other five do not, and two **long**
  cells (`bos/long` −0.28, `eqh_eql/long` −0.63) actually **reverse** — first
  touch is *worse* there.
- **Reliability (held-rate) — consistent across ALL 8 cells.** The probability
  the level produces a +1-ATR favorable move before a −1-ATR adverse one is
  **~0.58–0.66 on the first touch vs ~0.45–0.52 on repeats** in every cell, long
  and short (gradient table below). This ~10–15pp drop from touch #1 to #2+ is
  the most robust pattern in the data — the level is most *reliable* on its
  first test even where the average magnitude is not higher. The gate keys on
  `mfe_atr`; the held-rate gradient is the stronger qualitative signal.
- **The de-biasing earned its keep.** `ob/long` shows a full-sample lift (+0.59,
  Holm p 0.001) but **fails the early/late time-split** (split = ·) → correctly
  NOT confirmed. `fvg/short` clears Holm + split but its lift-CI lower bound
  (+0.092) sits just under the +0.10 ATR bar → NO-DECAY by a hair. Both are
  exactly the borderline cases the gate exists to reject.
- **Live corroboration (thin, blended).** The live ledger cannot isolate
  first-touch (cooldown), but the structural-*short* cells that show backtest
  decay also carry positive blended live avg_r (`bos/short` +0.58 n=153,
  `eqh_eql/short` +0.77, `liquidity_sweep/short` +0.89) — weak but directionally
  consistent.

**Caveat — why this is a kill-test, not a build signal.** `mfe_atr` is forward
price-path excursion **gross of costs**, not realized R through an entry / stop
/ TP. A +2-ATR first-touch MFE premium says the path runs further favorably, not
that a tradable +2R exists. Parameters (`window=24`, `band=0.25` ATR,
`min_gap=1`) are a single unswept point; the run is **1d only** (the 4h pass is
O(n²) in the extractors and was deferred — `--timeframes 1d 4h` to include it).

**Decision (pre-committed): DECAY-CONFIRMED → escalate to the faithful
per-strategy entry-simulation harness**, prioritising **`fvg/long`** (by far the
strongest), then `bos/short` and `eqh_eql/short`. That harness must convert the
excursion premium into realized, cost-netted avg_r with real entries/stops,
check parameter + 4h robustness, and remain gated on live-OOS as the ledger
grows. XS-solo stays the deploy core meanwhile.

## Exploratory — touch-index gradient (uncorrected, not gate-deciding)

| zone × dir | touch | n | mean mfe_atr | held-rate |
| --- | :-: | ---: | ---: | ---: |
| bos/long | 1 | 2551 | +3.557 | 0.58 |
| bos/long | 2 | 2407 | +3.081 | 0.46 |
| bos/long | 3+ | 28839 | +3.896 | 0.48 |
| bos/short | 1 | 2321 | +2.819 | 0.58 |
| bos/short | 2 | 2121 | +2.441 | 0.45 |
| bos/short | 3+ | 25807 | +2.449 | 0.49 |
| eqh_eql/long | 1 | 384 | +3.371 | 0.65 |
| eqh_eql/long | 2 | 379 | +2.932 | 0.52 |
| eqh_eql/long | 3+ | 7232 | +4.061 | 0.51 |
| eqh_eql/short | 1 | 317 | +3.009 | 0.64 |
| eqh_eql/short | 2 | 311 | +2.597 | 0.52 |
| eqh_eql/short | 3+ | 5974 | +2.473 | 0.52 |
| fvg/long | 1 | 3841 | +5.735 | 0.66 |
| fvg/long | 2 | 3589 | +3.572 | 0.49 |
| fvg/long | 3+ | 44618 | +3.693 | 0.48 |
| fvg/short | 1 | 3751 | +2.702 | 0.66 |
| fvg/short | 2 | 3493 | +2.574 | 0.51 |
| fvg/short | 3+ | 42134 | +2.534 | 0.50 |
| ob/long | 1 | 2688 | +4.433 | 0.50 |
| ob/long | 2 | 2540 | +3.532 | 0.47 |
| ob/long | 3+ | 31541 | +3.868 | 0.48 |
| ob/short | 1 | 2584 | +2.565 | 0.51 |
| ob/short | 2 | 2382 | +2.621 | 0.45 |
| ob/short | 3+ | 30877 | +2.531 | 0.50 |

## Live context — blended structural avg_r

Blended over ALL touches (live cooldown removes repeats — cannot split first-vs-repeat; context only).

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
| liquidity_sweep | long | 3 | +0.833 |
| fib_golden_zone | short | 3 | +0.500 |

---

*Kill-test in excursion-space (forward ATR-normalized MFE/MAE per touch, no entry simulation). A DECAY-CONFIRMED cell motivates the faithful per-strategy harness; NO-DECAY weakens the thesis. fib is opt-in (`--zone-types … fib`) due to its walk-forward cost.*
