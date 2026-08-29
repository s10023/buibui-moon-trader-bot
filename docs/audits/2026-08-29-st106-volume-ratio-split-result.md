# ST106 — retrace/impulse volume ratio: UNREACHABLE, and the definition is wrong

**Date:** 2026-08-29 · **SoT:** ST106 · **Pre-registration:** `docs/superpowers/specs/2026-08-29-st106-volume-ratio-split-prereg.md`
**Data:** `docs/plans/st104-study-trades.parquet` (control arm) joined to `ohlcv` · read-only, no sweep re-run

## Verdict

The pre-registered split is UNREACHABLE and was not scored. Two of the pre-registration's own
decision-log conditions fired before scoring, and either alone stops the study.

D1 fires on every cell. The ratio ≤ 0.10 bucket holds 5 trades of 21,969 in the primary cell
(15m short) against a floor of 100, and across all eight control cells it holds 20 trades of
77,577. Nothing was scored, so no outcome was inspected and no researcher degree of freedom
was spent on the answer.

D1 is normally a wait-for-more-data verdict, and here it is not. The bucket is empty for a
structural reason rather than a sample-size one: the first percentile of the adjacent-bar
volume ratio is 0.2364 in the primary cell, so the operator's 0.10 threshold sits below the
1st percentile of the entire distribution. The distribution is also stable across every
timeframe tested — p1 between 0.2172 and 0.3376, median between 0.8125 and 0.9760 from 15m
through 1d — which is the expected shape for consecutive bars on a continuous 24/7 tape, where
neighbouring bars carry broadly comparable volume. Growing the ledger 20× would buy a
pathological left-tail sample, not a measurement of the tell. **Do not re-open this on the
grounds that the ledger has grown.**

The stronger conclusion is that the tell was mis-operationalised. "Bounce back into the level
on ~10% of the impulse volume" cannot mean one bar's volume over the previous bar's volume,
because that quantity is almost never near 0.10 on any timeframe. It is far more likely a
multi-bar *leg* ratio — the whole retrace leg's volume against the whole impulse leg's volume —
which is a different construction and owes its own pre-registration. That re-registration, not
this split, is the live successor.

D3 fires independently, and it corrects a factual error in the pre-registration. The spec
states "the detector enters at the signal bar's close; the retrace bar is not yet known". The
engine does not do this. `analytics/backtest/engine.py:803` documents entry as "next candle's
open after the signal candle" and `:984` takes `entry_price = opens_np[entry_idx]`; measured on
400 sampled control fires, `entry_price` equals the stamped bar's open on 400 of 400 and its
close on 2. The signal bar is therefore `entry_time − 1 bar`, not `entry_time`.

The pinned definitions survive this correction because they are anchored to the signal bar
rather than to the entry bar, and the signal bar is recoverable exactly (the join found both
bars for 100% of trades in all eight cells, confirming no gaps). What does not survive is the
causality paragraph. Under the corrected indexing V_impulse is the sweep bar and is fully known
before entry, while V_retrace is the volume of the very bar the trade enters at the open of —
so the ratio is not usable at the production entry instant, and a gating variant would have to
enter at that bar's close. That is still one bar later than production, so the follow-up build
the spec describes is unchanged in shape; only the reason for it is different.

This audit is **not** a spec reconcile and must not be counted as one. It walks the goal,
definitions, power screen and decision log, but the pre-registered primary and secondary tests
were never executed, so the walk is partial by construction. `specs/INDEX.md` lists it under
"Also referenced by" rather than "Reconciled by", which is the correct classification.

## What was measured

Control arm `{"lookback": 50, "swing_n": 5, "tolerance_pct": 0.003}` — production defaults,
77,577 trades. Ratio = volume(`entry_time`) / volume(`entry_time − 1 bar`).

| cell | n | join | p1 | p5 | median | ratio ≤ 0.10 |
| --- | --- | --- | --- | --- | --- | --- |
| 15m short (primary) | 21,969 | 100.0% | 0.2364 | 0.3397 | 0.8238 | 5 |
| 15m long | 19,829 | 100.0% | 0.2389 | 0.3380 | 0.8253 | 1 |
| 1h short | 16,048 | 100.0% | 0.2333 | 0.3563 | 0.8437 | 8 |
| 1h long | 16,098 | 100.0% | 0.2446 | 0.3562 | 0.8519 | 5 |
| 4h short | 1,676 | 100.0% | 0.2172 | 0.3358 | 0.8125 | 1 |
| 4h long | 1,774 | 100.0% | 0.2644 | 0.3603 | 0.8517 | 0 |
| 1d short | 41 | 100.0% | 0.2728 | 0.4251 | 0.9760 | 0 |
| 1d long | 142 | 100.0% | 0.3376 | 0.4398 | 0.9285 | 0 |

The parquet is already deduped within each arm (raw = distinct on
`symbol, timeframe, direction, entry_time`, 1.00×), so the 5.29× `backtest_trades` duplication
factor does not apply here.

## Cost

Zero new compute. The study reused ST104's per-trade parquet rather than re-running a sweep,
which is also why the ST104 trap does not bite: no `BacktestSweepConfig` was constructed, so
there were no zero-cost fee, slippage or `min_sl_pct` defaults to inherit. No costed figure is
reported because no outcome was scored.

## Successor

A multi-bar leg-ratio construction, pre-registered on its own terms, with the leg boundaries
pinned before looking. It should carry the corrected entry indexing from D3 above, and it
should run the same reachability screen first — this study's whole cost was avoided by counting
the bucket before scoring it.
