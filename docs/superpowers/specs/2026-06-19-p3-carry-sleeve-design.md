# P3 Carry sleeve — funding-carry as a vol-scaled forecast (design)

**Date:** 2026-06-19
**Status:** design (approved direction; spec under review)
**Author:** quant-systems engineer (Claude)
**Predecessors:** `analytics/forecast/` (P2 trend), `analytics/xsmom/` (P3 XS-mom, deploy core), `analytics/combine/` (P3 IDM combine layer)

## 1. Motivation

The combine verdict (`docs/audits/2026-06-18-p3-trend-xs-combine.md`) is structural:
a Sharpe-improving combine needs a **second comparably-strong edge**, not a better
weight vector or more portfolio machinery. XS cross-sectional momentum is the deploy
core (+1.375, confirmed alpha-not-beta, persistent); trend (+0.36) is too weak to lift
a blend with it. The IDM combine layer is built and waiting for a third return stream.

**Carry** (perp funding) is the natural next crypto structural edge:

- It is *mechanically* a different signal from price-trend momentum (funding level vs
  price path), so it is a strong candidate to **diversify** the XS-solo core.
- We already ingest `funding_rates` — no new data, free-first. Coverage is rich: all 25
  universe perps, ~7k funding rows each (137k total), funding persistently positive
  (≈ +14% annualized for majors → being short *earns* carry), and **cross-sectional
  dispersion in annualized funding spans −17% to +15%** — a real relative-carry signal,
  not a dry well.

This sleeve expresses funding carry as a Carver-style vol-scaled forecast, gates it on
the same de-biased bar as every other sleeve, and (if it clears at a comparable Sharpe)
becomes the third stream the combine layer can blend. If sub-bar, it joins trend on the
shelf and XS-solo remains the core.

## 2. Goal + success metric (anti-drift)

**Goal:** measure whether a crypto funding-carry sleeve is a deployment-grade,
diversifying edge.

**Success metric (the de-biased gate, identical to prior sleeves):**

> DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ block-bootstrap CI lower bound > 0

over the honest multiple-testing trial family, plus a **diversification read**
(`corr_to_xs`, `corr_to_trend`) against the deploy core. A negative/sub-bar verdict is a
**result, not a failure** — report it honestly and shelf the sleeve.

## 3. Non-goals (YAGNI / scope fence)

- **No combine extension.** The IDM combine currently blends exactly two streams
  (`xs_result`, `trend_result`). Generalizing it to N streams (XS + carry, or
  XS + trend + carry) is a **follow-up gated on carry clearing the gate**, not part of
  this work.
- **No live wiring, no schema change, no golden change.** Pure, read-only replay over
  `analytics.db` (`duckdb.connect(..., read_only=True)`). Additive package; regression
  goldens must stay UNMOVED.
- **No new data ingestion.** Funding already in `funding_rates`. (Survivorship of dead
  names stays a pre-capital rigor audit, unchanged — out of scope here.)
- **No crypto-fitting of the carry scalar** (see §5.3) — that would be the exact
  in-sample overfitting the gate exists to catch.

## 4. Construction decisions (locked)

1. **Forecast = risk-adjusted annualized funding (Carver-canonical carry).** Funding is
   the perp's basis / cost-of-carry, so this is the literal carry signal. Vol-adjust
   (÷ annualized price-return vol) keeps Carver risk-parity across coins and makes the
   forecast magnitude comparable to the EWMAC forecasts. *Rejected:* funding-minus-EWMA
   (that is funding-*momentum*, would correlate with the momentum sleeves and defeat the
   diversification purpose); raw funding with no vol-adjust (breaks risk-parity).
2. **Sign:** carry forecast = **−annualized_funding / vol_ann** → **long when funding < 0**
   (it pays you to hold long), short when funding > 0.
3. **Expression: build BOTH, headline cross-sectional.** The carry forecast is shared;
   it is run through both an absolute book (mirrors `run_forecast_backtest`) and a
   cross-sectional demeaned book (mirrors `run_xs_backtest`). Headline = cross-sectional
   (the breadth thesis: XS cleared, absolute trend did not; and cross-sectional carry is
   the better diversifier — short-high-funding points opposite to long-recent-winners).
   Absolute carry is the standard contrast table (directional-vs-relative), the same
   shape as the universe-vs-majors / original-vs-dollar-neutral contrasts in prior audits.
