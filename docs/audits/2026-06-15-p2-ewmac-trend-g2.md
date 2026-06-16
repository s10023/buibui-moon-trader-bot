# P2 — EWMAC Trend Sleeve A/B vs Gate G2 (verdict)

> Read-only audit. Engine: `analytics/forecast/`. Driver:
> `tools/forecast_audit.py` (`make buibui-forecast-audit`). Spec/plan:
> `docs/superpowers/specs/2026-06-15-p2-ewmac-trend-sleeve-design.md` ·
> `docs/superpowers/plans/2026-06-15-p2-ewmac-trend-sleeve.md`.
> Ran against `analytics.db` (25-perp N3 universe, 1d, ~2019→2026, 2473 days).

## What was built

Continuous, vol-normalised, multi-speed **EWMAC** trend forecasts (Carver
convention: vol-adjusted crossover × published forecast scalar, capped ±20,
equal-weight 4-speed combine × FDM) → per-instrument vol-targeted leverage →
**portfolio-vol-targeted (20 %) causal paper book** with honest costs (turnover +
perp funding accrual). Evaluated **universe-wide**, with DSR / PBO / block-
bootstrap-CI / MinTRL stamps. Pure, causal (no look-ahead — strengthened tests
assert it), read-only; goldens unmoved.

## Result (net of costs, 20 % vol target)

### Breadth contrast (@ 2 bps/leg)

| Book | n_inst | days | Sharpe | Sortino | max_dd | ann_ret | ann_vol | DSR | PBO | boot CI (Sharpe) | MinTRL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| universe | 25 | 2473 | **+0.363** | +0.350 | −0.262 | +0.051 | +0.181 | +0.487 | +0.003 | [−0.356, +1.062] | ∞ |
| majors (BTC/ETH/SOL) | 3 | 2473 | +0.649 | +0.660 | −0.407 | +0.118 | +0.203 | +0.054 | +0.054 | [−0.176, +1.443] | ∞ |

### Cost sensitivity (universe)

| Costs/leg | Sharpe | max_dd | ann_ret | ann_vol |
| --- | --- | --- | --- | --- |
| 0 bps | +0.372 | −0.261 | +0.052 | +0.181 |
| 2 bps | +0.363 | −0.262 | +0.051 | +0.181 |
| 8 bps | +0.337 | −0.267 | +0.046 | +0.181 |
| 16 bps | +0.301 | −0.275 | +0.039 | +0.181 |

### Per-speed Sharpe (H2 cycle-bias check)

| Trial | Sharpe |
| --- | --- |
| s8/32 (fast) | **+0.826** |
| s16/64 | +0.451 |
| s32/128 | +0.110 |
| s64/256 (slow ≈ "50W EMA") | +0.031 |
| combined (equal-weight ×4) | +0.363 |

## Verdict: G2 NOT cleared (FAIL on the ≥ ~1 bar) — but a real, structural, cost-robust positive

The gate is **trend-sleeve OOS Sharpe ≥ ~1 on the universe, costs in,
DSR/PBO-gated.** The universe book scores **+0.36** — clearly positive and
cost-robust, but far short of 1.0, and its **95 % bootstrap CI [−0.36, +1.06]
includes zero**, so the realised Sharpe is **not statistically distinguishable
from 0** on this sample. DSR (0.49) is a coin-flip, well under the 0.95 commit
bar; MinTRL is ∞ (a 0.36-Sharpe track can never confirm a 1.0 target at 95 %).
The low PBO (0.003) is reassuring but near-trivial here — the config is fixed
textbook (no selection to overfit).

**This is still a meaningful step up from the TA book** (0/123 DSR passes,
−0.12 R/alert): trend is the first edge in this system that is positive,
cost-robust to 16 bps, structurally motivated, and expressed correctly as a
continuous vol-scaled forecast. It just is not yet at the bar.

### The three findings that matter more than the headline

1. **Fast trend carries the edge; slow trend is dead on this sample.** s8/32
   alone = **+0.83** (near the G2 bar); the two slowest speeds are ~0. The
   equal-weight combine is dragged down to +0.36 by the dead slow legs. The
   textbook combine is the *honest* number, but the speed structure is a strong,
   actionable signal — a forecast-weight study (down-weighting the dead slow
   speeds) is the obvious next lever, **gated by DSR/PBO** so it does not become
   the in-sample tilt the guards exist to prevent.
2. **H2 falsified.** The slow 64/256 EWMAC — the "50 W EMA flip = BTC 4-year
   cycle bias" proxy — has the **worst** Sharpe (+0.03). On this deep history +
   bear market there is no slow-cycle trend edge; the cycle-bias hypothesis is
   not supported. (Do not build a slow-cycle/50 W sleeve.)
3. **Breadth cut risk, not Sharpe — this sample.** Majors-only Sharpe (0.65) >
   universe (0.36), the reverse of the "breadth lifts trend Sharpe" prior. But
   breadth **nearly halved max-DD** (−41 % → −26 %) at a similar vol target. So
   the alts diluted return-per-unit-risk here while diversifying drawdown.
   Likely drivers: (a) newer/lower-quality alt history (e.g. HYPE) with weaker or
   noisier trends; (b) high cross-correlation so breadth adds little independent
   trend. **This is the cross-sectional question, not the time-series one** —
   which is exactly what P3 (XS momentum + a real correlation/IDM portfolio
   layer) is for. TS-momentum across correlated alts is not where alt breadth
   pays; relative-strength ranking is.

## Caveats

- **Equal-weight, fixed-textbook config by design** (no crypto-fit speeds/scalars,
  FDM = 1.25 constant). The honest unbiased read. A weight/FDM optimisation could
  lift the combined Sharpe toward the fast-speed level, but only with its own OOS
  validation and a DSR/PBO haircut over the enlarged trial family.
- **`sharpe_annual` (curve-based) vs the bootstrap CI (array-based)** differ by
  ~0.016 (the curve drops the first return via `pct_change`); negligible,
  conservative (headline slightly lower). Noted for completeness.
- **No IDM / correlation weighting** (deferred to P3): the portfolio vol governor
  absorbs aggregate correlation into the level but does not optimise per-name
  weights — so the universe book is closer to an equal-risk average than an
  efficient trend portfolio.

## Recommendation / next levers (in order)

1. **Forecast-weight study (cheap, in-engine):** does down-weighting the dead
   slow speeds (or a fast-tilted set) lift the *universe* combined Sharpe toward
   the s8/32 level **and survive DSR/PBO** over the enlarged trial family? If yes
   → re-test G2. If the lift evaporates under the haircut → it was in-sample.
2. **P3 cross-sectional momentum + portfolio layer (IDM, correlation):** the
   "majors > universe" finding says the alt breadth has to be expressed as
   *relative strength*, not parallel absolute trend. This is the proper
   diversification lever and the natural next sub-project.
3. **Re-judge G2** only after (1)–(2). The trend sleeve stays as a **positive,
   cost-robust core candidate** in the meantime — demoted to "promising,
   sub-bar", not killed.

## DoD

`make lint-py` ✓ · `make typecheck` ✓ (305 files) · `make test` ✓ (1836 passed)
· `make test-regression` ✓ (3/3 goldens UNMOVED — additive read-only package).
