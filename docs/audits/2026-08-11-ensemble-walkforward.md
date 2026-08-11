# Ensemble / confluence score — walk-forward VERDICT

**Date:** 2026-08-11.
**Spec (pre-registered):** `docs/superpowers/specs/2026-08-11-ensemble-walkforward-design.md`
**Drivers:** `docs/plans/scratch/ensemble_walkforward.py` (Stage 1),
`docs/plans/scratch/ensemble_walkforward_gate.py` (Stage 2).
**Supersedes as the live claim:** the 2026-08-08 half-split in
`project_ensemble_confluence_scoring.md`.

## Verdict: FAILS the three-leg gate — do NOT wire it into sizing

`passes_gate(DSR, PBO, boot_lo) -> False`.

| leg | value | bar | result |
| --- | --- | --- | --- |
| **DSR** (family = 16) | **0.7030** | ≥ 0.95 | **FAIL** |
| **PBO** | 0.2354 | ≤ 0.50 | PASS |
| **boot_lo** (mean) | +0.0529 | > 0 | PASS |

Target `dir/strat/expa/step`, n_obs **74 book-days**, mean excess **+0.1546 R/day**,
Sharpe +0.2540. CI95 mean [+0.0529, +0.2558]; CI95 Sharpe [+0.0899, +0.4520].

Two of three legs pass, so this is **not** a null — it is an effect that cannot be
separated from the best of sixteen searched constructions, and that is disqualifying
on its own terms.

## The number that decides it

| cell | mean excess |
| --- | --- |
| `dir/strat/expa/step` (target) | **+0.1546 R/day** |
| `dir/strat/trai/step` (its twin) | **−0.1723 R/day** |

**Same axes, same sizing map. Only the window differs.** A 0.33 R/day sign flip on a
single construction choice nobody had a prior about. The effect is a property of
fitting on an expanding window — i.e. of the early ledger — not a property of the
score. The trailing-window cell is the more realistic "what you would actually have
fitted on the day", and it is the single **worst** of the sixteen.

## Only one cell of sixteen clears even the generous floor

The Stage 1 kill bar (+0.139 R/day) is the 1-trial DSR floor, deliberately the most
generous bar anyone could claim. It was written to kill cheaply, not to certify.

| cell | excess R/day | vs bar |
| --- | --- | --- |
| dir/strat/expa/step | +0.1546 | PASS |
| dir/no-strat/expa/step | +0.1118 | fail |
| no-dir/no-strat/expa/step | +0.0499 | fail |
| no-dir/strat/expa/step | +0.0348 | fail |
| *(9 cells between −0.05 and +0.03)* | | fail |
| no-dir/strat/trai/step | −0.0531 | fail |
| dir/strat/trai/step | −0.1723 | fail |

At the **16-trial family actually declared**, the required excess is ≈ +0.32 R/day.
The best cell reaches less than half of it.

## The verdict is a function of assumed search size, and that is the honest part

| assumed trials | DSR |
| --- | --- |
| 1 | **0.9853** |
| 4 | 0.8881 |
| **16 (declared, primary)** | **0.7030** |

Had exactly this one construction been pre-registered and nothing else tried, it
would have cleared. It was not: the 08-08 work chose five axes by analyst judgement,
and this run declared sixteen cells. Claiming the 1-trial number would be the
`weekend_gate.py` failure mode restated — that audit's DSR ran 0.9x at one trial
against 0.67 at 127, on an axis that is *also* shelved.

The window sign-flip above is independent evidence that the 1-trial reading would
have been luck rather than a pre-registration.

## What the causal refit did to the 08-08 headline

| | 08-08 half-split (fired-time) | this run (causal rolling) |
| --- | --- | --- |
| top-bucket avg_r | **+0.310** (k=5, n=94) | **+0.0130** (k=5, n=180) |
| top-bucket win rate | 48.9% | 41.1% |
| avg_r monotone in k | dips at k=2 | **No** |
| win rate monotone in k | Yes | Yes (21.9% → 41.1%) |

**The top-bucket effect fell by ~96% once look-ahead was removed and the fit was
rolled.** The 08-08 script split on `fired_at_ms`, which admits alerts that had fired
but not yet resolved — with median resolution lag 23.1h and a 310.4h tail, up to ~13
days of future outcomes were leaking into the buckets that then graded them. The
causal filter excluded **1,861 fired-before/filled-after row-days**, so the guard
bites rather than being decorative.

Win-rate monotonicity is the one finding that survives intact. It is also the weaker
claim: win rate ignores payoff size, and this book's expectancy lives in the tail.

## What this does and does not close

**Closed.** The ensemble score does not clear the gate on the current ledger and must
not be wired into `portfolio/sizing.py`. The 08-08 memory's "next step: walk-forward,
then the gate, only then touch sizing" is now run, and it stops at the gate.

**Closed.** `direction = short` was the standing reservation and it is confirmed as
load-bearing in the wrong way: dropping the axis costs the target cell +0.1546 →
+0.0348. A rolling refit did not rescue it, which was the hoped-for structural answer.

**Not closed — the one clean re-entry.** Pre-register *this exact single construction*
now (five axes, `step`, expanding, normalised excess) and re-test it on book-days that
accrue strictly after 2026-08-11. That is a genuine n_trials = 1 test because the
construction is fixed before the data exists, and the ledger grows about one book-day
per day. It costs nothing today. Re-running the same sixteen cells on a longer ledger
would **not** be independent evidence and should not be mistaken for it.

## Method notes worth keeping

- **Normalised, not unnormalised.** The unnormalised overlay reads +0.0897 R/day, and
  it is confounded: on a book with a −0.083 R/day baseline, trading less mechanically
  improves the mean with no selection skill. Never quote it as the headline.
- **Book-day observations, not alerts.** 4,674 alerts over 113 book-days. Aggregating
  is the correlation correction; un-aggregating to buy power would re-introduce
  exactly what the aggregation removes, and the pre-registration forbids it.
- **No `abs()` fold, by construction.** The excess series is natively positive when
  the score helps, so every leg runs in its native direction — the H8/H14 directional
  trap is avoided rather than patched.
- **The kill bar was necessary, not sufficient.** Stage 1 "passed" it at +0.1546 and
  the gate still failed. A cheap screen calibrated to the most generous assumption
  earns its keep by killing fast, and must not be read as a pass when it survives.
