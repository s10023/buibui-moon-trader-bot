# ST134 — pre-registration: trial independence and observation clustering in the sweep commit gate

**Date:** 2026-09-10 · **Status:** pre-registered, unrun · **SoT row:** ST134
**Follows:** ST128 (`docs/superpowers/specs/2026-09-10-st128-wfo-resweep-preregistration.md`)

The ST128 re-sweep ran and wrote nothing: **273 cells, 0 UPDATE, 0 KEEP, 273 SKIP**, with an
attribution control under the defective cost book moving **0 cells**. Its Decision Log named that
outcome as the observable reversing "reuse `/wfo-sweep` Step 4", and it fired. The open question
moved to the gate, and its own §9 says a revised rule needs its own pre-registration. This is it.

⚠ **This file is written AFTER seeing a result its author dislikes.** That is the shape memory
`feedback_post_hoc_robustness_conditioned_on_answer.md` records — you only bound a result you
dislike, so pessimistic rules get audited and optimistic ones ship. §7 states the disclosure in
full and §4 states the falsification that has to survive it. Nothing here may be justified by how
many cells it admits.

## 1. What is being corrected

Two counting defects in `analytics/sweep_guard.py`, one permissive in effect and one restrictive.
**Both are pre-registered together, and reporting one without the other is out of contract.**

| # | Defect | Direction of the correction |
| --- | --- | --- |
| a | The trial family counts correlated arms as independent searches | **loosens** the gate |
| b | The observation count is not day-clustered | **tightens** the gate |

### 1a. The trial family

`evaluate_commit_gate` deflates against `n_trials = max(n_grid, n_trials)` — the raw grid size.
DSR's benchmark is the expected maximum of `N` trials drawn from a null of variance `V[SR]`, and
it grows like `sqrt(2 ln N)`.

The arms of this grid are `tp_r` values. **They share their entries and their stops and differ
only in where the target sits** — nine re-labellings of one trade population, not nine searches.
DSR was built for a family of ostensibly-equivalent candidate strategies; a monotone parameter
family is a different object, and counting its arms as independent overstates the selection
benchmark.

This repo already holds the correction and applies it to the *symbol* axis, never the *trial*
axis: `analytics.forecast.effective_independent_series`, `n_eff = k / (1 + (k−1)·ρ)`. At the
filed `ρ = 0.315`, `k = 25` it returns 2.92, which is why a naive t over 41,571 pooled
symbol-days reads 2.21 on a cell whose corrected value is +0.757.

Magnitude, holding `V[SR]` fixed (it is the empirical variance of the trial Sharpes and does not
move when `N` is re-counted, so these are exact):

| N | benchmark `SR₀ / sqrt(V[SR])` | vs N = 9 |
| ---: | ---: | ---: |
| 9 | 1.5212 | — |
| 3 | 0.8529 | **−43.9%** |
| 2 | 0.5199 | **−65.8%** |

⚠ **Amendment (2026-09-11, ST134 I7):** at `k = 9`, `n_trials_eff = 9/(1 + 8ρ)` drops
below the pre-registered floor `MIN_EFFECTIVE_TRIALS = 2.0` at `ρ ≈ 0.4375` — BELOW
§4a's own correlation bar `ρ* = 0.5`. So any family that clears §4a's PROCEED bar
(CI lower bound `> 0.5`) has, by construction, already floor-bound to `N = 2`, the
**−65.8%** row above — never the graduated `N = 3` row the table otherwise seems to
offer. The kill-switch's ρ measurement cannot discriminate "correlated enough to
license the correction" from "correlated enough to license the MAXIMUM correction
this family's own grid size permits" — a PROCEED licenses the bottom row of this
table, not a point somewhere inside it. This does not change the decision rule in
§6 or the table above; it is a correction to what a PROCEED verdict, once reached,
turns out to mean for a 9-arm grid specifically (§1d's 99-arm flat-SL grids floor at
a correspondingly lower ρ still).

### 1b. The observation count

