# P3 Cross-Sectional Reversal Sleeve — verdict (2026-07-25)

**Verdict: REDUNDANT / NO-EDGE.** Short-horizon (2-7 day) cross-sectional reversal
is the sign-inverse of the momentum deploy core — it loses ~-2.9 Sharpe on the N3
universe, and it loses **even at ZERO cost**, so this is not a microstructure or cost
artifact: the crypto cross-section CONTINUES at 2-7 days, it does not revert. The "a
short-horizon reversal is the decorrelated second edge" hypothesis is falsified.
XS-solo momentum remains the deploy core; the `analytics/xsrev/` package is shelved as
a validated template + diagnostic, alongside trend and carry.

Spec: `docs/superpowers/specs/2026-07-24-p3-xs-reversal-sleeve-design.md` ·
Plan: `docs/superpowers/plans/2026-07-24-p3-xs-reversal-sleeve.md` ·
Engine: `analytics/xsrev/` · Driver: `tools/xsrev_audit.py` (`make buibui-xsrev-audit`).
Read-only replay over `analytics.db` (1d, N3 universe, 25 perps, 2516 days).

## In plain English

- **Headline:** we tested "buy the recent losers, short the recent winners" over a
  2-7 day window across the 25-coin universe. It loses badly (Sharpe -2.9). The
  opposite is true — recent winners keep winning at this horizon.
- **Metric:** Sharpe = return per unit of risk. The de-biased gate needs DSR >= 0.95,
  PBO <= 0.5, a bootstrap lower bound > 0, and cost-robustness. Reversal fails every
  one, at every cost, at every formation window.
- **Money:** at a 20% vol target the reversal book would have LOST money every year —
  annual return -0.61, drawdown ~100%.
- **Backtest vs live:** this is a backtest over 2516 days of real universe data. There
  is no live leg — it is a research kill-test, and a decisive one.

## The gate

> DSR >= 0.95 ∧ PBO <= 0.5 ∧ block-bootstrap CI lower bound > 0, still positive at 8 bps.

| book | days | sharpe | dsr | pbo | boot_lo | corr_to_xs | xs_sharpe | gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| universe @2bps | 2516 | -2.913 | 0.000 | 0.900 | -3.633 | -0.279 | +1.296 | FAIL |
| majors @2bps | 2516 | -1.208 | 0.000 | 0.979 | -1.878 | -0.254 | +0.262 | FAIL |

Sanity anchor: the same replay reports `xs_sharpe = +1.296` (reproducing the known
XS-momentum deploy-core result on this window), so the plumbing is correct; the
reversal numbers are real, not a harness artifact.

## Findings

1. **Negative at ZERO cost** (universe -2.748 @0bps -> -2.913 @2bps -> -3.408 @8bps ->
   -4.062 @16bps). It is not a cost or bid-ask-bounce artifact — the SIGNAL is
   wrong-signed. The short-horizon cross-section continues, it does not revert.
2. **The additivity thesis is falsified.** The whole premise was that a 2-7 day
   reversal lives at a horizon XS momentum (8-256d EWMAC) does not cover, so it would
   be additive. Instead momentum extends DOWN to 2-7 days — there is no reversal
   pocket. `corr_to_xs = -0.28` (modest, because the horizon differs) confirms it is
   momentum-flavoured, not orthogonal.
3. **Every formation window agrees** — per-k Sharpe k2 -2.78 / k3 -2.85 / k5 -2.78 /
   k7 -2.87, combined -2.91. The excluded k=1 diagnostic is worse (-3.38): no
   bid-ask-bounce reversal edge either.
4. **Breadth AMPLIFIES the loss** (universe -2.9 vs majors -1.2) — the mirror image of
   XS-momentum, where alt breadth PAYS. Consistent with reversal = -momentum: whatever
   breadth does for momentum, it does against its inverse.
5. **Scalar-insensitive** (5.0 -2.75 / 10.0 -2.91 / 20.0 -2.99) — governor-normalised,
   as designed; the loss is not an artifact of the a-priori scalar.
6. **OI-positioning crowding panel:** -0.41 Sharpe, **DESCRIPTIVE ONLY**
   (`open_interest` is ~144d majors / 30-60d rest, far short of MinTRL) — no
   BUILD/SHELF verdict; a rigorous OI arm needs a deeper OI backfill.

## Decision

- **SHELF the reversal sleeve** alongside trend + carry — demoted, not deleted. The
  `analytics/xsrev/` package + audit remain a validated diagnostic and a reusable
  forecast-construction template (it correctly reuses the XS book via the additive,
  byte-identical `forecasts=` injection hook, which is the one durable asset here).
- **Hypothesis retired:** "short-horizon reversal is the decorrelated second edge."
  Cheaply falsified by the de-biased gate — the gate's intended use.
- **XS-solo momentum remains the deploy core** (+1.375, unchanged).
- **The binding constraint is confirmed a FIFTH time** (exits, trend-weight study, the
  combine, carry, now reversal): the system needs a second *strong* edge, and the cheap
  price-only free-data levers keep coming up empty. The next candidate must come from
  genuinely NEW information — OI depth (CoinGlass), basis term-structure (dated
  futures), or a non-price structural signal — a fresh brainstorm, not another sign or
  horizon variant of the same price series.

## Reproduce

```bash
make buibui-xsrev-audit          # read-only over analytics.db
```
