# P3 — Cross-Sectional Momentum Sleeve vs Gate G3 (verdict)

**Date:** 2026-06-16
**Driver:** `tools/xsmom_audit.py` (`make buibui-xsmom-audit`), read-only over `analytics.db`.
**Spec:** `docs/superpowers/specs/2026-06-16-p3-cross-sectional-momentum-sleeve-design.md`
**Plan:** `docs/superpowers/plans/2026-06-16-p3-cross-sectional-momentum-sleeve.md`

## What was built

A standalone cross-sectional momentum sleeve: the existing capped multi-speed
EWMAC forecast per instrument, cross-sectionally demeaned each day (dollar-
neutral), `.shift(1)`'d for causality, vol-parity sized, and run through a
dollar-neutral long-short book with honest costs (turnover + funding; shorts
*receive* funding on perps) and the 20%-vol portfolio governor. Evaluated
universe-wide with DSR / PBO / block-bootstrap-CI / MinTRL, plus correlation to
the trend sleeve. New package `analytics/xsmom/` ({`book`, `replay`, `report`}),
pure / causal / read-only — reuses `ForecastConfig`, `combine_forecasts`,
`ew_return_vol`, the trend loader `load_daily_inputs`, `portfolio.metrics`, and
`research_guards` unchanged. No schema/golden change.

## Result (net of costs, 20% vol target)

### Breadth contrast (@ 2 bps/leg)

| Book | n_inst | days | Sharpe | Sortino | max_dd | ann_ret | ann_vol | DSR | PBO | boot CI (Sharpe) | MinTRL | corr_to_trend | trend_Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| universe | 25 | 2475 | **+1.375** | +1.388 | −0.397 | +0.478 | +0.321 | **+0.997** | **+0.295** | **[+0.596, +2.106]** | +7035 | +0.370 | +0.359 |
| majors (BTC/ETH/SOL) | 3 | 2475 | +0.294 | +0.278 | −0.325 | +0.030 | +0.128 | +0.727 | +0.919 | [−0.593, +1.142] | ∞ | +0.225 | +0.647 |

### Cost sensitivity (universe)

| bps/leg | Sharpe | DSR | PBO | boot_lo | MinTRL |
| --- | --- | --- | --- | --- | --- |
| 0 | +1.401 | +0.997 | +0.274 | +0.621 | +6155 |
| 2 | +1.375 | +0.997 | +0.295 | +0.596 | +7035 |
| 8 | +1.298 | +0.995 | +0.350 | +0.512 | +11201 |
| 16 | +1.194 | +0.992 | +0.440 | +0.406 | +26463 |

### Per-speed XS Sharpe

| trial | Sharpe |
| --- | --- |
| s8_32 | +1.646 |
| s16_64 | +1.319 |
| s32_128 | +1.175 |
| s64_256 | +0.914 |
| combined | +1.375 |

## Verdict: G3 CLEARED — the first gate-clearing sleeve in this system

The commit gate is **DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0** (the same bar the
forecast-weight study failed). The universe XS sleeve clears all three:
**DSR +0.997, PBO +0.295, bootstrap-CI [+0.596, +2.106] (excludes zero)** — and
it clears at **every** cost level from 0 to 16 bps/leg (DSR ≥ 0.99, PBO ≤ 0.44,
boot_lo ≥ +0.41 throughout). Headline Sharpe **+1.375** net of 2 bps, cost-robust
to **+1.194** at 16 bps. This is the first sleeve in the project — after a
−0.12 R/alert TA book (0/123 DSR passes) and a +0.36 sub-bar trend sleeve — to
pass the de-biased commit gate.

**Sanity checks pass.** `trend_Sharpe` +0.359 reproduces the G2 universe +0.36
(the driver reuses `replay_universe` verbatim — methodology cross-check). The
per-speed structure is fast > slow (s8_32 +1.65 → s64_256 +0.91), same shape as
trend.

## The findings that matter

