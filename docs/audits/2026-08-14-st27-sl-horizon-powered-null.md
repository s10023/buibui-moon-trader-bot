# ST27 — `sl_horizon.py` re-run under a one-sided powered-null criterion (2026-08-14)

**Verdict: 22 of 30 negative claims in the ST9 audit were unsupported. 8 survive.
No verdict direction reverses, and the ST9 disposition is unchanged.**

`analytics/sl_horizon.py` was the fifth site in the powered-null defect family that
PR #617 fixed in four other modules and ST26 re-ran. It was confirmed and deliberately
deferred there because the predicate does not transfer unchanged. This is that fix plus
the re-run it required.

## The two defect sites

**(1) The no-candidates branch.** `CONFIRMED-BAD` / `NO-DIFFERENCE` were emitted whenever
no arm's paired-lift CI cleared `+bar` — absence of evidence read as evidence of absence.
Both are positive claims that no ATR-scaled stop rescues the cell. The branch reported
`ci_lo = ci_hi = None`, which is why all 29 filed negative verdicts carry em-dashes in
the CI columns: no reader could check them.

**(2) The gates branch.** An arm that *did* clear `+bar` with a Holm-significant CI but
failed DSR/PBO was labelled `NO-DIFFERENCE`. "The lift is real but I cannot rule out
overfitting" is not "no difference" — and the `reasons` string stated the truth while the
decision field contradicted it, so the defect was invisible to anyone reading reasons.

Both of ST26's stated premises about this module were wrong and are corrected here:
`CONFIRMED-BAD` never needed the `abs()` fold (it is emitted only where no Sharpe is
computed, so the directional rule never bites), and site (2) was never named.

## Why `powered_null` does not drop in

`audit_guard.CellVerdict.powered_null` is two-sided containment (`ci_lo > -bar and
ci_hi < bar`) because it sizes one cell's effect against the bar in both directions. This
is a **best-of-k arm sweep**, and the only negative claim on offer is one-sided — *no arm
beats baseline*. The honest predicate is therefore `all arms' ci_hi < bar`
(`sl_horizon.negative_claim_licensed`). An arm's lower bound is irrelevant: an arm that
reliably *loses* to baseline is evidence **for** the claim, not against it.

Two consequences worth stating, because both look like omissions:

- **No Holm adjustment is applied to the negative direction, and none is needed.** To
  wrongly assert the claim, the one arm that truly beats the bar must have its own CI
  miss — a single-interval coverage failure bounded at 2.5%, regardless of how many arms
  are swept. Multiplicity binds on the positive (best-of-k) side, which is what DSR/PBO
  already gate.
- **Significance is not required for a negative claim.** Five surviving cells carry
  Holm-adjusted p well above alpha (`engulfing`/15m reads 0.246). Demanding a significant
  effect before concluding no effect exceeds the bar would be backwards; `powered_null` is
  CI-only for the same reason.

## Method — the criterion is isolated from accrued data

ST26's method. A straight diff against the July table would confound the fix with a month
of new observations, and both effects are present: live alerts accrue continuously
(`doji`/15m n moved 63 → 97) and the 2026-08-13 `/db-update` heal re-ran every backtest.

So both labels are re-derived from the **same re-run row** — the old one from that row's
own branch and baseline sign, the new one from its own CI. Identical data, one variable.
The re-run used a snapshot copy of `analytics.db` taken at 10:13 MYT, isolated from the
15-minute signal-watch writer.

The re-run corpus carries 30 negative claims against the filed table's 29; that
difference is data, not criterion.

## Results

| panel | negative claims | survive | moved by criterion | median CI width |
| --- | --- | --- | --- | --- |
| LIVE (the pre-committed gate) | 15 | **5** | **10** | **11.3× bar** |
| Backtest (corroboration) | 15 | 3 | 12 | 0.8× bar |

Every survivor is `CONFIRMED-BAD`; all 4 filed `NO-DIFFERENCE` cells move. Positive and
actionable labels are untouched, as pre-registered: the three 1d `SUSPECT` cells
(`doji` k=1.5, `engulfing` k=1.0, `morning_evening_star` k=1.0) are unchanged.

### The live gate now supports a negative claim only at 15m

All five survivors are 15m, and each is stronger than the label requires: their CIs are
**entirely negative** (`ci_hi` from −0.001 to −0.140), so it is not merely that no arm
beats the flat stop — every arm loses to it. That is the physically expected result and
it corroborates the grid's own design note: at 15m the flat 2% stop is already ~7.5 ATR,
so every arm in the grid is a *tightening*, and tightening reliably hurts.

Every 1h and 4h live negative claim moved, at a median CI width of 11.3× the bar.
`doji`/15m — filed `NO-DIFFERENCE` — has a CI of **[−0.424, +0.563]**, 19.7× the bar. The
live substrate simply has no power at 1h/4h: n runs 30–250 there.

### The three mislabels are the sharpest finding

The defect-(2) cells are the ST9 analogue of H10's `h96`, and they are worse:

