# ST17 — give-back / heat-and-run on the live outcome ledger (2026-08-18)

**Verdict: the book gives back a median 0.81R of open profit and keeps 55% of its peak, and 28.9% of trades that reach +1R finish at or below zero.** The effect is large and its bootstrap CI [+0.7153, +0.9455] is nowhere near zero. Capture falls monotonically with timeframe (+0.633 at 15m to −0.423 at 1d, holding stop width constant), but **that gradient is the flat-2%-SL defect resurfacing rather than an independent property of give-back** — see "The timeframe gradient is a stop-width artifact". This is a DESCRIPTIVE result, it proposes no rule, and it does **not** re-open exit tuning as a P&L lever — that question was closed on 2026-08-07 and this measurement is consistent with the closure.

Pre-registered 2026-08-17 as the give-back / heat-and-run pre-registration, fixed before any number was computed. The filename is deliberately not cited: the spec-reconcile counter derives from an audit naming a spec file, and this audit executes that pre-registration rather than reconciling an implementation against it. Run with `poetry run python tools/giveback_study.py`; measurement in `analytics/giveback.py`.

## What was measured

Population: 5,230 resolved rows of `signal_alert_outcomes` carrying a non-null `outcome_r` (5,170 at pre-registration; the ledger accrued 60 rows since). **Zero rows were unmeasurable** — every hold window covered at least one OHLCV bar, so nothing is hidden behind a data gap.

`MFE_R` is the favourable excursion between `candle_ts_ms` and `outcome_filled_at_ms` in units of that row's own `R_unit = abs(entry_price - sl_price)`; `giveback_R = MFE_R - outcome_r`, with `outcome_r` restated onto the post-parity net basis. The primary statistic is conditional on `MFE_R >= 1.0R`, the pre-registered threshold X.

## The result

Conditional population — rows reaching at least +1.0R, n = 1,790 (34.2% of the ledger):

| Quantity | Value |
| --- | --- |
| Share finishing at `outcome_r <= 0` | **28.9%** |
| Median `giveback_R` | **+0.8131** |
| IQR `giveback_R` | [+0.4094, +1.9826] |
| Median capture ratio (`outcome_r / MFE_R`) | **+0.5545** |
| Bootstrap CI on median `giveback_R` | [+0.7153, +0.9455] |
| Intrabar-ambiguous rows | 28 (1.6%) |

Reported beside it, never as the headline — the whole population reads 64.2% finishing at or below zero, median `giveback_R` +1.106, median capture −0.715. **That line is an artifact and must not be quoted:** it is dominated by never-green losers, whose `giveback_R` is ~1.0 by construction. It is included only because the pre-registration says to report it.

## The timeframe gradient is a stop-width artifact

Capture falls monotonically across timeframes. It is tempting to read this as "the book gives back more on higher timeframes", and that reading is wrong.

Every stop in the ledger is **2.000% of entry at the median on every timeframe** — 15m, 1h, 4h and 1d alike, with an IQR of exactly [2.000, 2.000]. That is the known flat-2%-SL defect, and it means the stop is not scaled to each timeframe's range. What does differ is how long a trade survives: median bars held runs **96 / 24 / 10 / 2** from 15m to 1d.

Holding stop width constant (the ~2% stratum only, `MFE_R >= 1.0`):

| TF | n | share `<= 0` | median `giveback_R` | median capture |
| --- | ---: | ---: | ---: | ---: |
| 15m | 1,006 | 19.5% | +0.625 | +0.633 |
| 1h | 386 | 27.2% | +0.872 | +0.556 |
| 4h | 172 | 50.6% | +2.112 | −0.002 |
| 1d | 54 | 68.5% | +2.315 | −0.423 |

The gradient survives — indeed strengthens — which rules out stop-width *composition* as the explanation but does not make the finding independent of stop width. The mechanism is the opposite one: because the stop is a fixed 2% regardless of timeframe, it is far tighter relative to a daily bar's range than to a 15m bar's, so a 1d trade is resolved in a median of **two bars**. On 1d, what the give-back statistic is measuring is largely a fixed stop being taken out by ordinary bar noise before the move resolves — not a book that earned profit and surrendered it.

**So the honest reading is that the timeframe gradient restates the flat-2%-SL defect in a new statistic.** It is corroboration of a known defect, not a new axis. Read it alongside that defect's own standing verdict, which holds `CONFIRMED-BAD` at 15m only and leaves 1h/4h **INSUFFICIENT** — untested, not "widening works". Nothing here licenses widening a stop on any timeframe.

The pooled, unstratified version of this table is in `tools/giveback_study.py`'s output and is not reproduced here, because its stop-width composition differs slightly per timeframe and the stratified table above is the one that supports the claim.

## Prior art — and what this does not re-open

**This is not the first measurement of give-back on this book, and the earlier one is why the framing above matters.**

The 2026-06-11 MFE/MAE diagnostic measured the *expired* cohort and found 63.7% reached ≥1R with a median MFE of +1.35R, concluding **GO / exit-fixable**. That thesis was closed on 2026-08-07: a whole-book time-stop sweep with the sample held constant is **monotone in hold time and negative at every setting** (0.25× −0.023R through 4.00× −0.148R), with no interior optimum — the best setting tested is the shortest, and it still loses. Locking in earlier does not make the book profitable. The standing instruction is: do not re-open exit tuning on this book as a profitability lever.