`n_obs` enters DSR as `sqrt(n − 1)`. `sweep_guard` counts raw trades and never clusters them —
**the exact blind spot `audit_guard` carried until 2026-08-25 (ST80)**, on a path that never got
the fix. `AuditCell.cluster_key` is required and fails closed; `sweep_guard` has no equivalent.

Measured there on 125 cells / 166,384 distinct trades: trade-weighted DEFF **4.991**, median
**1.670**, the deflation concentrating in the 15m cells that are 64.4% of the live ledger.
Correcting `n_obs → n_obs / DEFF` multiplies the z-statistic by `1/sqrt(DEFF)` — **≈ 0.774 at the
median, ≈ 0.448 trade-weighted.**

⚠ **The two corrections are comparable in magnitude and opposite in sign.** With only (a), several
cells would be expected to clear. With both, the net is genuinely unknown in advance. That is the
argument for running the measurement rather than reasoning about it, and it is also why (a) alone
would not have been an honest deliverable.

### 1c. Not the same estimator applied twice

`AGENTS.md` says day-clustering and `effective_independent_series` are one phenomenon with two
estimators, and that one or the other applies in any one place — never both. **That prohibition is
about one axis, and these are two.** (a) corrects the count of *searches* along the trial axis;
(b) corrects the count of *observations* within the chosen arm. Applying only one leaves the other
denominator wrong. The spec states this because the pairing superficially resembles the thing the
rule forbids, and a reader should not have to re-derive that it does not.

### 1d. Family-size asymmetry, handled by the same path

`param_sweep.py:454-460` drops the `sl_pct` axis when a strategy emits structural stops, so
**a structural-stop detector faces a 9-trial family and a flat-SL detector a 99-trial one** — the
same 0.95 bar across families an order of magnitude apart, for a reason unrelated to their edge.
No special case is added: `n_trials_eff` measures each family's own correlation structure and the
asymmetry is absorbed rather than patched.

### 1e. Restated thresholds — a three-link chain

`AGENTS.md`: the gate is one function, `analytics.research_guards.passes_gate`, and all coded
verdicts delegate to it. Three sites do not:

| Site | What it holds |
| --- | --- |
| `analytics/sweep_guard.py:34-35` | its own `DSR_THRESHOLD = 0.95`, `PBO_THRESHOLD = 0.5` |
| `analytics/recalibrate_lib.py:19,26` | its own threshold under a comment saying it *"matches the sweep commit-gate threshold in `analytics/sweep_guard.py`"* |
| — | which in turn claims to match the published gate |

Three links, none calling `passes_gate`, all agreeing **by coincidence**. That is the condition
`AGENTS.md` names as indistinguishable from agreement by construction, and it is why
`carry_gate_verdict` kept an inline restatement until 2026-08-19 with no test able to fail.
The two shared thresholds are made to delegate.

⚠ **The third leg is deliberately NOT touched.** `sweep_guard`'s is `n_obs ≥ MinTRL`; the
published gate's is `boot_lo > 0`. Aligning them would change nothing *observed* — MinTRL bound
on none of the 82 scoreable cells — but swapping a leg is a bar change in principle, not a
tidy-up, and this file's whole claim is that nothing was loosened. It is filed as its own row.

## 2. The correction — pre-committed computation

**`n_trials_eff`.** `_build_perf_matrix` already produces the `(n_bins, n_trials)` binned
return matrix for PBO, so ρ costs no extra backtests. Pass its columns to
`effective_independent_series` as `{arm_label: binned_series}`; take `n_eff`; feed it to
`deflated_sharpe_ratio` in place of `max(n_grid, n_trials)`. Bounds, both required:

- **Floor at 2.** Below two trials deflation is undefined and the existing `n_trials < 2`
  INSUFFICIENT guard already refuses; `n_trials_eff` must not route around it.
- **Ceiling at the raw count.** A correction may only ever reduce the family. If ρ measures
  negative, `n_eff` is clamped to `k`, never allowed above it.

