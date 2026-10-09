# Does the short-beats-long direction claim survive without the pre-fix bos alerts?

Ticket: <https://github.com/s10023/buibui-moon-trader-bot/issues/925> (map <https://github.com/s10023/buibui-moon-trader-bot/issues/864>, register row A09) · Date 2026-10-09 · faithful re-run, no new construction, no trial spent. R, percentages and counts only.

Follow-on: none (no build; the claim is downgraded).

## Answer

The claim is downgraded. Read it as "no direction effect found (INSUFFICIENT), sign regime-contingent", never as "short beats long", and never as "direction ruled out".
Removing the pre-fix `bos` alerts is not what does it: in the original window the short-minus-long spread falls only from +0.441R to +0.391R, still positive on all three symbols and still inverted at 4h.
What does it is the day-clustering the original audit never applied, plus the ledger since. With whole UTC days resampled, the original window's spread has a 95% CI of [−0.106, +0.962] with `bos` and [−0.127, +0.853] without it: 60 days, and same-day outcomes move together so strongly that the CI is 4.7 to 5.3 times as wide as an i.i.d. one (a design effect of about 22 to 28). Over the whole ledger to 2026-10-09, without pre-fix `bos`, the spread is −0.033R on [−0.312, +0.248]. In the post-fix era it reverses to −0.232R on [−0.705, +0.203], with the point estimate negative on every symbol and every timeframe.
The issue's premise that `bos` was about 87% of May volume does not hold for the live ledger: `bos` fired 0 of 417 live alerts in May and 256 of the 2,417 resolved rows in the original window (10.6%, almost all from March).
F8 is unaffected, since the soft direction gate stays soft either way, as the ticket says.

## Method

