# Spec reconcile — P3 cross-sectional momentum sleeve (the deploy core)

**Date:** 2026-08-06
**Spec:** `docs/superpowers/specs/2026-06-16-p3-cross-sectional-momentum-sleeve-design.md`
**Code:** `analytics/xsmom/`, `tools/xsmom_audit.py`, `tests/xsmom/`
**Method:** every pre-registered claim located in code or recorded absent; guards
mutation-tested rather than read.

## Verdict — the implementation is FAITHFUL; the one defect is in the SPEC

This is the third spec reconciled and the highest-stakes one: xsmom is the only
sleeve carrying real capital (+1.375 Sharpe, DSR 0.997, PBO 0.295). **The
expected failure mode did not occur.** Unlike H8 (a pre-registered MinTRL leg the
code never built) and P2 (two such legs), **every gate leg and construction step
this spec pre-registers is implemented as written.**

The one substantive finding runs the other way: the spec's pre-registered
causality test is **unsatisfiable as literally written**, and the code
implements the correct invariant instead.

**Counter: 6 of 46 spec docs reconciled — and the counter was wrong again, 3 for
3.** #566 set it to "5 of 46" listing **"xsmom"** among the reconciled, while the
same document named `p3-cross-sectional-momentum-sleeve-design.md` as "the
highest-stakes one **still unchecked**". Both cannot be true. That entry most
likely referred to #549's causality-leg fix — which checked **one leg**, not the
spec — but it is unrecoverable, so it is recorded rather than silently
re-counted. The lesson is procedural: **name the FILE, not the sleeve.** "xsmom"
is ambiguous across seven spec docs bearing that name; today's reconcile covers
exactly one of them, the 2026-06-16 sleeve design.

## Finding 1 — the pre-registered causality test cannot be satisfied by correct code

§Causality (called "the package's most important test") specifies:

> perturb a single instrument's close on day `d` and assert **no book return on
> day `< d+1` changes** (RED without the shift, GREEN with it)

"Days `< d+1`" includes day `d`. But the book return on day `d` is
`leverage_d × r_d`, and `r_d = close_d / close_{d-1} − 1` depends on `close_d`
**directly**. Perturbing `close_d` therefore *must* move the day-`d` book return
even when the position is perfectly causal. Measured on a 3-instrument fixture at
`k = 250`, bumping `FLAT`'s close ×1.5:

| quantity | max abs delta | spec/code expectation |
| --- | --- | --- |
| book return, days ≤ k | **0.924** | spec asserts 0 — **fails on correct code** |
| book return, days < k | **0.000** | — |
| leverage, days ≤ k | **0.000** | code asserts 0 — **holds exactly** |

All 0.924 of the discrepancy sits at day `k` itself. **The code asserts on
`leverage[: k+1]`** (`tests/xsmom/test_book.py:98`), i.e. on the *position*,
which is what causality is actually about and which holds to machine zero.

**Disposition: fix the SPEC, not the code** — same as #566's Finding 1. Two
correct restatements exist; the code implements the first and it is the stronger:

1. the **position** held through day `d` is invariant to `close_d`; or
2. book returns **strictly before** day `d` are invariant to `close_d`.

**Severity: no harm occurred, but the trap is live.** Nobody implemented the
spec's wording literally. Had they, the test would have been red against working
code, and the "fix" would have been to remove the day-`d` return's dependence on
`close_d` — i.e. to break the P&L to satisfy a broken test. That is the failure
mode this reconcile series exists to catch, arriving from the opposite direction
to the usual one.

## Finding 2 — criterion 4 was cleared at +0.37, and "near zero" is doing work

§Verdict joins four criteria with **and**: positive Sharpe, cost-robust,
DSR/PBO-survivable, **and** "Diversifying — `corr_to_trend` near zero". The
shipped verdict reports **`corr_to_trend` +0.37** and calls it "diversifies
trend". On a plain reading +0.37 is not "near zero".

**This is defensible in context and is not a gate failure.** The very next
paragraph in the spec states criterion 4's actual purpose: an honest negative
includes "merely a re-labelled trend with **high** `corr_to_trend`". +0.37 is
plainly not high, so the disqualifier the criterion exists to trip did not trip,
and the sleeve cleared the three quantitative legs outright. Recorded because the
pre-registered wording and the accepted value do not match, and because a future
reader comparing the two should not have to re-derive why that was acceptable.

