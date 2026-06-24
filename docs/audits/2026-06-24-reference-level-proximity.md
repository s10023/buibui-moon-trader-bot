# Reference-level proximity audit

**Date:** 2026-06-24  ·  **Status:** read-only measurement (no engine change)

## Headline verdict: **NO-EDGE**

Live near-level cohort does not clear the de-biased gate — do not build.

Pre-committed BUILD gate (locked before running): on the **live** ledger a primary cell must clear `n >= min_n`, near-cohort bootstrap-CI lower bound `> +bar`, Holm-adjusted `p < alpha`, AND `mean(near) - mean(far) > 0` with its two-sample bootstrap CI excluding 0. Direction is split throughout. Exploratory tables are uncorrected and never gate-deciding.

Params: `bar=±0.05R`  `alpha=0.05`  `min_n=30`  `sweep_lookback=3`. Levels: MO/WO/DO, MonH/MonL, PDH/PDL, PWH/PWL (week = Monday 00:00 UTC).

## Interpretation (hand-written; tables below are tool-generated)

**Decision: do NOT build the `reference_level` detector yet** — the pre-committed gate is
not cleared on the live ledger. But this is the **underpowered-positive** kind of NO-EDGE,
not a flat null, and it **directionally confirms the journal instinct**. The pieces:

1. **The long sweep-reclaim lift is real and significant.** Live longs that swept-and-reclaimed
   a prior low (PDL/PWL) average **+0.203R vs −0.521R for longs far from any level — a +0.725R
   lift whose bootstrap CI [+0.233, +1.267] excludes zero.** "Near a swept prior low, reclaim,
   go long" genuinely separates winners from losers in the live OOS data. It fails the gate only
   on the **absolute** leg: at n=54 with high R-variance the cohort's own CI is [−0.43, +0.95]
   (Holm p 0.81), so we cannot yet prove the cohort clears +0.05R after multiple-testing
   correction. This is missing **power**, not a missing effect.

2. **The IS "BUILD" on shorts is an in-sample artifact that does NOT replicate OOS.** The
   backtest short sweep-reject cell BUILDs (+0.164, lift +0.150, n=13k, p≈0), but the live short
   cohort shows essentially no lift (−0.021). The engine replays the same history the detectors
   fired on — not true OOS; the live ledger is. Classic IS-positive / OOS-null decay, which is
   exactly why the gate is bound to live. **Correctly ignored.**

3. **IS and live disagree on which side carries** (IS → short, live → long) ⇒ trust live. The
   live per-level texture (uncorrected, suggestive only) is consistent with the long thesis:
   near (≤0.5 ATR) **PDL-long +0.435** (n=33), **WO-long +0.428** (n=39), **PDH-short +0.611**
   (n=72) all look strong; opens-as-long-support (DO/MO) are negative.

**Recommendation.** (a) Do not build — gate not cleared. (b) This is the **strongest
"revisit-later" candidate** in the backlog: the long sweep-reclaim at PDL/PWL (and possibly the
Weekly/Monday open as support) shows a real, significant lift and validates the user's discretionary
style directionally. **Re-run this exact audit when the live long-near cell roughly doubles
(≈n≥100)** — the absolute leg may then clear. (c) XS-solo stays the deploy core; this changes
nothing there. (d) Do not chase the IS short signal (decay).

## Source: `live`  (2883 entries)

### Primary cells — live (pre-committed gate)

| cell | n_near | avg_near | near CI | n_far | avg_far | lift | lift CI | Holm p | decision |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: | --- |
| P1: long sweep+reclaim @ PDL/PWL | 54 | +0.203 | [-0.427, +0.945] | 321 | -0.521 | +0.725 | [+0.233, +1.267] | 0.806 | **NO-EDGE** |
| P2: short sweep+reject @ PDH/PWH | 85 | +0.120 | [-0.244, +0.507] | 756 | +0.141 | -0.021 | [-0.313, +0.274] | 0.806 | **NO-EDGE** |