1. **Breadth is essential, and the direction REVERSES vs trend.** Majors-only XS
   is weak (Sharpe +0.29, DSR 0.73, PBO 0.92, boot_lo −0.59) — three instruments
   barely form a cross-section to rank. The universe (25) is where the edge lives.
   This is the exact mirror of the G2 trend finding (majors 0.65 > universe 0.36)
   and **confirms the P2→P3 thesis precisely: alt breadth pays cross-sectionally
   (relative strength), not in parallel absolute trend.** The lever the trend
   sleeve could not pull, the cross-sectional expression pulls cleanly.

2. **The cross-sectional expression revives the slow speeds.** In trend, slow
   EWMAC was dead (s64_256 +0.03) and dragged the equal-weight combine to +0.36.
   In XS, *every* speed is strongly positive (slowest s64_256 still +0.91), so the
   equal-weight combine holds up at +1.375 — no dead-leg drag, no forecast-weight
   study needed. Fast still leads, but the slow relative-strength signal is real
   here where the slow absolute-trend signal was not.

3. **It is a strong standalone sleeve, and still diversifies trend.**
   `corr_to_trend` is +0.37 — not a pure diversifier, but well below 1. An
   equal-vol blend of two corr-0.37 sleeves cuts portfolio vol ~17%. Since XS is
   by far the stronger sleeve, the eventual IDM combine (Task 2) would weight XS
   heavily with trend as a modest diversifier — not the other way round.

## Caveats (read before trusting the headline)

- **Survivorship is the binding caveat.** `config/universe.toml` is a *fixed*
  25-perp set selected on **recent** liquidity (`tools/select_universe.py`: top-N
  by 30d median quote volume, listed ≥ 1y) and held constant across the whole
  2475-day backtest (instruments are NaN before they listed, but the *membership*
  is today's survivors). It therefore excludes coins that were liquid in 2021 and
  later died/delisted (LUNA, FTT, …). Cross-sectional momentum on a survivor set
  is generally **optimistic** — the survivors are the persistent trenders, and the
  worst blow-ups (which a live XS book would have ranked and traded) are absent.
  **+1.375 should be read as an upper bound.** The honest next validation is a
  point-in-time / delisting-inclusive universe (the `symbol_lifecycle` table from
  N3 is the infrastructure for this). Until that is run, treat the magnitude as
  provisional and the *sign + breadth-direction* as the robust finding.
- **MinTRL > sample.** MinTRL to confirm a true Sharpe **> 1.0** at 95% is ~7035
  obs vs 2475 available. So while DSR (vs benchmark 0, trial-deflated) is 0.997
  and the bootstrap CI excludes zero, the sample **cannot yet statistically
  confirm the true Sharpe exceeds 1.0** — only that it is very likely > 0. The
  point estimate is well above 1.0; the confirmation of *that level* is not yet
  earned.
- **Vol runs hot.** Realised ann_vol is 32% vs the 20% target — the causal
  governor lags realised vol in crypto's volatility clustering (it clips at
  1.5×/0.5× on a trailing, shifted estimate). Sharpe is vol-invariant so this does
  not inflate it, but a live deployment would either tighten the governor or
  accept the higher vol.
- **Dollar-neutral, not beta-neutral.** No BTC-beta neutralisation (deferred). A
  dollar-neutral demeaned book already nets out most market beta, but residual
  beta is unmeasured here — a noted refinement.

## Recommendation / next levers (in order)

1. **Survivorship re-test (cheap, decisive).** Re-run the sleeve on a
   delisting-inclusive / point-in-time universe using `symbol_lifecycle`. If the
   edge survives (even attenuated) with the dead names included, the result is
   real and this becomes the system's core sleeve. This is the single most
   important validation and should precede any deployment thinking.
2. **IDM / correlation portfolio layer + trend×XS combine (Task 2).** With a
   genuine positive sleeve in hand, the correlation-aware combine of trend (+0.36)
   and XS (+1.375, corr 0.37) is now worth building — XS-weighted, trend as
   diversifier. Carver's IDM is the reference.
3. **Re-judge the roadmap.** XS momentum is promoted from hypothesis to the
   strongest candidate core sleeve in the system — pending (1). The trend sleeve
   stays as a cost-robust diversifier; the TA book remains demoted.

## DoD

lint-py ✓ · typecheck ✓ (318 files) · `make test` 1870 ✓ · test-regression
goldens **UNMOVED** ✓ (additive read-only package) · lint-md ✓.