**Recommended: amend the spec** to state criterion 4 as the disqualifier it
actually is (e.g. "not a re-labelled trend — `corr_to_trend` well below the level
at which the two sleeves are the same bet"), rather than a bound nothing enforces.

## Finding 3 — the G3 verdict has no coded expression; a human reads a prose line

The four-criteria verdict is evaluated by an operator reading
`tools/xsmom_audit.py:203`, which prints prose ("G3 read: is the XS sleeve
positive, cost-robust, DSR/PBO-survivable, AND low-correlated to trend…").
The only coded gate in the package is inside `evaluate_xs_capacity`
(`report.py:175`), which is the three-leg `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0`
and correctly documents that the diversification read is irrelevant *there*.

**Not a spec violation** — the spec never asked for a verdict function, and the
sibling `combine/` sleeve grew `combine_gate_verdict` under its own spec. But it
is the same class as card-v3's unenforced live-wins-at-n≥10 rule and the pundit
`n≥30` gate that lives only in a docstring: **a rule a human is asked to apply is
not a gate.** Filed, not fixed.

## What is clean — stated explicitly, because "no drift" is the finding here

Every item below was checked against code, not assumed:

**Construction (§Math, all 7 steps).** Forecast identical to trend
(`combine_forecasts`, capped ±20) · demean over the active set via skipna row
mean · **`.shift(1)` applied after demeaning and before sizing**
(`book.py:85`) · vol-parity `(g/10) × (vol_target / vol_ann)` · honest costs
(gross, `|Δlev| × rate` turnover, `lev × funding` with shorts receiving) ·
**aggregate is the long-short SUM, not an equal-risk mean** (`book.py:167`, the
one step easiest to get silently wrong) · causal 20 %-vol governor clipped
`g_min`–`g_max` with `.shift(1)` trailing vol.

**Dataclasses.** `XSBookResult` carries all six pre-registered fields.
`XSReport` mirrors all twelve `G2Report` fields **plus** `corr_to_trend` and
`trend_sharpe`. `evaluate_xs(result, cfg, trial_returns, trend_returns)` matches
the pre-registered signature.

**The degenerate-safe contract holds.** The spec requires that when
`trend_returns` is empty, `corr_to_trend` and `trend_sharpe` both default to
`0.0` — "never NaN". Verified in both paths: `report.py:120-125` guards
`trend_sharpe`, and `_aligned_corr` returns `0.0` at `n < 2`.

**Multiple-testing family.** `replay_xs_trials` returns exactly the four
single-speed books plus `combined`. DSR deflates against it, and **PBO runs over
the same family with no `pbo_returns` split** — matching the spec's explicit v1
decision (the split was a weight-study artifact, and `evaluate_xs` correctly does
not expose the parameter that `forecast.evaluate` does).

**Cost sensitivity** sweeps `(0, 2, 8, 16)` bps, covering criterion 2's 8–16 bps.

**Module layout, driver, make target** all as specified.

**Definition of Done** — `README.md` carries 12 xsmom references, CLAUDE.md and
MEMORY both cover the package/tool/target. Unlike P2's DoD, nothing here was
pre-registered and skipped.

## Guard mutation-tested, not read

The spec calls the causality test "the package's most important test", and this
sleeve carries the repo's known vacuity precedent
(`[[xsmom-causality-test-vacuous]]` — the probe once passed under mutation
because the fixture was cap-saturated, so perturbing a ±20-pinned instrument
moved the forecast by exactly 0.0; fixed in #549). So it was mutated:

| mutation | tests | result |
| --- | --- | --- |
| `xs_leverage` — drop `demeaned.shift(1)` | `test_xs_leverage_is_causal_no_lookahead` | **FAILED ✓** |
| same | `test_xs_leverage_dollar_neutral_is_causal_no_lookahead` | **FAILED ✓** |

2/2 died as they must; tree restored and re-verified green (48 passed).

**This guard already carries a positive control** (`test_book.py:102-111`) — it
asserts the perturbation *did* propagate to row `k+1`, without which the
invariance assertion would pass simply because the bump changed nothing. That is
exactly the gap #566 filed against the four `forecast/` and `combine/` guards,
already closed here by #549. **The #549 fix holds and is not decorative.**

## #567's cross-section correction does NOT apply here

PR #567 established that pooling symbol-days across this 25-perp universe inflates a
naive t-stat ~2.92× (mean pairwise correlation 0.315 ⇒ ~2.92 effective
independent series), and flagged xsmom's per-symbol cuts as inheriting it.
**Checked, and they do not.** The only t-stat the sleeve publishes is
`alpha_tstat` from `beta_attribution`, a full-sample OLS whose inputs are
`run_xs_backtest(...).portfolio_return` and a market proxy — **book-level daily
series, one observation per day** (`tools/xsmom_audit.py:118`). No published
xsmom figure is computed over pooled symbol-days.

Recorded as a clean negative so nobody "corrects" a number that needs no
correction — the deflator is fail-safe but applying it where it does not belong
would understate a real result.

## Scope note

`diagnostics.py`, `execution.py` and `live.py` postdate this spec and belong to
later sub-projects with their own specs and plans (beta-neutral/persistence
2026-06-17, execution capacity 2026-06-20, live targets 2026-06-20, routing
overlay 2026-06-21). The spec's "no live-daemon, schema, or golden change"
out-of-scope line was **deliberately superseded** by those, not violated by this
one. Judged in scope for this reconcile only insofar as they do not contradict
the v1 construction — they do not; each threads through `run_xs_backtest` via
additive default-off keywords that are byte-identical when unset.

## Follow-ups filed (none fixed here)

1. **Amend §Causality** to assert on the position (or on returns strictly before
   day `d`). The current wording is a trap for any reimplementation.
2. **Amend §Verdict criterion 4** to read as the disqualifier it is, not a bound
   nothing enforces.
3. **Decide whether the G3 verdict deserves a coded `xs_gate_verdict`** beside
   `combine_gate_verdict`, or whether it is explicitly advisory. Either is fine;
   the present state is neither.
4. Carried from #566, still open: amend P2 §1/§6 to the three-leg gate, and add
   positive controls to the four `forecast/` + `combine/` causality guards —
   `tests/xsmom/test_book.py:102-111` is the worked pattern.
5. **Next specs by stakes:** H9 warning-value, then ST1 reference-level.