- Source: `signal_alert_outcomes` on a read-only snapshot of `analytics.db` taken 2026-10-09 06:14 UTC; resolved rows (`win`/`loss`/`expired` with `outcome_r`), 9,422 in total.
- Original window: rows with `outcome_filled_at_ms` before 2026-06-04 00:00 UTC, scored on the stored `outcome_r` (gross basis then, as in the original). This reproduces the filed table to within a handful of rows: long n=734 −0.420 against filed 732 −0.418; short n=1,683 +0.021 against filed 1,678 +0.015.
- Pre-fix `bos`: a `bos` row with `fired_at_ms` before d5fba6c (#652, 2026-08-18 14:15:35 UTC), which made `bos` and `liquidity_sweep` causal. No `bos` row was fired before the fix and resolved after it, so the fired and filled clocks give the same split.
- Whole-ledger and era views: `outcome_r` is put onto the net basis with `portfolio.replay.restate_on_resolution_clock`, which splits on `outcome_filled_at_ms`. Post-fix era = rows fired and resolved after the fix.
- Inference uses ONE correction: a cluster bootstrap over UTC entry days (`analytics.research_guards.cluster.cluster_bootstrap_ci`, `utc_day_keys` on `candle_ts_ms`; 5,000 resamples, seed 925) of the statistic mean(short) − mean(long). `effective_independent_series` is not applied on top. The i.i.d. CI is printed only to size the design effect (the squared ratio of the two CI widths), never as a second test. A UTC day is a lower bound on the dependence unit on a 24/7 tape, so every clustered CI below is if anything too narrow.

## Results

| view | long n | long avg_r | short n | short avg_r | short − long | day-cluster 95% CI | days | i.i.d. 95% CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original window, all | 734 | −0.420 | 1,683 | +0.021 | +0.441 | [−0.106, +0.962] | 60 | [+0.342, +0.542] |
| original window, ex-`bos` | 631 | −0.425 | 1,530 | −0.034 | +0.391 | [−0.127, +0.853] | 60 | [+0.286, +0.493] |
| original window, ex-`bos` and `liquidity_sweep` | 628 | −0.431 | 1,520 | −0.041 | +0.390 | [−0.122, +0.849] | 60 | [+0.283, +0.493] |
| original window, ex-`bos`, restated net | 631 | −0.496 | 1,530 | −0.106 | +0.390 | [−0.128, +0.852] | 60 | [+0.285, +0.492] |
| whole ledger, net, all | 2,846 | −0.128 | 6,576 | −0.132 | −0.004 | [−0.286, +0.281] | 182 | [−0.061, +0.052] |
| whole ledger, net, ex pre-fix `bos` | 2,730 | −0.113 | 6,414 | −0.146 | −0.033 | [−0.312, +0.248] | 182 | [−0.092, +0.024] |
| gap era (2026-06-04 to fix), net, ex pre-fix `bos` | 650 | −0.132 | 2,275 | −0.120 | +0.011 | [−0.355, +0.378] | 75 | [−0.097, +0.109] |
| post-fix era, net, all | 1,441 | +0.051 | 2,548 | −0.181 | −0.232 | [−0.705, +0.203] | 52 | [−0.316, −0.151] |
| post-fix era, net, ex-`bos` | 1,397 | +0.041 | 2,537 | −0.180 | −0.222 | [−0.688, +0.212] | 52 | [−0.312, −0.137] |

Short − long by slice, original window ex-`bos`, then post-fix era (net):

| slice | original ex-`bos` | post-fix |
| --- | --- | --- |
| BTCUSDT | +0.246 | −0.176 |
| ETHUSDT | +0.287 | −0.228 |
| SOLUSDT | +0.697 | −0.321 |
| 15m | +0.598 (329 / 1,019) | −0.114 (1,020 / 1,958) |
| 1h | +0.193 (183 / 335) | −0.431 (324 / 475) |
| 4h | −0.203 (92 / 143) | −0.998 (82 / 99) |
| 1d | +0.308 (27 / 33) | −1.205 (15 / 16) |

## Reading

1. Faithful to the ticket's question, the original split survives removal of the pre-fix `bos` rows in its point estimate (−0.05R). `bos` was a minor share of that window, and the leak was not what made the spread.
2. The claim was never significant once same-day dependence is respected. One UTC day of trending tape resolves most same-direction alerts the same way, so 2,417 alerts carry roughly the information of 85 to 100 independent ones, and 60 days of one ~10-week regime cannot separate a direction effect from that regime. The original audit's own reconciliation ("regime-contingent, not permanent") was right; the summary line "the one OOS-robust axis is direction (short)" overstated it.
3. Later data agrees: about zero over 182 days, and reversed in point after the fix. Both CIs include zero, so this is also not evidence that long beats short. The defensible statement is that no direction effect was found and its sign has followed the regime.
4. Caveat: the ledger is post-gate, and the gate stack changed across these eras (the replay's era check counts dozens of signal-path rule changes), so the long/short mix is not one population over time. Costs are modelled, not realised.

## Quoting surfaces

The two surfaces the ticket names no longer carry the claim: AGENTS.md lists direction among the conditioning axes with no buildable edge, which this result supports, and the do-not-relitigate memory file does not quote it. It survives in the memory index line for the 2026-06-04 conditional-edge test and in that topic file, both reworded alongside this finding. The dated audits and specs that repeat it (2026-06-04 conditional-edge test, H14, H19, the H8 design, the ST104 pre-registration) are left as records of what was believed when they were written.

## Effect on #949

None directly: #949 concerns `backtest_trades`, and this study reads only the live ledger. On the live side, no pre-fix `bos` alert resolved after the fix, and removing pre-fix `bos` (and `liquidity_sweep`, 13 to 15 rows) moves no verdict here.

## Sources

[1] `docs/audits/2026-06-03-direction-axis-hard-flip.md` (the filed split). [2] Register row A09, `docs/research/2026-10-07-tested-register.md`. [3] Retest rule, #908. [4] d5fba6c (#652), the `bos`/`liquidity_sweep` causality fix. [5] Driver and raw output: `docs/plans/scratch/wayfinder-864/research/a09-direction/` (gitignored, local).