4. **Multi-span smoothing family = the carry analog of the EWMAC speeds.** Raw daily
   funding is denoised with an EWMA over a small family of spans (days); each span is a
   single-span carry forecast, combined equal-weight × FDM and re-capped — exactly
   mirroring `combine_forecasts`. The per-span books + combined are the **DSR/PBO trial
   family** and answer "does the edge live at the instantaneous funding or the persistent
   level?" (the carry analog of trend's "fast-only carries it").
5. **Causality** is identical to the other two sleeves and is the load-bearing invariant
   (§6).

## 5. Architecture

New pure package **`analytics/carry/`** (read-only, additive, default-off). Reuses
`analytics.forecast` (`ForecastConfig`, `vol.ew_return_vol`), `analytics.forecast.replay`
(`load_daily_inputs` — already returns both closes *and* fundings),
`analytics.research_guards`, and `portfolio.metrics` unchanged. No schema / golden change.

### 5.1 `config.py` — `CarryConfig`

Frozen dataclass, composes a `ForecastConfig` for the shared honest-cost / vol / governor
constants (mirrors how `combine.CombineConfig` holds `sleeve_cfg`). Carry-specific knobs:

```text
@dataclass(frozen=True)
class CarryConfig:
    sleeve_cfg: ForecastConfig = ForecastConfig()   # fee/slip/vol_span/cap/governor/
                                                     # vol_target/annualization_days
    carry_spans: tuple[int, ...] = (1, 5, 20, 60)    # EWMA smoothing spans (days)
    carry_scalar: float = 30.0                       # FIXED a-priori (NOT crypto-fit); §5.3
    fdm: float = 1.25                                # forecast diversification multiplier
    cross_sectional: bool = True                     # headline = cross-sectional

    @property
    def annualization_days(self) -> float: return self.sleeve_cfg.annualization_days
    # ... thin pass-throughs for cap, vol_span, vol_target_annual, fee_pct, slippage_pct,
    #     gov_window, g_min, g_max as needed by book.py

    @classmethod
    def from_toml(cls, path) -> CarryConfig:
        # delegates to ForecastConfig.from_toml for the [backtest] costs
        return cls(sleeve_cfg=ForecastConfig.from_toml(path))
```

`__post_init__` validates `carry_spans` non-empty and all ≥ 1.

> **Annualization note.** `load_daily_inputs` already sums the day's ~3 funding rows into
> one daily figure. So annualized funding = `daily_funding_sum × annualization_days`
> (365), **not** `× 3 × 365` — the 3/day is already inside the daily sum.

### 5.2 `forecast.py` — the one genuinely new piece

Pure functions over `(close, funding_daily)` Series (no DB, no IO). `funding_daily` is the
day-indexed summed funding aligned to the close index (0.0 where missing), exactly what
`load_daily_inputs` returns.

```text
annualized_funding(funding_daily, span, ann_days) -> Series
    # EWMA(funding_daily, span=span, adjust=False).mean() * ann_days   (causal)

scaled_carry_forecast(close, funding_daily, span, scalar, vol_span, cap, ann_days) -> Series
    # ann_f   = annualized_funding(funding_daily, span, ann_days)
    # vol_ann = ew_return_vol(close, vol_span) * sqrt(ann_days)         (causal, shift baked in)
    # carry_adj = (-ann_f) / vol_ann            # long when funding<0; Sharpe-like
    # return (carry_adj * scalar).clip(-cap, +cap)

combine_carry_forecasts(close, funding_daily, spans, scalar, fdm, vol_span, cap, ann_days) -> Series
    # mean over per-span scaled_carry_forecast × fdm, re-capped ±cap
    # (mirrors analytics.forecast.ewmac.combine_forecasts; equal-weight)
```

All causal: EWMA and `ew_return_vol` use only data through each day; the `.shift(1)` that
makes the position causal happens in `book.py`, after the (cross-sectional) reduction.

### 5.3 The `carry_scalar` is fixed a-priori (anti-overfitting)

Carver picks the forecast scalar so the long-run average absolute forecast ≈ 10. **We do
NOT estimate it on crypto data** — that would be in-sample fitting. Two facts make a fixed
value safe:

- Each book is **vol-governed to a 20% target**, so the governor renormalizes the absolute
  level of a standalone book. The scalar's only real effects are (a) how often the ±cap
  binds and (b) relative span weighting — and under equal-weight combine the shared scalar
  cancels across spans.
