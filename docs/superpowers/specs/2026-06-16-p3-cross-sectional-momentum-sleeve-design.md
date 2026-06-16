# P3 — Cross-Sectional Momentum Sleeve (design)

**Date:** 2026-06-16
**Status:** approved (brainstorm) → ready for plan
**Author:** quant-systems engineer (Claude) + user
**Predecessors:** P2 EWMAC trend sleeve
(`docs/audits/2026-06-15-p2-ewmac-trend-g2.md`), forecast-weight study
(`docs/audits/2026-06-16-p2-forecast-weight-study.md`)

## Goal (one line + success metric)

Test whether expressing the alt-coin breadth as **relative strength**
(cross-sectional, demeaned) rather than **parallel absolute trend** (time-series)
produces a positive, cost-robust, DSR/PBO-survivable sleeve that is **diversifying
vs the trend sleeve** (low return correlation). Success = a clear, de-biased
verdict on that question; an honest negative is a valid result.

## Why this, why now

The P2 G2 verdict left one finding pointing squarely here (finding #3):

> Majors-only trend Sharpe (0.65) > universe (0.36) — the reverse of the
> "breadth lifts trend Sharpe" prior. Breadth nearly halved max-DD
> (−41 % → −26 %) but diluted return-per-unit-risk. **This is the
> cross-sectional question, not the time-series one.** TS-momentum across
> correlated alts is not where alt breadth pays; relative-strength ranking is.

The forecast-weight study then confirmed the time-series weight vector is not the
lever (no scheme cleared the gate; fast tilt is in-sample). So the binding
constraint is **breadth expressed cross-sectionally**. This sub-project builds the
first standalone cross-sectional momentum (XS-mom) sleeve to measure that edge.

## Signal choice (decided)

**Per-instrument score = the existing capped multi-speed EWMAC forecast,
cross-sectionally demeaned each day.** Rationale:

- **Cleanest experiment.** The only variable changed vs the trend sleeve is the
  *expression* (cross-sectional demean) — not the signal. A positive result is
  unambiguously attributable to relative-strength expression, isolating exactly
  the variable G2 finding #3 flagged.
- **Maximal reuse.** `combine_forecasts`, `ew_return_vol`, the causal portfolio
  vol governor, `portfolio.metrics`, and `research_guards` all carry over. The XS
  sleeve is a thin demean-and-net layer, not a second engine.
- **Carver-consistent.** "Relative momentum" *is* the instrument forecast minus
  the universe-average forecast.

A raw trailing-return signal tests a genuinely different hypothesis and is a
future lever; mixing a new signal *and* a new expression in one sub-project would
confound the verdict, so it is out of scope here.

## Construction (decided)

**Continuous demeaned forecast** (not discrete top-K/bottom-K quantiles):

- Discrete quintiles add an arbitrary `K` to overfit and throw away the magnitude
  information the continuous forecast already carries. The continuous demeaned
  forecast is naturally dollar-neutral (Σ demeaned = 0), reuses all the trend
  machinery, and has no discretization knob.

### Math (per UTC day `d`, per active instrument `i`)

1. **Forecast** — `f_i(d) = combine_forecasts(close_i, cfg.speeds, cfg.fdm,
   cfg.vol_span, cfg.cap, weights=cfg.weights)` — identical to the trend sleeve,
   capped ±20, causal.
2. **Cross-sectional demean** — `g_i(d) = f_i(d) − mean_{j ∈ active(d)} f_j(d)`,
   where `active(d)` is the set of instruments with a defined forecast on day `d`.
   By construction `Σ_i g_i(d) = 0` → dollar-neutral. (The mean is a same-day
   cross-sectional reduction over causal forecasts — no future information.)
3. **Causal shift** — `.shift(1)` the demeaned forecast before sizing (position
   held during day `d` uses info through close of `d-1`).
4. **Vol-parity leverage** —
   `leverage_i = (g_i_shifted / 10.0) · (cfg.vol_target_annual / vol_ann_i)`,
   with `vol_ann_i = ew_return_vol(close_i, cfg.vol_span) · √annualization_days`
   (already causal). The `/10.0` constant mirrors the trend sleeve so leverage
   magnitudes are comparable; its absolute level is ultimately governed.
5. **Returns + honest costs** (per instrument, identical to trend):
   - `gross_i = leverage_i · r_i`, `r_i = close_i.pct_change()`
   - `turnover_cost_i = |leverage_i − leverage_i.shift(1)| · (fee_pct + slippage_pct)`
   - `funding_cost_i = leverage_i · funding_daily_i` — **shorts (negative
     leverage) receive funding** in contango, captured by the sign.
   - `net_i = gross_i − turnover_cost_i − funding_cost_i`
6. **Aggregate** — `book(d) = Σ_i net_i(d)` (long-short sum, not equal-risk mean;
   the level is set by the governor in the next step, so sum-vs-mean is only a
   scale the governor absorbs).
7. **Portfolio vol governor** — the existing causal trailing-vol governor
   (20 % target, clipped `g_min`–`g_max`, `.shift(1)` trailing-vol) scales the
   book. Identical logic to `forecast/book.py`.

## Module layout

New sibling package `analytics/xsmom/`, mirroring `analytics/forecast/`:

```text
analytics/xsmom/
  __init__.py        # eager re-exports
  book.py            # run_xs_backtest(closes, fundings, cfg) -> XSBookResult
  replay.py          # replay_xs / replay_xs_trials (read-only DB front door)
  report.py          # evaluate_xs(...) -> XSReport (G3-style verdict)
```

Reused as-is (no edits, additive): `analytics/forecast/config.py`
(`ForecastConfig` — has speeds/vol_span/fdm/cap/costs/vol_target/governor knobs,
nothing XS-specific needed), `analytics/forecast/ewmac.py`
(`combine_forecasts`), `analytics/forecast/vol.py` (`ew_return_vol`),
`analytics/forecast/replay.py` (`load_daily_inputs`), `portfolio.metrics`,
`analytics/research_guards/`.

Driver: `tools/xsmom_audit.py` + `make buibui-xsmom-audit`
(`duckdb.connect(..., read_only=True)`).

### `XSBookResult` (frozen dataclass)

Mirror `ForecastBookResult`: `daily_index`, `portfolio_return` (net,
post-governor, NaN-free), `pre_governor_return`, `governor`, `active_count`,
`per_instrument_net`. Plus `gross_long`/`gross_short` daily series is **not**
required for v1 (kept minimal); add only if the verdict needs leg attribution.

### `XSReport` (frozen dataclass)

Mirror `G2Report` headline + guards: `sharpe_annual`, `sortino_annual`,
`max_dd`, `calmar`, `annual_return`, `annual_vol`, `n_obs`, `dsr`, `pbo`,
`boot_lo`, `boot_hi`, `min_trl`. **Plus the decision-relevant XS extras:**

- `corr_to_trend: float` — Pearson correlation of XS daily returns vs the trend
  sleeve daily returns (aligned on common days). The diversification number.
- `trend_sharpe: float` — the trend universe Sharpe on the same window, for the
  contrast row (expected ≈ +0.36).

`evaluate_xs(result, cfg, trial_returns, trend_returns)` takes the trend sleeve's
daily returns as an argument (the driver computes them once via
`forecast.replay.replay_universe` on the same universe/window). `corr_to_trend`
and `trend_sharpe` are computed over the common-day intersection; when
`trend_returns` is empty both default to `0.0` (degenerate-safe, never NaN).

## Multiple-testing family (DSR / PBO)

Symmetric with the trend sleeve's 5-trial family:

- **`replay_xs_trials`** returns daily returns for: one XS book per single speed
  (`s8_32`, `s16_64`, `s32_128`, `s64_256`) + the combined XS book
  (`"combined"`).
- DSR deflates the chosen combined-XS Sharpe against this family.
- PBO/CSCV runs over the same family (no separate `pbo_returns` split needed in
  v1; the schemes-only split was a weight-study artifact).

## Causality invariant (load-bearing)

The position held during day `d` is sized only from information through close of
`d-1`. The cross-sectional demean is a same-day reduction over causal forecasts;
the `.shift(1)` is applied **after** demeaning, **before** sizing. Tests:
strengthened middle-bar perturbation — perturb a single instrument's close on day
`d` and assert no book return on day `< d+1` changes (RED without the shift,
GREEN with it). This is the package's most important test.

## Verdict (G3-style) — what "success" means

The sleeve is worth carrying into the IDM combine (Task 2) if it is:

1. **Positive** headline Sharpe (net of costs),
2. **Cost-robust** (still positive at 8–16 bps/leg in the cost-sensitivity table),
3. **DSR/PBO-survivable** over the 5-trial family, **and**
4. **Diversifying** — `corr_to_trend` near zero (a modest-Sharpe XS sleeve that is
   uncorrelated with trend is a real combine win even if it fails the ≥~1 bar
   standalone).

An honest negative (XS edge is also weak, or merely a re-labelled trend with high
`corr_to_trend`) is a valid, publishable result — demote, don't hide. The verdict
doc lands at `docs/audits/2026-06-16-p3-xsmom-sleeve.md`.

## Out of scope (explicit, deferred)

- **Beta-neutralization** (regress out BTC) — dollar-neutral only in v1; residual
  BTC-beta is a noted caveat and a future refinement (mirrors how IDM was deferred
  out of P2).
- **IDM / correlation portfolio layer** and the **trend + XS combine** — Task 2,
  separate sub-project.
- **Raw trailing-return / risk-adjusted-return signals** — different-signal
  hypotheses, future levers.
- Any live-daemon, schema, or golden change. Read-only replay against
  `analytics.db` only.

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ · `make test` green (new `tests/xsmom/`)
- `make test-regression` goldens **UNMOVED** (additive read-only package — no
  engine/schema touch).
- Verdict written to `docs/audits/2026-06-16-p3-xsmom-sleeve.md`.
- CLAUDE.md + README + MEMORY updated for the new package/tool/make target.