**`n_obs_eff`.** `analytics.research_guards.cluster.cluster_stats` on `utc_day_keys`, the unit
`AGENTS.md` documents as a lower bound on a 24/7 tape (DEFF rises 1.508 at 12h → 1.670 at a day →
1.873 at 2d → 2.366 at 1w with no plateau, so a design effect reads as a floor and a surviving
verdict as conservative). `n_obs_eff = n_obs / DEFF`, fed to `deflated_sharpe_ratio`.

**`V[SR]` is left alone.** §1a notes it also absorbs real between-arm effect as if it were
selection noise, which inflates the benchmark further. Correcting it is a third change with a
much weaker mechanism, and leaving it leaves the corrected gate **still conservative** — the
residual error runs in the safe direction. Not in scope.

**The `2 × n_splits` floor applies to `n_obs_eff`, not `n_obs`.** The floor exists so the
statistics are stable, and clustered observations do not stabilise them. ⚠ This is strictly
stricter than today and has a predictable consequence that must be reported rather than
discovered: **cells can move from scoreable to INSUFFICIENT.** At median DEFF 1.670 any cell
holding fewer than ~47 raw trades drops below the 28-trade floor.

## 3. The reachable population is 82 of 273 cells, not 273

Neither correction touches an `INSUFFICIENT` verdict, and 191 of 273 cells are one:

| Reason | Cells | Reachable by this correction? |
| --- | ---: | --- |
| no non-overfit config to evaluate | 111 | no |
| too few trades (< 2× n_splits) | 56 | no — and (b) can only add to this |
| no signals detected | 24 | no |
| **scoreable** | **82** | yes |

**So the ceiling on this whole exercise is 82 cells (30%), and (b) lowers it.** Any report
implying otherwise is wrong. The 111 that hold no non-overfit config are refused by the OOS
filter before the gate is reached, and that filter is not in scope here.

## 4. Falsification — run BEFORE any of the 273 cells

Both kill-switches are settled before a single corrected cell verdict is read. Either one firing
ends the exercise, and the result is reported as a finding rather than retried.

### 4a. Correlation floor — three outcomes, not two

Measure ρ across arms on all 273 cells and report the distribution **first**, with a bootstrap CI
on the median. ⛔ **A threshold comparison is not a verdict here.** A sample-size floor, an MDE, a
p-value or a failure to clear a bar are none of them power — that is the family `AGENTS.md`
records at six sites, each spelling the arithmetic differently. So the read is CI containment
against the pre-committed bar `ρ* = 0.5`, and it has **three** outcomes:

| Reading | Verdict | Action |
| --- | --- | --- |
| CI lower bound **> 0.5** | the arms are established as correlated enough to license §1a | proceed to §4b |
| CI upper bound **< 0.5** | established: the arms carry too little dependence for the correction | **stop.** File as *"the correction is not licensed"* |
| CI **straddles 0.5** | INSUFFICIENT — untested, not cleared | **stop.** File as underpowered, and say what n it would take |

⛔ **In neither stopping case is "the gate was right" an available conclusion.** Failing to
license a correction says nothing about whether the gate's refusals are correct; those are
different claims and only the first is on the table. ST133 then proceeds on the existing evidence
and no re-run happens.

⚠ ρ is estimated over `n_bins = 2 × n_splits = 28` bins, so a per-cell ρ is noisy by
construction. The decision statistic is the **distribution across 273 cells**, never any one
cell's point estimate.

### 4b. Null calibration

A gate that admits a known-null family is too permissive whatever it does to real cells. This is
the check that makes §7's disclosure survivable, so it is not optional.

**Construction.** Draw a shared random sign vector over the time bins and apply it to *every*
arm's trades in that bin. This preserves the cross-arm correlation structure exactly — all arms
flip together — while setting the expected return to zero. The family is then a null with this
family's own dependence structure, which is what the correction claims to be pricing.