- We therefore fix `carry_scalar = 30.0` (Carver's typical carry-scalar order of magnitude)
  and **report a scalar-sensitivity row** in the audit (like the cost-sensitivity row) to
  demonstrate the verdict does not hinge on the un-fit value.

### 5.4 `book.py`

```text
@dataclass(frozen=True)
class CarryBookResult:                 # SAME shape as ForecastBookResult / XSBookResult
    daily_index: pd.DatetimeIndex
    portfolio_return: np.ndarray       # net, post-governor (NaN-free; warm-up = 0.0)
    pre_governor_return: np.ndarray
    governor: np.ndarray
    active_count: np.ndarray
    per_instrument_net: dict[str, pd.Series]

carry_forecast_matrix(closes, fundings, cfg) -> pd.DataFrame
    # per-instrument combine_carry_forecasts, reindexed to the sorted union index
    # (NaN warm-up preserved, like xs_forecasts)

run_carry_backtest(closes, fundings, cfg) -> CarryBookResult
    # f = carry_forecast_matrix(...)
    # if cfg.cross_sectional:
    #     f = f.sub(f.mean(axis=1), axis=0)          # demean over active set (skipna)
    # f_shifted = f.shift(1)                         # CAUSAL: position on d uses info ≤ d-1
    # per instrument: leverage = (f_shifted[sym]/10) * (vol_target / vol_ann[sym])
    #                 gross    = leverage * close.pct_change()
    #                 turnover = |Δleverage| * (fee+slip)
    #                 funding  = leverage * funding_daily   (shorts receive funding)
    #                 net      = gross - turnover - funding
    # aggregate: pre = net_mat.sum(axis=1) if cross_sectional else net_mat.mean(axis=1)
    # governor: trailing realized vol of `pre`, rolling(gov_window).std().shift(1) * sqrt(ann),
    #           g = (vol_target / trailing_vol).clip(g_min, g_max); port = g.fillna(0)*pre

equity_curve(result) -> pd.Series                    # (1+r).cumprod()
```

`run_carry_backtest` is a single entry that branches on `cfg.cross_sectional` for (demean,
sum-vs-mean). This is intentional mild duplication of the two existing book templates,
chosen for isolation (additive, goldens unmoved) over refactoring the shared books.

### 5.5 `replay.py` — read-only DB front door

```text
replay_carry(conn, cfg, symbols=None) -> CarryBookResult
    # syms = symbols or load_universe(); closes,fundings = load_daily_inputs(conn,syms)
    # return run_carry_backtest(closes, fundings, cfg)

replay_carry_trials(conn, cfg, symbols=None) -> dict[str, np.ndarray]
    # per single-span book (dataclasses.replace(cfg, carry_spans=(s,))) + "combined"
    # all under cfg.cross_sectional — the DSR/PBO family for the headline book
```

Reuses `analytics.forecast.replay.load_daily_inputs` (no duplicate DB code).

### 5.6 `report.py`

```text
@dataclass(frozen=True)
class CarryReport:
    sharpe_annual sortino_annual max_dd calmar annual_return annual_vol n_obs
    dsr pbo boot_lo boot_hi min_trl
    corr_to_xs xs_sharpe          # diversification vs the DEPLOY CORE (primary)
    corr_to_trend trend_sharpe    # secondary

evaluate_carry(result, cfg, trial_returns, xs_returns, trend_returns) -> CarryReport
    # mirrors analytics.xsmom.report.evaluate_xs; adds corr_to_xs / xs_sharpe
    # DSR over {per-span ∪ combined}; PBO/CSCV over the same; boot CI on the headline book;
    # MinTRL vs target_sr = 1/sqrt(ann)

carry_gate_verdict(report) -> bool
    # report.dsr >= 0.95 and report.pbo <= 0.5 and report.boot_lo > 0.0
```

`_per_period_sharpe` / `_ann_sharpe` / `_aligned_corr` are copied from the xsmom report
(same definitions; the joint-dead-warmup `(0,0)` exclusion in `_aligned_corr` matters).

### 5.7 `__init__.py`

Eager re-exports: `CarryConfig`, `CarryBookResult`, `run_carry_backtest`,
`carry_forecast_matrix`, `equity_curve`, `replay_carry`, `replay_carry_trials`,
`CarryReport`, `evaluate_carry`, `carry_gate_verdict`,
`annualized_funding`, `scaled_carry_forecast`, `combine_carry_forecasts`.

### 5.8 Driver — `tools/carry_audit.py` + `make buibui-carry-audit`

Read-only (`duckdb.connect("analytics.db", read_only=True)`). Replays carry over the N3
universe (1d) and prints, each with DSR/PBO/bootstrap-CI/MinTRL stamps where applicable:

1. **Headline cross-sectional carry gate** — over the per-span + combined family; plus
   `corr_to_xs` / `xs_sharpe` and `corr_to_trend` / `trend_sharpe` (the diversification
   read against the deploy core). Prints `GATE: CLEAR / FAIL`.
2. **Absolute-vs-cross-sectional contrast** — Sharpe of each expression.
3. **Breadth contrast** — universe vs majors (BTC/ETH/... the same majors set the other
   audits use).
4. **Cost sensitivity** — 0 / 2 / 8 / 16 bps per leg.
5. **Per-span Sharpe** — which smoothing horizon carries the edge.
6. **Scalar sensitivity** — carry_scalar ∈ {15, 30, 60} (robustness to the un-fit scalar).

The XS and trend daily returns on the same universe/window are computed via the existing
`xsmom.replay.replay_xs` / `forecast.replay.replay_universe` for the corr reads.

## 6. Causality — the load-bearing invariant

Funding for day `d−1` is fully known by its close (the last 8h funding settles 16:00 UTC
on `d−1`); `load_daily_inputs` groups funding by UTC date. The carry forecast for the
position held on day `d` must use funding+price through `d−1` only:

- `annualized_funding` uses a causal EWMA (`adjust=False`, no forward window).
- `vol_ann` uses `ew_return_vol` (the `.shift(1)` is already baked in).
- The cross-sectional demean is a **same-day** reduction over causal forecasts; the
  `.shift(1)` is applied **AFTER** demeaning, **BEFORE** sizing (identical to xsmom).
- The governor uses trailing realized vol `.rolling(...).std().shift(1)`.

**Test obligation (non-vacuous):** a perturbation test bumps a **future** funding (and a
future close) value and asserts today's leverage **and** governor are unchanged; the same
test verified RED (fails) when the `.shift(1)` is removed. For the cross-sectional book,
the perturbation is applied to one instrument and the no-look-ahead assertion holds across
the coupled (demeaned) instruments.

## 7. Testing plan (TDD)

New test modules, all using `duckdb.connect(":memory:")` for any DB touch and `MagicMock`
for no real network:

- `tests/test_carry_forecast.py` — `annualized_funding` value + causality; `scaled_carry_forecast`
  **sign** (funding<0 → forecast>0 → long), cap clamp, vol-adjust; `combine_carry_forecasts`
  equal-weight + FDM + re-cap.
- `tests/test_carry_book.py` — absolute + cross-sectional modes; governor clip; turnover &
  **funding sign** (shorts receive funding → reduces cost); `pre = sum` (XS) vs `mean`
  (absolute); the **causality perturbation** test (future bump → today's leverage+governor
  unchanged; RED-without-shift); demean active-set skipna (NaN warm-up excluded);
  `equity_curve` starts at 1.0.
- `tests/test_carry_replay.py` — in-memory DB seeded with a few symbols' 1d OHLCV + funding;
  `replay_carry` shape; `replay_carry_trials` keys = per-span + "combined".
- `tests/test_carry_report.py` — `evaluate_carry` field plumbing; `carry_gate_verdict`
  boundary (0.95 / 0.5 / 0.0); `corr_to_xs` excludes joint-dead `(0,0)`.

## 8. Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green (new tests pass)
- `make test-regression` — goldens **UNMOVED** (additive read-only package; if they move,
  something leaked into a shared path — investigate, do not regenerate).
- `make buibui-carry-audit` runs end-to-end read-only and produces the six tables.
- Verdict written to `docs/audits/2026-06-19-p3-carry-sleeve.md` (CLEAR or FAIL, honestly).
- CLAUDE.md (`analytics/carry/` section + `tools/` row + Makefile target) and MEMORY.md
  Current State updated. Handoff prompt refreshed.

## 9. Decision after the verdict

- **Clears the gate at a comparable Sharpe** (near XS's strength) and diversifies XS
  (`corr_to_xs` low/negative) → **graduate to the combine** (follow-up: generalize the IDM
  layer to a third stream; XS-heavy + carry, or XS + trend + carry).
- **Sub-bar** → shelf alongside trend; **XS-solo remains the deploy core**. Record which
  expression/span looked best for a future OOS re-test. A negative result still retires the
  "carry is the obvious second edge" hypothesis cheaply — that is the point of the gate.
