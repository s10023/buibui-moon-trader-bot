# P3 Carry sleeve — funding-carry G-gate verdict (2026-06-19)

**Verdict: FAIL the de-biased gate. No standalone edge — carry is essentially flat.**
The "carry is the obvious second edge" hypothesis is retired cheaply. Shelf the carry
sleeve alongside trend; **XS-solo remains the deploy core**. The diversification
*direction* is right (carry is genuinely low/anti-correlated to XS) but a ~0-Sharpe
sleeve has nothing to contribute to a combine regardless of correlation.

Spec: `docs/superpowers/specs/2026-06-19-p3-carry-sleeve-design.md` ·
Plan: `docs/superpowers/plans/2026-06-19-p3-carry-sleeve.md` ·
Engine: `analytics/carry/` · Driver: `tools/carry_audit.py` (`make buibui-carry-audit`).
Read-only replay over `analytics.db` (1d, N3 universe, 25 perps, 2477 days).

## The gate

> DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ block-bootstrap CI lower bound > 0

Headline = cross-sectional (relative) funding carry, honest costs @2 bps/leg, carry
scalar fixed a-priori at 30 (NOT crypto-fit; governor-normalised standalone book).

| expression | days | Sharpe | max_dd | DSR | PBO | boot_lo | boot_hi | corr_to_xs | corr_to_trend | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **cross-sectional** | 2477 | **+0.034** | −0.552 | 0.352 | 0.784 | −0.755 | +0.851 | +0.040 | −0.028 | **FAIL** |
| absolute | 2477 | −0.681 | −0.627 | 0.025 | 0.208 | −1.565 | +0.208 | +0.067 | −0.391 | FAIL |

Sanity anchor: the same replay reports `xs_sharpe = +1.375` (reproducing the known XS
deploy-core result) and the trend sleeve on the same window — so the plumbing is correct;
the carry numbers are real, not a harness artifact.

The cross-sectional sleeve is **flat** (Sharpe +0.034, annual return −0.023 after costs),
DSR is a coin-flip-below (0.35), PBO is high (0.78 ⇒ high overfit probability across the
span family), and the 95% bootstrap CI spans zero ([−0.755, +0.851]). It fails every gate
criterion.

## Findings

1. **Cross-sectional > absolute, again — the breadth thesis holds (P2→P3→here).**
   Cross-sectional carry (+0.034) beats absolute carry (−0.681). Absolute carry is
   net-short market beta (funding is persistently positive ⇒ the book is mostly short ⇒
   `corr_to_trend −0.391`, a directional-momentum bet, and it bleeds). Relative carry
   strips that out — but lands on flat, not positive.

2. **The diversification *direction* is right but moot.** `corr_to_xs ≈ +0.04` (universe),
   **−0.24 (majors)**: carry is genuinely uncorrelated-to-anti-correlated with the XS
   momentum core. This confirms the mechanical intuition (high funding ↔ crowded recent
   winners, so *short-high-funding* opposes *long-recent-winners*). But the combine math
   needs a *comparably-strong* second edge — a ~0-Sharpe stream cannot lift a blend no
   matter how decorrelated. (Combine verdict, 2026-06-18: beating S_xs needs S_carry ≳ 0.9.)

3. **The (weak) edge that exists is the persistent funding LEVEL, not instantaneous
   funding.** Per-span Sharpe (cross-sectional): span1 (raw daily funding) **−0.098**,
   span5 +0.215, **span20 +0.278** (best), span60 +0.176. Funding carry, to the extent it
   pays, is a slow level signal; raw instantaneous funding is negative. The equal-weight
   combine is dragged to flat by the dead span1 (the same drag pattern as the trend
   sleeve's dead slow speeds). A span-weighting study could recover ~+0.28 in-sample, but
   that is exactly the data-snoop the gate forbids without OOS confirmation, and +0.28 is
   still well short of the bar and of XS's +1.375.

4. **Not cost-robust.** Universe Sharpe by cost: 0 bps +0.125, 2 bps +0.034, 8 bps −0.238,
   16 bps −0.600. Positive only near zero cost; gone by 8 bps. (Cross-sectional carry
   turns over as funding ranks rotate.)

5. **Scalar sensitivity is a caveat, not a finding.** scalar 15 → +0.049, 30 → +0.034,
   60 → +0.335. The lift at 60 is cap-saturation reshaping the book, and 60 is not an
   a-priori value — picking it would be data-snooping. At the a-priori 30 the sleeve is
   flat. Reported for honesty; it does not change the verdict.

6. **Majors-only is marginally better and more decorrelated** (+0.155 Sharpe,
   `corr_to_xs −0.24`) but n_inst=3 and still FAIL — the opposite breadth-direction from
   XS (which *needs* alt breadth). Not actionable.

## Decision

- **Shelf the carry sleeve alongside trend.** It is a real, cost-fragile, sub-bar signal —
  demoted, not deleted (working agreement: demote on (regime × session × combo)-style
  evidence; this is the cross-sectional analog). The `analytics/carry/` package + audit
  remain as a validated diagnostic and a reusable forecast-construction template.
- **XS-solo remains the deploy core** (+1.375, alpha-not-beta, persistent). Unchanged.
- **The combine / IDM layer still has no comparably-strong second edge to combine.** The
  binding constraint is confirmed a *fourth* time (exits, trend weight study, the combine,
  now carry): the system needs a second *strong* edge, and funding carry is not it.
- **Hypothesis retired:** "carry is the obvious second structural edge." Cheaply falsified
  by a de-biased gate — the intended use of the gate. The next second-edge candidate must
  come from elsewhere (basis/term-structure needs quarterly-future or spot+perp data we do
  not yet ingest; or a non-price structural signal). That is a fresh brainstorm, not a
  weight-vector tweak on carry.

## Reproduce

```bash
make buibui-carry-audit          # read-only over analytics.db
# or: PYTHONPATH=. poetry run python tools/carry_audit.py --majors BTCUSDT,ETHUSDT,SOLUSDT
```