**Scope.** The control targets the **DSR leg specifically** — the leg being corrected and the leg
that binds on all 82 scoreable cells — holding PBO and MinTRL at their observed values. 200
replicates over a stratified sample of 30 cells. PBO's CSCV is `C(14,7) = 3,432` splits per
evaluation and running it inside the control would cost more than the whole re-run for a leg that
is not being changed.

**Pre-committed thresholds.**

- The corrected gate must pass **≤ 10%** of null replicates. Above that it is abandoned.
- The **uncorrected** gate is run on the same nulls and reported beside it. This is not a
  formality: if today's gate passes far below the nominal 5%, that is direct evidence it is
  over-conservative, established without reference to any real cell — the strongest form this
  argument can take, and the one least contaminated by §7.

## 5. What must be reported — effect size, not a pass count

⛔ **"How many cells clear" is not the decision-relevant number and must not be the headline.**
The decision this feeds is whether to move live `tp_r` values, and a pass count cannot support it.

Required, per corrected cell that would move:

1. **OOS `avg_r` delta** between the winner and the current live value — the distribution, not a
   mean alone.
2. **Live exposure**: alerts per week in that cell, from the live ledger, so a large delta on a
   cell that fires twice a year is visible as such.
3. **The 2×2**, every cell scored under all four books: raw / trials-corrected / n-corrected /
   both. Reporting only "both" makes the two corrections unattributable, which is the failure
   ST128 §5 exists to prevent.
4. **Cells moving scoreable → INSUFFICIENT** under (b), counted separately from cells refused on
   a metric.

⚠ **The OOS delta is an optimistic bound and must be labelled one.** The winner is selected on it;
that selection is exactly what DSR deflates for. ST128's write rule already requires a ≥ 0.5 step
or > +0.05R OOS improvement, so any write clears that bar in the *fitted* book by construction and
the live realisation is expected to be smaller.

⚠ **Neither correction changes any book.** No `avg_r` and no `win_rate` improves because of this
work. The gate is a refusal rule; the only channel to P&L is a `tp_r` that the operator then
chooses to write. And `win_rate` is a dial rather than a goal — `tp_r` trades win rate against
payoff per win, so a rule that raised it would establish nothing.

## 6. Pre-committed decision rule

1. Measure ρ on all 273 cells. §4a returns anything but *"CI lower bound > 0.5"* ⇒ **stop, report
   which of the two non-proceeding readings it was, route to ST133.**
2. Run the null calibration. §4b threshold fails ⇒ **stop, report, route to ST133.**
3. Only then re-run the 273 cells under the 2×2 of §5.
4. A cell is committable only under the **unchanged** bar: `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n_obs_eff ≥
   MinTRL`, with the ST128 §2 pick and write rules applied verbatim on top.
5. **No TOML is written, under any outcome.** Applying stays a separate deliberate act taken with
   the delta in front of the operator — `buibui-signal-watch.service` runs the working tree on a
   15-minute timer, so writing `config/signal_watch*.toml` IS the deployment, with no review
   window between the edit and live alerts.

## 7. Disclosure

**The correlated-arms mechanism was found while looking for why a disliked result came out as it
did.** ST128 returned 273 SKIP; this author went looking for an estimator explanation and found
one. That ordering is stated rather than elided because it is the whole risk in this file.

Three things are offered as the defence, and none of them is a cell count:

- The mechanism is **structural and checkable in advance** — the arms share entries and stops by
  construction, which is true of this grid whatever any cell returns. §4a turns it into a
  measurement whose bar, and whose three readings, are fixed before the measurement.
- The **restrictive twin is pre-registered with it.** §1b tightens the gate and was identified in
  the same pass; shipping (a) alone would have been the post-hoc move, and the operator was asked
  and kept both.
- The **null calibration** (§4b) tests the corrected gate against a family whose answer is known,
  so the permissiveness question is settled without reference to any real cell.