| cell | lift CI | DSR | PBO | filed | corrected |
| --- | --- | --- | --- | --- | --- |
| `hammer_hanging_man` 1d | [+0.063, +0.197] | 0.999 | 0.645 | NO-DIFFERENCE | INSUFFICIENT |
| `hammer_hanging_man` 4h | [+0.057, +0.121] | 0.849 | 0.000 | NO-DIFFERENCE | INSUFFICIENT |
| `inside_bar` 1d | [+0.155, +0.260] | 1.000 | 0.744 | NO-DIFFERENCE | INSUFFICIENT |

All three CIs exclude **both zero and the bar** — these are the cells where widening the
stop most clearly helped, and each was stamped "no difference". `hammer_hanging_man`/4h
fails on DSR alone at 0.849 with PBO of exactly 0.000; the other two fail on PBO alone
with DSR ≥ 0.999. "We ruled out an effect" and "there is an effect we cannot yet trust"
point at opposite next actions.

### The backtest survivors are marginal — read them as such

The backtest panel is genuinely well-powered (median CI width 0.8× bar at n up to
116,618), which is why any negative claim survives there at all. But **7 of its 18 cells
sit within 0.01 of the bar on `ci_hi`**, and the split runs straight through that band:

- survive by 0.002–0.007: `inside_bar`/1h (0.043), `morning_evening_star`/1h (0.044),
  `doji`/1h (0.048)
- move by 0.000–0.009: `morning_evening_star`/4h (**0.050**, at the bar to reporting
  precision), `pin_bar`/1h (0.051), `engulfing`/1h (0.055), `hammer_hanging_man`/1h (0.059)

Those three survivors are not robust results. A different bootstrap seed or a bar of 0.06
would flip them. The honest reading of the backtest 1h family is that its arms land *near*
the bar, not below it.

## What this changes for the flat-2% question itself

ST9 is the "is the hard-coded 2% stop wrong, and does another width beat it" audit, so
correcting its negative claims moves that answer. Three filed statements are now wrong:

- **"Every 1h/4h/15m cell is CONFIRMED-BAD on both substrates"** — this was the sentence
  carrying "where there's data, the family is genuinely bad and the 2% is not the cause".
  On the corrected criterion the live gate supports `CONFIRMED-BAD` **at 15m only**. Every
  live 1h and 4h negative claim is INSUFFICIENT, and on the backtest substrate only three
  1h cells survive, all by ≤0.007 of the bar.
- **"ATR-widening is CONFIRMED-BAD at 15m/1h/4h"** (`do_not_relitigate`, "stop width is
  closed") — same correction. The claim now holds at 15m, where it is strong.
- **The tension flagged on 2026-08-07 is resolved.** `flat_sl_pct_defect` recorded a
  monotone per-TF gradient in the live ledger (15m −0.051 → 1d −0.305) as "IN TENSION with
  ST9's own verdict… both cannot be the whole story", and called that the actual
  unexamined question. There is no tension: ST9 never had the power to call 1h or 4h
  either way. The gradient stands unopposed.

⚠ **Read this as "untested", not as "widening works".** The fidelity gate still fails and
these tables remain unaccepted, so the corrected reading is that the 1h/4h door was closed
on evidence that did not exist — not that it should now be walked through. The one genuine
positive signal is that `hammer_hanging_man`/4h shows a bar-clearing backtest lift
(+0.090, CI [+0.057, +0.121], PBO 0.000) that was filed as NO-DIFFERENCE; it fails DSR at
0.849 and so is a candidate for the revisit condition, not an action.

**`sl_pct` remains a first-class swept axis** (`analytics/param_sweep.py`, 0.5%–3.0% by
0.25%, per symbol × tf × strategy, with a commit gate that deflates the pick). Nothing
here argues for building a sweep; ST9 was the read-only diagnostic that precedes one, and
its revisit condition (a) — re-run the production backtest over the full 25-symbol
universe so 1d stored-n grows — is unchanged.

## What does not change

- **The fidelity gate fails again** (backtest 0.962, live 0.990 agreement; worst avg_r
  delta 0.251 / 0.233), so under the pre-committed protocol these tables remain **not
  accepted**, exactly as in July. No threshold was relaxed.
- **The ST9 disposition stands**: the 1d graveyard for this family is genuinely suspect
  but not cleared, the family stays demoted rather than revived, and the flat 2% stop is
  not touched.
- **No production behaviour changes.** `sl_horizon.py` is a read-only audit library; no
  detector, star rating, gate or golden is affected.

## What this changes

`docs/audits/2026-07-21-st9-sl-horizon.md` carries a banner pointing here; its tables are
left as the dated record of that run rather than overwritten. Its 29 negative verdicts
should be read as **untested**, not as tested-and-clear — 22 of the 30 equivalent claims
on fresh data do not survive contact with a CI.

The one durable lesson beyond this module: the four earlier sites all inferred power from
a sample-size floor, and this fifth one inferred it from *failure to clear a threshold*.
Those are different mistakes with the same shape — a test that did not fire is not a test
that found nothing — so the family is defined by the shape, and greps for `min_n` would
never have found this one.
