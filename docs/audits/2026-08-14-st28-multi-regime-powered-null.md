# ST28 — the powered-null family has a sixth site, in the multi-regime study

**Date:** 2026-08-14.
**Spec corrected:** `docs/superpowers/specs/2026-08-12-multi-regime-validation-design.md` §Kill rule.
**Verdict corrected:** `docs/audits/2026-08-12-multi-regime-validation.md`.
**Code:** `analytics/audit_guard.py::powered_null` (extracted), `tools/multi_regime_study.py` (promoted + fixed).

## Verdict

**A sixth powered-null site exists and is now fixed. Of the three cells filed as
powered nulls, 0 survive the corrected criterion. No verdict direction reverses.**

The family was declared closed at 5/5 on 2026-08-14 (ST27). It was not. The sixth
site was in gitignored scratch code, so no gate, grep or review surface could see
it — it was found by promoting that code into `tools/`, which is the only reason
this was looked at.

## The defect

`docs/plans/scratch/multi_regime_study.py:118-124` decided a powered null like this:

```python
detected = abs(t) >= Z95
if detected:
    ...
elif tf in ("15m", "1h"):
    verdict = "POWERED NULL — no dependence detectable above …"
else:
    verdict = "INSUFFICIENT — …"
```

The label came from **a hardcoded timeframe whitelist plus non-significance**. No
CI is compared to an effect-size floor anywhere in the file.

The spec's prose kill rule says the same thing: `|Δ| < MDE on 15m ⇒ a powered
null`. Since `MDE = (Z95 + Z80) × SE = 2.802 × SE`, that reduces to `|t| < 2.802`.
**It is a significance test wearing a power label.** `MDE` is a function of the
data's own noise, so it can say how large an effect would have been visible and
never whether a small one was ruled out — the exact inversion CLAUDE.md already
carries for the other five sites, arrived at along a sixth route.

## The correction

`analytics.audit_guard.powered_null` — `ci_lo > -bar and ci_hi < bar` — at
`bar = 0.05R` (`DEFAULT_BAR`, ~2.5× round-trip cost). Units are per-trade R, the
same as `indicator_condition`, `state_audit`, `sl_horizon`, `warning_audit` and
`weekly_path`; the spec's own §3 warns against substituting the ensemble spec's
+0.139 R/day bar, confirming the panel's units.

| cell | Δ | SE | 95% CI | half-width vs bar | filed | corrected |
| --- | --- | --- | --- | --- | --- | --- |
| `cvd_divergence`/15m/short | +0.0331 | 0.1805 | [−0.3207, +0.3869] | **7.1×** | powered null | INSUFFICIENT |
| `engulfing`/15m/short | −0.0017 | 0.0544 | [−0.1083, +0.1049] | **2.1×** | powered null | INSUFFICIENT |
| `fib_golden_zone`/15m/long | −0.0819 | 0.0971 | [−0.2722, +0.1084] | **3.8×** | powered null | INSUFFICIENT |
| `eqh_eql`/15m/short | +0.1577 | 0.0676 | [+0.0252, +0.2902] | 2.6× | nominal hit | nominal hit (unchanged) |

**0 of 3 survive** — the H14 signature exactly, where 0 of 10 NO-EDGE cells had a
CI excluding the bar. The result is not sensitive to the bar: `cvd_divergence`
would need `bar > 0.387R`, ~8× round-trip cost, to contain.

## What does not change

- **The headline NO.** It rests on no cell surviving the 4-test Bonferroni
  threshold (|t| ≥ 2.498), and `eqh_eql` at |t| = 2.33 still does not.
- **The exploratory level comparison**, which was always the decisive number: the
  2021–22 15m book reads median −0.0431R against the 2025–26 corpus's −0.0474R,
  so the book is equally unprofitable in a bull leg, a bear leg and now. That is a
  descriptive comparison, not one of the four gated cells, and it is untouched.
- **The three findings that generalise** (one-regime backtest corpus, trial count
  dominating n, the ~5.29× duplication factor). All independent of the criterion.

What weakens is the strength of the cell-level claim: *ruled out* becomes *not
ruled out*. The two point at opposite next actions, which is why it matters even
with the direction intact — regime dependence up to ±0.11R–±0.39R remains
untested on the panel the study called its best-powered.

## Two things that generalise

**1. The sixth site looked different again, as predicted.** Four sites inferred
power from a sample-size floor, the fifth from failure-to-clear-a-threshold, this
one from non-significance against a noise-derived MDE. A grep for `min_n`, and a
grep for ST27's pattern, would both have missed it. **Stop looking for the
pattern and look for the claim**: any place that emits "no effect" is a candidate,
whatever arithmetic produced it. The criterion now lives in exactly one function,
so a seventh site has to be a *new* refusal to call it rather than a new way to
spell it.

**2. The spec and the code disagreed, and neither noticed.** The spec detects at
`|Δ| ≥ MDE` (|t| ≥ 2.802); the script detected at `|t| ≥ Z95` (1.96). Under its
own spec, `eqh_eql` — the cell the verdict reported as a nominal hit and built a
re-entry recommendation on — would have been a *powered null* and no hit would
have existed at all. The filed table follows the code. This is the H8
missing-gate-leg class in reverse: not a pre-registered leg the code never
implemented, but a pre-registered leg the code implemented *differently*, which
no gate can catch because both halves are internally consistent. Only reading the
spec against the implementation finds it.

## Method note

The study **cannot be re-run to confirm this.** Both regime legs were generated by
re-running backtests over 2021/2022, and `/db-update` has since overwritten
`backtest_trades` back to 2025-09-12 → 2026-08-13 — **0 rows in both windows**.
The correction is therefore arithmetic on the filed Δ and SE, which is sufficient:
the CI is a deterministic function of the two, and both are published.

The scratch original printed `eligible: 0` and exited 0 in that state — an empty
population presenting as a null result. `tools/multi_regime_study.py::load_legs`
now raises and names the missing legs.

## Gates

`lint-py` · `typecheck` · `test` green. `test-regression` **not run and not
required** — the diff is `analytics/audit_guard.py`, `analytics/sl_horizon.py`
(one docstring reference), `tools/`, `tests/` and docs, none of which is in the
backtest trigger set.

All three fixes were **mutation-tested** — revert each, its own test fails:
the CI criterion in `score_cell` (2 tests), the empty-leg guard in `load_legs`
(1 test), and the extracted `powered_null` (10 tests across two files, including
the pre-existing `test_audit_guard.py`).
