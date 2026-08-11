# Ensemble walk-forward — pre-registered design

**Date:** 2026-08-11. **Verdict:** `docs/audits/2026-08-11-ensemble-walkforward.md`
**Drivers:** `docs/plans/scratch/ensemble_walkforward.py` (Stage 1),
`docs/plans/scratch/ensemble_walkforward_gate.py` (Stage 2) — gitignored, on disk,
re-runnable, and covered by `LEDGER_DIRS` in `deploy/backup-analytics.sh`.

**This document was written before either script ran.** Its scratch original is
`docs/plans/scratch/ensemble_walkforward_prereg.md`; this is that file promoted
unchanged apart from the cross-references and the amendment log at the end.

## Question

The 2026-08-08 half-split found win rate monotone in an ensemble score (28.1% →
48.9%, k≥4 moving −0.121R → +0.058R). Two things were left unproven: the fit was
**one static split**, and the score **never faced the three-leg gate**.

Does the relationship survive a **causal rolling refit**, and is the resulting
effect large enough that the gate is reachable at this ledger length?

## Success metric — the only number that decides Stage 2

**Book-day overlay excess ≥ +0.139 R/day.**

That is the minimum per-day mean excess needed to clear DSR ≥ 0.95 at n_obs = 74
with a **single pre-registered trial** — the most generous trial family anyone
could honestly claim. Computed against `analytics.research_guards`:

| observations | 1 trial | 10 trials | 30 trials |
| --- | --- | --- | --- |
| 62 | +0.152R | +0.317R | +0.370R |
| 74 | +0.139R | +0.303R | +0.356R |
| 84 | +0.130R | +0.294R | +0.346R |
| 113 | +0.112R | +0.274R | +0.327R |

Book-day R std is 0.7153 (n=113), which converts Sharpe to R/day.

**Kill rule:** excess < +0.139 R/day ⇒ **STOP, do not run Stage 2.** No trial
family can rescue a target that misses the 1-trial floor.

**This is a necessary condition, not a sufficient one.** Clearing the most
generous bar means the gate is *reachable*, never that it is cleared.

**The unit is load-bearing.** Stage 1 reports a *book-day overlay excess*, not an
alert-level avg_r gap. The 08-08 headline (+0.18R) is alert-level and is **not
comparable** to this bar. Substituting one for the other is the H15 `bar`-units
trap: a bare number that looks portable and silently changes meaning with the
panel.

## Population

`signal_alert_outcomes` where `outcome_r is not null` — 4,674 rows, 113 book-days,
2026-03-25 → 2026-08-11 (140 calendar days, 80.7% covered).

## Observation unit

**BOOK-DAY**: the mean `outcome_r` of alerts fired that UTC day. Days with no
alerts are absent from the index, not zero.

Book-day aggregation **is** the correlation correction — several strategies fire
on the same symbol/candle, so a t over pooled alerts is inflated. Per CLAUDE.md,
book-day rows must **not** then be deflated again by
`effective_independent_series`; that is the documented double-deflation error.

Re-aggregating to alert level to buy statistical power is **forbidden by this
pre-registration**. It would manufacture n by re-introducing exactly the
correlation the aggregation removes.

## Causality — the defect this design exists to avoid

The fit at day `d` uses only alerts whose outcome was **known** at `d`:

```text
outcome_filled_at_ms < start_of_day_utc(d)
```

**Not** `fired_at_ms < start_of_day_utc(d)`, which is what the 08-08 half-split
used. Resolution lag is median 23.1h, p90 45.7h, max 310.4h, so a fired-time
filter leaks up to ~13 days of future outcomes into the buckets that then grade
them.

The guard is proved by coverage, not asserted: the run counts the
fired-before/filled-after rows it excludes and prints the total, so a vacuous
filter would show up as a zero.

## Construction

For each book-day `d` past warm-up:

1. Window = resolved-by-`d` alerts (expanding, or trailing 45d — a declared variant).
2. Per axis, favourable buckets = those whose window mean beats the window mean,
   among buckets with n ≥ 30 in the window.
3. Score each alert fired on `d`: 0–5, the count of axes it ticks.
4. Weight by a sizing map, then book-day return = weighted mean of that day's
   `outcome_r`.

Axes, unchanged from 08-08: `direction`, `tf`, `strategy`,
`session` (UTC hour // 6), `dow`.

Overlay excess: `e_d = weighted_mean_d − equal_weighted_mean_d`. Natively
positive when the score helps, so **no `abs()` fold** is applied or needed — every
gate leg runs in its native direction. Folding here would certify "the magnitude
is credible", which is not the claim.

**The weighted mean is NORMALISED (`Σwr / Σw`), and that choice is load-bearing.**
It holds gross exposure constant and reallocates within the day, so the excess
isolates *selection*. The unnormalised alternative (`Σwr / n`, i.e. take less risk
on low-score days) is **confounded on this book**: the baseline is net negative at
−0.083 R/day, so simply trading less mechanically improves the mean without any
selection skill at all. The unnormalised series is reported as a sensitivity and
must never be quoted as the headline. Days where `Σw = 0` are flat — book return
0.0, excess `−equal_mean_d`.

## Declared variant family — counted, not free

`direction` in/out × `strategy` in/out × window {expanding, trailing-45d} ×
sizing {step, linear} = **16 cells**.

Declaring these before the run is what keeps the DSR honest. Discovering them
during the run is how a trial family gets understated. **Any variant added later
is an amendment and raises the trial count.**

- `direction` in/out is the live test of the standing reservation that
  `direction = short` is regime-contingent. The tripwire flipped `trend` → `range`
  on 2026-08-10, so this contrast is measurable now rather than hypothetical. A
  rolling refit is itself the structural answer: a trailing window stops marking
  short favourable once short stops paying.
- `strategy` in/out is the 08-08 control against merely re-discovering star ratings.

## Sizing maps

| map | weight |
| --- | --- |
| `step` | `1` if `k/n_axes ≥ 0.8` else `0` — exactly k≥4 at five axes, the 08-08 headline |
| `linear` | `k / n_axes` |

The fractional form is how the same rule applies when a variant drops an axis; at
five axes it is identical to the pre-registered `k≥4` and `k/5`.

**Sizing, never suppression.** Per `golden-signal-feedback-loop`, live outcomes
never feed stars, alerts or `min_avg_r`. A score fitted on live outcomes that
suppressed future alerts would close a deliberately one-way loop and permanently
bias the ledger against re-evaluation. The overlay is a weighting of an
already-complete ledger; nothing here touches the alert path.

## Warm-up

First 45 days of the ledger are fit-only and never scored, leaving 74 scored
book-days — the n the power table above is indexed on.

## Stage 2, only if the kill rule passes

`weekend_gate.py`'s structure: DSR (against the 16-cell family), CSCV PBO, block
bootstrap on `e_d`, `analytics.research_guards.passes_gate`. Reports the
**DSR-vs-trial-family sensitivity curve**, because at this sample size the verdict
is largely a function of assumed search size — the finding `weekend_gate.py`
already recorded when its own DSR ran 0.9x at 1 trial against 0.67 at 127.

**Target cell:** all five axes, `step` sizing, expanding window — the direct
analogue of the 08-08 construction, named here so the target is pre-registered
rather than picked after seeing the table.

## Amendment log

**2026-08-11, before the run:** added the normalised-vs-unnormalised paragraph
above. The original wording said only "weighted mean", which is ambiguous between
two books that answer different questions, and on a net-negative baseline the
unnormalised one is confounded. No result had been produced when this was written.

No amendments after the first run.
