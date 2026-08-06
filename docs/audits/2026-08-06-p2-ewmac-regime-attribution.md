# P2 §6 per-regime attribution — EWMAC trend sleeve

**Date:** 2026-08-06
**Scope:** the one pre-registered check in the P2 EWMAC spec that was never
built, filed as Finding 2 of
`docs/audits/2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md`.
**Question (P2 §6, verbatim):** "Attribution — per-instrument and per-regime (via
`classify_series`): does the trend Sharpe concentrate in trend regimes, as
theory predicts?"

## Verdict — NO, the concentration is not established

The point estimates lean the way theory predicts, and **every one of them
dissolves once the cross-section's correlation is accounted for.** Nothing here
rescues EWMAC from the shelf, and the check that was supposed to decide it has
now actually been run.

This does not change the sleeve's FAIL (+0.36, CI includes 0). It changes the
**shelving rationale**, which until today rested on an unrun diagnostic.

## What was measured

`analytics/forecast/attribution.py` (new) over the existing universe book — no
new data, no new sweep, no gate change. Two views:

- **instrument-day** — each symbol's own net return, labelled by *that symbol's*
  own regime. The direct form of the theory question, since a trend regime is a
  property of an instrument.
- **book-day** — the book's daily return, labelled by the dominant regime across
  the instruments actually held that day. Coarser, but it is the level the
  shelving decision speaks to.

Run it with `PYTHONPATH=. poetry run python tools/forecast_audit.py --regime`.

### Headline table (lag=1, the evidential read)

| view | regime | n_obs | share | mean_bps | sharpe | t_corr | t_naive |
| --- | --- | --- | --- | --- | --- | --- | --- |
| instrument-day | high_vol | 8487 | 0.204 | −1.197 | −0.180 | −0.297 | −0.870 |
| instrument-day | **trend** | 29987 | **0.721** | +1.658 | **+0.244** | **+0.757** | +2.213 |
| instrument-day | range | 2697 | 0.065 | −5.165 | −1.348 | −1.253 | −3.665 |
| book-day | high_vol | 488 | 0.196 | +2.419 | **+0.431** | +0.498 | +0.498 |
| book-day | **trend** | 1980 | 0.795 | +1.799 | **+0.372** | +0.866 | +0.866 |
| book-day | range | 5 | 0.002 | −0.188 | −0.122 | −0.014 | −0.014 |

Unconditional comparators: instrument-day Sharpe **+0.094** (n=41571); book
Sharpe **+0.379** (n=2525), consistent with the standing +0.36.

## Why the answer is NO, in four steps

**1. The raw split looks like a win.** Conditioning on trend takes the
instrument-day Sharpe from +0.094 unconditional to +0.244, and the non-trend
complement is negative (−0.332, mean −2.056 bps). Read naively — as `t_naive`
invites — the trend cell is significant at t=2.21 and range is strongly negative
at t=−3.67.

**2. Those t-stats are inflated ~2.92×, and the correction kills all of them.**
Pooling 41,571 symbol-days treats 25 crypto perps as 25 independent bets. Mean
pairwise correlation of the sleeve's per-instrument net returns is **0.315**, so
under an equicorrelation approximation the book carries the noise reduction of
**2.92 effective independent series, not 25**. Deflating by `sqrt(k/n_eff)`:

| cell | t_naive | t_corr |
| --- | --- | --- |
| trend | +2.213 | **+0.757** |
| range | −3.665 | **−1.253** |
| high_vol | −0.870 | −0.297 |
| non-trend complement | −1.87 | **−0.64** |

Not one cell separates from noise. **The correction is applied inside the tool**,
not left to a caveat — `RegimeCell.t_stat` is the deflated figure and
`t_stat_naive` is retained beside it so the adjustment is auditable.

**3. There is no dose-response.** If trend Sharpe genuinely concentrated in
trend regimes, tightening the slope threshold that defines "trend" should raise
the trend cell's Sharpe. It does not — over a 16× range of thresholds the Sharpe
wobbles without direction while significance decays with n:

| slope threshold | trend share | sharpe | t_naive |
| --- | --- | --- | --- |
| 0.005 (default) | 0.721 | +0.244 | 2.21 |
| 0.010 | 0.660 | +0.261 | 2.26 |
| 0.020 | 0.534 | +0.255 | 1.99 |
| 0.030 | 0.429 | +0.291 | 2.03 |
| 0.050 | 0.272 | +0.112 | 0.62 |
| 0.080 | 0.128 | +0.198 | 0.76 |

A plausible mechanism for the flatness: EWMAC's forecast is *already* scaled
continuously by trend strength, so the sleeve harvests the dose itself. A regime
label can only add an on/off boundary, and the data says that boundary is not
worth much.

**4. The book-day view shows nothing at all.** high_vol book-days (+0.431)
slightly *beat* trend book-days (+0.372), both with t < 1. The book is "in trend"
79.5% of days by dominant label, so this view has almost no variation to
explain — which is itself the finding: there is no book-level regime timing here.

## The decision-relevant counterfactual

Would gating the sleeve on regime actually help? Built **optimistically** — each
instrument's exposure zeroed on its own non-trend days, with *no* turnover charged
for exiting and re-entering, so the real variant is strictly worse:

| book | Sharpe | block-bootstrap CI |
| --- | --- | --- |
| base | +0.379 | [−0.330, +1.088] |
| regime-gated (optimistic) | +0.481 | [−0.243, +1.240] |
| difference series | +0.102 | **[−0.459, +0.715]** |

The gain is +0.102 Sharpe with a CI straddling zero, from a variant that flatters
itself by ignoring re-entry costs on ~28% of days. **This does not justify a
costed, gate-tested follow-up.** Recorded so nobody has to re-derive it.

## Traps handled (all four were pre-registered in the task brief)

1. **`classify_series` causality — checked first, and it is causal.** EWM slope,
   Wilder ATR, and a *trailing* rolling quantile: no forward window, no
   full-sample statistic. But the label for bar `t` reads bar `t`'s own
   high/low/close, so attributing `return_t` to `regime_t` sorts returns by a
   label that partly knows the return. The headline therefore uses **lag=1**.
   **The contamination was measured, not asserted** — under lag=0 the
   instrument-day high_vol cell reads **+0.762 Sharpe (t_naive +3.673)** against
   **−0.180** lagged, and trend reads −0.007 against +0.244. A naive
   contemporaneous attribution would have concluded "EWMAC's edge concentrates in
   *high-vol* regimes", the opposite of both the theory and the honest result.
   `lag=0` is kept in the output so this stays visible.
2. **No MinTRL leg was added.** The gate remains three legs. This is attribution,
   not a gate change.
3. **Small-n cells are visible and were not allowed to drive anything.** `n_obs`
   is reported per cell. The worked example is book-day/range at **n=5**, which
   under lag=0 prints a Sharpe of **+28.7** — meaningless, and obviously so only
   because n is printed beside it.
4. **Goldens unmoved.** The sleeve is read-only research and touches nothing in
   the backtest pipeline.

## What this changes

- **EWMAC stays shelved, and the escape hatch is now closed by measurement.** The
  standing verdict — "structurally real but FAILS the gate, SHELVED **as a
  diversifier candidate**" — used phrasing that presupposed the answer to this
  unrun check. The regime-conditional reading is now tested and unsupported.
- **The binding constraint is unchanged**: the next edge needs genuinely new
  data. This closes a candidate rather than opening one.
- **A reusable lesson about the cross-section.** Any future audit that pools
  symbol-days across this universe inherits the same ~2.92× t-stat inflation.
  `effective_independent_series` is exported from `analytics.forecast` for reuse;
  the same trap applies to the XS sleeve's per-symbol slices.

## Follow-ups filed (none fixed here)

1. **`analytics/regime.py::classify_series` labels 72% of crypto symbol-days
   "trend"** at its default threshold. That is a live setting used by the regime
   gate elsewhere in the repo, not just by this audit — worth asking whether a
   classifier that is "on" three-quarters of the time is doing the work its
   consumers assume.
2. **Amend P2 §1/§6** to state the three-leg gate and describe MinTRL as a
   reported stamp (carried over from #566, still open).
3. **Add positive controls to the four causality guards** (carried over from
   #566, still open). This branch's own lag guard has one, and it was
   mutation-verified: removing `.shift(lag)` kills two tests.