⚠ **Also disclosed: the ST128 run's machine-readable evidence is gone.** Its reproduce block
writes to `/tmp/c.json` and `/tmp/d.json`, and neither survives. Its "uniform 9 trials" line could
therefore not be verified from artifacts, and it is inconsistent with `_default_param_ranges`
returning a 99-combo grid for any flat-SL detector — it implies every one of the 82 scoreable
cells was structural-stop, which is plausible and unestablished. **This run writes its JSON under
`docs/plans/scratch/`, which the backup glob covers.**

## 8. What this does NOT decide

- **It does not write any TOML.** §6.5.
- **It does not touch the third leg.** §1e.
- **It does not touch the OOS filter** that refuses 111 of the 191 INSUFFICIENT cells.
- **It does not re-open any filed sleeve verdict.** Mapped rather than recalled: `sweep_guard` has
  exactly one production consumer, `param_sweep.py`. Every sleeve verdict (xsmom, combine, carry,
  cvd, wick_anchor, ST104) goes through `passes_gate`, which this file does not change.
- **It does not decide ST133** — the 168 of 273 cells carrying a `tp_r` that fails today's OOS
  filter. That sequences after this.
- **It does not settle `live_parity`**, still unpassed and deliberately unfixed on ST128.

⚠ **A LEAD, not a finding, and it needs its own row:** the §1a argument travels to any filed
result that deflated by a raw trial count over a *monotone arm family* — ST104's four arms,
ST9/H11's six-arm ATR-widening sweep, `wick_anchor`'s two. **The direction matters and it is
uncomfortable:** correcting them makes those gates *more permissive*, pushing CONFIRMED-BAD toward
less-certain, which cuts against ST57's ruling that the ATR verdict is conservative and stands
strengthened. Enumerating them is out of scope here; it is named so it is not discovered a third
time.

## 9. Sequencing

ST128 §5's chain is unchanged and this file inserts one step ahead of its step 3:

1. ST128 pre-registration landed; corrected sweep ran; delta reported. **Done, no writes.**
2. **This pre-registration lands.** *(this branch)*
3. §4 kill-switches run. §6 re-run only if both hold. **No writes.**
4. Operator rules on applying. If yes, TOMLs move in a change of their own.
5. Only then the production backtest, and only then ST122's recalibrate.

⚠ **Steps 4 and 5 must not share a change.** If the TOMLs move and a recalibrate runs together,
neither movement is attributable. `config/*.toml` is inside CI's regression paths filter, so a
`tp_r` write also requires `make test-regression` and will likely move goldens — a decision of its
own, not a step that follows.

## Decision Log

| Decision | Observable that REVERSES it |
| --- | --- |
| Correct the trial count by effective independence | A bootstrap CI on median arm ρ whose UPPER bound sits below 0.5 — the arms would then be established as carrying too little dependence for the mechanism to describe this family. A CI straddling 0.5 reverses nothing; it reports the measurement as underpowered |
| Keep the 0.95 / 0.5 bar rather than lowering it | The null calibration showing the bar mis-calibrated at the corrected trial count — a corrected gate passing > 10% of nulls, or an uncorrected one passing far below 5% |
| Pre-register the restrictive twin (§1b) alongside the permissive one | A measurement showing `utc_day_keys` DEFF ≈ 1.0 on sweep cells — clustering would then be a no-op here and (b) would be ceremony |
| `V[SR]` left uncorrected | Evidence that the between-arm Sharpe spread is dominated by real `tp_r` effect rather than noise — the benchmark would then be inflated by a second route and leaving it would stop being merely conservative |
| The `2 × n_splits` floor applies to `n_obs_eff` | Cells moving scoreable → INSUFFICIENT in numbers that make the gate uninformative rather than stricter — a floor nothing clears is the H8 unreachable class |
| Third leg (`MinTRL` vs `boot_lo`) left alone | MinTRL becoming the deciding leg on any cell — it bound on none of the 82, which is the only reason leaving it is free |
| No TOML written under any outcome | The signal-watch timer ceasing to run the working tree — the hazard is entirely a consequence of that coupling |