### Exploratory — live (uncorrected, not gate-deciding)

#### Band gradient (live)

avg_r by direction × nearest-level band.

| direction | band | n | avg_r |
| --- | --- | ---: | ---: |
| long | <=0.25 | 221 | -0.214 |
| long | <=0.5 | 136 | -0.234 |
| long | <=1.0 | 172 | -0.498 |
| long | >1.0 | 321 | -0.521 |
| short | <=0.25 | 495 | -0.063 |
| short | <=0.5 | 342 | +0.126 |
| short | <=1.0 | 440 | -0.028 |
| short | >1.0 | 756 | +0.141 |

#### Per-level near cohort (live)

avg_r for the near cohort (<=0.5 ATR) by nearest level × direction.

| level | direction | n | avg_r |
| --- | --- | ---: | ---: |
| PDH | short | 72 | +0.611 |
| PDL | long | 33 | +0.435 |
| WO | long | 39 | +0.428 |
| PDL | short | 93 | +0.148 |
| DO | short | 307 | +0.128 |
| PWH | short | 44 | +0.124 |
| MonH | short | 49 | -0.083 |
| WO | short | 117 | -0.138 |
| PWL | short | 36 | -0.209 |
| PDH | long | 57 | -0.328 |
| DO | long | 86 | -0.514 |
| MonL | short | 70 | -0.517 |
| MO | short | 49 | -0.545 |
| MO | long | 55 | -0.581 |

## Source: `backtest`  (845422 entries)

### Primary cells — backtest (pre-committed gate)

| cell | n_near | avg_near | near CI | n_far | avg_far | lift | lift CI | Holm p | decision |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: | --- |
| P1: long sweep+reclaim @ PDL/PWL | 14077 | -0.198 | [-0.236, -0.160] | 184996 | -0.154 | -0.044 | [-0.073, -0.016] | 0.000 | **NO-EDGE** |
| P2: short sweep+reject @ PDH/PWH | 13435 | +0.164 | [+0.119, +0.208] | 172841 | +0.014 | +0.150 | [+0.117, +0.182] | 0.000 | **BUILD** |

### Exploratory — backtest (uncorrected, not gate-deciding)

#### Band gradient (backtest)

avg_r by direction × nearest-level band.

| direction | band | n | avg_r |
| --- | --- | ---: | ---: |
| long | <=0.25 | 84590 | -0.152 |
| long | <=0.5 | 67198 | -0.173 |
| long | <=1.0 | 96865 | -0.167 |
| long | >1.0 | 184996 | -0.154 |
| short | <=0.25 | 78597 | +0.136 |
| short | <=0.5 | 66334 | +0.107 |
| short | <=1.0 | 94001 | +0.126 |
| short | >1.0 | 172841 | +0.014 |

#### Per-level near cohort (backtest)

avg_r for the near cohort (<=0.5 ATR) by nearest level × direction.

| level | direction | n | avg_r |
| --- | --- | ---: | ---: |
| PWH | short | 6988 | +0.524 |
| PWL | short | 4718 | +0.315 |
| MonH | short | 9259 | +0.184 |
| MonL | short | 12955 | +0.176 |
| DO | short | 52804 | +0.172 |
| WO | short | 17770 | +0.105 |
| WO | long | 18940 | +0.083 |
| PDL | short | 17059 | +0.012 |
| MO | short | 8557 | -0.098 |
| PDH | short | 14821 | -0.112 |
| PDL | long | 13736 | -0.123 |
| MO | long | 8659 | -0.147 |
| DO | long | 56579 | -0.160 |
| PDH | long | 18816 | -0.163 |
| PWL | long | 4765 | -0.165 |
| MonH | long | 9754 | -0.285 |
| MonL | long | 13347 | -0.295 |
| PWH | long | 7192 | -0.489 |

---

_Limitation: this measures whether proximity modulates **already-fired** signals — a proxy for a level-trigger's edge, since the ledger cannot contain triggers that never fired. A BUILD verdict gates a separate `reference_level` detector spec._
