# ST26 — H8 / H9 / H10 re-run under the corrected powered-null criterion (2026-08-13)

**Verdict: every negative claim in all three audits was overstated. Of 170 filed
NO-EDGE / COSMETIC cells, 41 survive. 126 move on the criterion alone.**

PR #617 replaced the `n >= min_n` proxy for statistical power with CI containment
(`audit_guard.CellVerdict.powered_null`: `ci_lo > -bar and ci_hi < bar`). H14 and H15
were re-run in that PR; H8, H9 and H10 were repointed in code but never re-run, so
their filed verdicts still described the old criterion. This is that re-run.

## Method — the criterion is isolated from accrued data

A straight diff against the July tables would confound the fix with a month of new
observations, and both effects are present: live alerts accrue continuously, and the
2026-08-13 `/db-update` heal re-ran every backtest, so `backtest_trades` is not the
fixed corpus it is sometimes assumed to be (H8's 4h AVOID count moved 11 → 13 on data
alone).

So every "moved" figure below is computed by re-deriving **both** labels from the same
re-run row: the old label from its own `n`, the new one from its own CI. Identical data,
one variable. Counts that mix in data drift are marked as such.

Runs used a snapshot copy of `analytics.db` (`DEFAULT_DB_PATH` is relative, so a scratch
cwd isolates a long read from the 15-minute signal-watch writer).

## Results

| panel | filed negative claims | survive | moved by criterion | median CI width |
| --- | --- | --- | --- | --- |
| H8 1d (54 cells) | 47 NO-EDGE | **5** | **42** | **13.6× bar** |
| H8 4h (54 cells) | 31 NO-EDGE | 7 | 22 | 5.2× bar |
| H8 1h (54 cells) | 29 NO-EDGE | 15 | 16 | 2.6× bar |
| H8 live (50 cells) | 36 NO-EDGE | 10 | 24 | 4.9× bar |
| H9 (24 cells) | 12 COSMETIC | **0** | 11 | 3.9× bar |
| H10 (15 cells) | 15 NO-EDGE | 4 | 11 | 1.6× bar |

**Positive and actionable labels are unaffected, as pre-registered.** H8's AVOID and
BUILD counts are identical to filed on 1d (1 AVOID / 6 BUILD) and 1h AVOID (13, 8
long-side); H9's `w6_consecutive` short REVERSE stands; no cell was promoted by the
criterion. The three counts that did move — H8 4h AVOID 11 → 13, H8 1h BUILD 12 → 10,
H8 live AVOID 10 → 11, H9 one COSMETIC → SUPPRESS-CANDIDATE — are all data, not
criterion.

### H9 — nothing survives

All 12 COSMETIC cells are gone: 11 to the criterion, 1 promoted by new data. COSMETIC
is a positive claim that a warning does not matter, and not one of the twelve had a CI
that ruled an effect out. The widest ran **9.9× the bar**; the narrowest, `w2_equal_levels`
short at CI [+0.019, +0.062], misses containment only because its upper edge sits 0.012
above a 0.05 bar.

### H10 — 4 survive, and it is the only audit where any do

Universe h24/h48, majors h120 and BTC h120 have CIs genuinely inside ±0.05. The SoT's
guess that H10 "may survive intact" is wrong (11 of 15 move), but its reasoning was
half-right: H10 is the one audit whose fixture was genuinely powered, and it is the one
audit with survivors.

**The sharpest single finding in ST26 is H10's `h96`.** Filed as NO-EDGE, it carries
Holm p = **0.000**, CI **[0.032, 0.082]** entirely above zero, DSR **0.972** and PBO
**0.013** — it clears all three legs of the published research gate. The old criterion
did not merely over-claim absence on noisy cells; it stamped "no effect worth acting on"
onto the family's strongest positive result. Its corrected label is INSUFFICIENT for a
reason worth stating precisely: the CI straddles the 0.05 actionability bar, so the
effect is real and its size relative to the bar is unresolved. "We ruled out an effect"
and "there is an effect we cannot size" point at opposite next actions.

### H8 — the reassurance was the defect

The filed H8 text justifies itself in these words:

> `INSUFFICIENT = 0` everywhere: at `min_n = 30` every pooled cell is powered, so
> defect 2's fix changes no cell in this table. It was still worth fixing (it governs
> future runs at tighter `min_n`), and saying so is more honest than implying all four
> fixes moved the result.

The conclusion that the fix was inert rests on defining "powered" as `n >= min_n` — the
exact equation the fix exists to break. On the corrected criterion that same table moves
**104 of 143** cells. This is a cleaner specimen than H14's of why the defect class
survives review: the sentence reads as careful self-restraint, so a reviewer scanning
for over-claiming finds its opposite and moves on.

### Per-tier vs pooled is a presentation choice with a statistical consequence

Run pooled across timeframes, H8's backtest panel moves only 9 of 26 with a median CI of
**1.4× bar**; run per-tier as the filed doc presents it, 1d alone moves 42 of 47 at
**13.6× bar**. Pooling tightens the intervals enough to make cells look powered. The
filed tier split is the more conservative presentation and should be kept.

## The fifth site — `analytics/sl_horizon.py`: CONFIRMED, not fixed here

ST26 flagged this module and asked for a deliberate decision. It is the same defect
family, and **two of ST26's stated premises about it are wrong**:

1. **The scoped-out reason does not hold.** ST26 deferred it partly because
   `CONFIRMED-BAD` "additionally needs the `abs()` fold per CLAUDE.md's directional
   rule". `CONFIRMED-BAD` is emitted only at `sl_horizon.py:452`, the no-candidates
   branch, where `dsr=None` and `pbo=None`. No DSR is computed there, so the directional
   rule never bites.
2. **There is a second mislabel site ST26 did not name.** At `sl_horizon.py:500`, an arm
   that *did* clear the bar but failed the DSR/PBO gates is labelled `NO-DIFFERENCE`.
   "The lift is real but I cannot rule out overfitting" is not "no difference", and the
   `reasons` string states the truth accurately while the decision field contradicts it.

Exposure is real and is the strongest form of the defect in the repo:
`docs/audits/2026-07-21-st9-sl-horizon.md` carries **29 negative-claim verdicts (23
CONFIRMED-BAD + 6 NO-DIFFERENCE) reported with no confidence interval at all** — the
CI/DSR/PBO columns are em-dashes.

**Decision: fix it, but not as part of ST26.** The predicate does not transfer
unchanged — this is a best-of-k sweep selection, so the honest test is one-sided
(`all arms' ci_hi < bar` licenses "no arm beats baseline"), not the two-sided
containment used for a cell verdict. Fixing it means a code change to a production
module plus re-running and relabelling ST9's 29 verdicts, which is a second
ST26-sized task rather than a footnote to this one. Filed as its own SoT row.

## What changes

Nothing in production. All three tools were already repointed by #617; this run
establishes what their filed documents should say. The three audit docs have their
PENDING RE-RUN banners replaced with these results.

**No verdict direction reverses.** H8's 25 backtest AVOID cells, H9's REVERSE and H10's
positive-direction findings all stand. What changes is the strength of every claim that
no effect exists — those are now INSUFFICIENT, meaning untested, not tested-and-clear.
