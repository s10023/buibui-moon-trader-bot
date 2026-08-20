# ST56 — wick-anchor construction: the result is INDETERMINATE (2026-08-20)

**Verdict: INDETERMINATE, and not for want of sample. The pre-registered construction cannot
separate the hypothesis from its own intrabar tie-break rule, because the two arms have a
10.7x asymmetric exposure to that rule.** Under the pre-registered resolution (an ambiguous bar
is the loss) the wick anchor loses by −0.1072R per trade on a bootstrap CI of [−0.1121,
−0.1020]. Under the opposite resolution it WINS by +0.2386R on [+0.2332, +0.2441]. Both CIs
are tight and both exclude zero, in opposite directions. **The sign belongs to the tie-break,
not to the geometry.** No verdict on the wick anchor is licensed by this run, and the earlier
reading of it as CONFIRMED-BAD is withdrawn.

Executes the pre-registration in `2026-08-20-st56-wick-fill-anchor-power.md`, fixed at commit
`017b009` before any of these numbers existed. Run with
`poetry run python tools/wick_anchor_study.py`; measurement in `analytics/wick_anchor.py`.

## What was measured

337,294 raw `wick_fill` detector fires on BTCUSDT / ETHUSDT / SOLUSDT across 15m / 1h / 4h /
1d, of which 336,669 produced a paired trade. Both arms share the same fire set, the same legal
entry at the bar after the fire, and the same `tp_r`; only the stop differs.

| | Value |
| --- | --- |
| Fires | 337,294 |
| Paired trades | 336,669 |
| Dropped | 625 (wick wrong side 597 · unresolved 23 · no legal entry 5) |
| Wick-anchor drop rate | **0.2%** against a Decision Log reversal at ~40% |
| Median stop width | production 0.5000% · **wick 0.2925%** |
| Mean R | production −0.0145 · wick −0.1217 |

The drop rate is the one pre-registered reversal condition and it did not fire, so the pairing
itself is sound. The failure is downstream of it.

## The defect: a conservative convention stops being conservative

| Arm | Intrabar-ambiguous bars |
| --- | --- |
| Production | 4,007 |
| **Wick anchor** | **42,814** |

A bar touching both stop and target cannot be adjudicated from OHLC, so the pre-registration
resolves it as the loss. That is the standard conservative choice and it is defensible on one
arm. **It is not defensible across two arms with 10.7x different exposure to it.**

The asymmetry is structural rather than incidental. The wick stop is tighter (0.2925% against
0.5000% at the median), and because the target is `entry ± sl_dist × tp_r`, a tighter stop
drags the target in with it. Both levels sit closer to entry, so far more bars contain both.
The arm under test is therefore penalised by the tie-break in direct proportion to the very
property being tested. Flipping the rule moves the paired difference by **0.3458R** — three
times the size of the effect either reading claims to have found.

⚠ **This generalises past ST56 and is the durable result here: any paired comparison of stop
geometries in which one arm is systematically tighter carries asymmetric intrabar ambiguity,
so the tie-break rule silently owns the sign.** Stop-width comparisons are exactly the shape
this repo keeps running — the flat-2% family, the ATR-widening work, ST17's capture gradient.
Any of them resolved from OHLC alone inherits this.

## The repair, and the wall it hits

Intrabar ambiguity is resolvable with finer bars: walk 15m candles to see which level a 1h
trade reached first. `analytics.db` holds 1h, 15m, 4h, 1d and 1w, and **nothing below 15m**.

| Timeframe | Fires | Share | Resolvable from held data |
| --- | --- | --- | --- |
| 15m | 249,199 | **73.9%** | **NO — needs sub-15m bars** |
| 1h | 67,794 | 20.1% | yes, via 15m |
| 4h | 17,472 | 5.2% | yes, via 15m |
| 1d | 2,833 | 0.8% | yes, via 15m |

So **26.1% of the population can be resolved today and 73.9% cannot at any effort**, short of
acquiring sub-15m data. The resolvable subset is still ~88,000 trades, which is not a power
problem — but it is 1h and above only, and this book is 15m-dominated, so a verdict from it
would not describe the population that matters.

## Scope limit: this never tested the fallback path

Production geometry on the paired set came out **68.4% floor, 31.6% structural, and ZERO
fallback** — against 55.9% flat-2.000% in the live ledger. The cause is the entry convention:
with entry at the next bar's open the wick is almost always on the correct side of entry (597
wrong-side fires in 337,294), so the side-of-entry test essentially never fails and the flat
fallback never fires.

**This run therefore speaks only to the `min_sl_pct` floor, never to the fallback.** The live
path does not enter at the next bar's open, which is why its fallback share is so much higher.
A study of the fallback needs the live entry convention and is a different construction.

## Disclosure: the sensitivity was NOT pre-registered

The pre-registration fixed the pessimistic rule and said nothing about a bound on it. The
opposite-resolution figure was added **after** seeing a negative result that could not be
distinguished from an artifact of that rule. It is a bound, never a verdict, and a second
reading chosen after seeing the first is a second trial.

It is also worth recording why it was run at all: the check was prompted by a result that was
unwelcome. Had the tie-break flattered the hypothesis, nothing would have prompted it. A
robustness check added post hoc is only ever triggered by a result one dislikes, so the bound
lands on unwelcome findings and never on flattering ones — the bias is directional, not random.
That is a sibling of this repo's powered-null family: a check whose *application* is
conditioned on the answer, where each individual step is correct and no gate can see it.

## Decision Log

**What reverses INDETERMINATE:** a resolution rule that does not depend on OHLC tie-breaking —
sub-15m bars for the 15m cells, or 15m bars for the 1h-and-above subset with the verdict
explicitly scoped to those timeframes.

**What does NOT reverse it:** more fires. n is 336,669 and both CIs are already ±0.005. This is
not an underpowered null and more data of this shape cannot fix it.

**Not to be read as:** evidence that the wick anchor works, or that it does not. Neither
direction is licensed.