**This audit is consistent with that closure and does not challenge it.** It differs in population (all 5,230 resolved rows, not the expired cohort alone) and in statistic (the give-back and capture ratio, not the MFE level), so it is complementary rather than duplicative — but it measures the same underlying phenomenon, and the question "could an earlier exit capture this?" has already been answered **no** by direct test. A large give-back number is not evidence against that answer: profit surrendered is real, and the sweep that tried to bank it still lost.

## What this does not license

**No rule is proposed here, and none may be read out of this**, and the bar is higher than the pre-registration alone implies, because the whole-book version of the obvious rule has already been tested and lost (above). The pre-registration is explicit: the moment anyone proposes "trail at 1R" or "move to breakeven at X", that is a new construction fitted to the same data and it inherits the full three-leg gate (`DSR >= 0.95 ∧ PBO <= 0.5 ∧ boot_lo > 0`) under its own pre-registration. A give-back measurement says profit was surrendered; it does not say any rule could have kept it.

Two reasons that gap is wide rather than technical:

1. **`MFE_R` is read from bar extremes**, so it is the excursion an omniscient exit would have caught — an upper bound on what any real rule could capture, and never quotable as forgone profit.
2. **A stop that banks the give-back also truncates the winners**, and this study measures only one side of that trade. The 247 wins in the ledger reached their TP; a breakeven stop that fires on the 28.9% would also have to survive its effect on them.

## Traps defused, and the one that is not

**The excursion window mirrors the resolver exactly** — `open_time > candle_ts_ms`, strictly after the signal candle, because entry fills at the OPEN of the first post-signal bar (`analytics/signal/outcome_backfill.py`). Counting the signal bar's own high would admit excursion that existed before entry did — the look-ahead class that voided the structural-touch BUILD — and would leave `MFE_R` and `outcome_r` reading two different bar populations. A mutation flipping `>` to `>=` fails `test_signal_candle_bar_is_excluded_from_mfe` and nothing else, so the guard is reachable rather than decorative.

**Cost basis is restated on the resolution clock**, via a helper now shared with `replay_ledger` so the two cannot spell the rule differently. `MFE_R` is a raw price quantity; subtracting a mixed-basis `outcome_r` from it would be a category error.

**Stop width is reported but must not be read causally.** The `<1%` bucket shows median give-back +2.476 and capture −0.408 against the flat-2% mass at +0.774 and +0.580 — but modelled drag is `2(fee+slip)·entry/risk`, which scales inversely with stop width, so narrow-stop rows pay more of it in R by construction. The comparison inherits a bias, not a level shift. n is also 109 against 1,618.

**Strategy is confounded with timeframe** and is published for completeness only. The four worst cells by capture ratio — `wick_fill` (−0.586, n=74), `trend_day` (−0.370, n=44), `cvd_divergence` (−0.314, n=23), `orb` (−0.088, n=59) — are all detectors that run predominantly on the higher timeframes where the TF pattern already predicts poor capture. Nothing here separates the detector from its timeframe.

**The unresolved one:** the pre-registration's `giveback_R = MFE_R - outcome_r` and its stated invariant that never-green losers give back 0 are consistent only if `MFE_R` is the *unfloored* favourable extreme. The unfloored form is used. This is visible only in the whole-population line, which is already labelled an artifact; the conditional statistic is untouched because every row in it went green by at least 1R.

## Side finding — 29 ledger rows where `rr_ratio` disagrees with `tp_price`

Found while validating the join against the invariant "a win touched its TP, so `MFE_R >= rr_ratio`". Seven of 247 wins violate it — and all 247 satisfy the *true* invariant `MFE_R >= implied_rr(tp_price)`, because the resolver's touch test uses `tp_price` while the recorded payoff uses `rr_ratio`. Across the whole ledger, 29 of 5,299 rows (0.55%) carry an `rr_ratio` disagreeing with their own `tp_price` by more than 0.01R, with a largest disagreement of 4.2251R. **The direction is unanimous: in 29 of 29 the stored ratio is the larger one.** Only the 8 wins among them are materially affected — a loss pays −1.0 whatever the ratio says — and those 8 are credited more R than their own TP level implies.

Small, but it is a live-ledger accounting defect rather than a study artifact, and it runs one way. Not fixed here — it belongs to the alert/resolver path, not to a descriptive study.

## Limits stated up front

- **Costs are modelled, not realised.** Raw stays exactly −1.0 = declared risk, so no figure here expresses gap risk and each is an optimistic bound whose error runs one way.
- **Intrabar path is unknown.** 28 conditional rows (1.6%) sit on an exit bar that carried both the stop and the ≥1R favourable extreme; bar data cannot order them. They are counted, not resolved.
- **Every figure is an average across rule changes.** The sample straddles 46 ledger boundaries between 2026-03-25 and 2026-08-17; the largest single-era sub-sample is n=1,037, or 20%.
- **Scope is the live ledger only.** Backtest scope was deliberately not priced: it needs dedup on `(symbol, timeframe, strategy, direction, entry_time)` first, against a ~5.29× duplication factor.
