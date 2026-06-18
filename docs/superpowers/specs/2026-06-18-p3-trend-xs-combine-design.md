# P3 trend×XS combine layer — IDM portfolio-construction design

**Date:** 2026-06-18
**Status:** design (pre-implementation)
**Spec author:** brainstormed with the user (senior quant) on 2026-06-18
**Sub-project:** the IDM / correlation portfolio-combine layer (Carver L6–L7)

## 1. Context & goal

Two sleeves are now validated as separate research books:

- **XS cross-sectional momentum** (`analytics/xsmom/`): universe Sharpe **+1.375**,
  DSR 0.997, PBO 0.295, boot CI [+0.60, +2.11], confirmed **alpha-not-beta** and
  **persistent** (`docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md`).
  The first gate-clearing sleeve in the program — the positive-EV core.
- **EWMAC trend** (`analytics/forecast/`): universe Sharpe **+0.36**, sub-bar but a
  real, structural, cost-robust positive (`docs/audits/2026-06-15-p2-ewmac-trend-g2.md`).
  A diversifier, not a core.

`corr_to_trend ≈ +0.37`. They are run as two disconnected books. This sub-project
builds the **portfolio-construction layer** that combines them into **one sized
book** using a Carver Instrument Diversification Multiplier (IDM) derived from the
sleeve correlation.

**Goal (one line, the success metric):** a read-only, additive, default-off
combine layer that produces one IDM-scaled combined book from the two validated
post-governor sleeve return streams, and reports whether the combined book clears
**DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0** over the {trend, XS, combined} family —
i.e. *does diversifying between the two validated sleeves beat each alone, de-biased?*

**What this buys the end goal:** the first artifact that is a deployable *product*
(one sized book, one risk budget) rather than a *finding*; a Sharpe lift above the
best single sleeve if diversification pays (the one free lunch); a clean
deploy-or-retire decision on the sub-bar trend sleeve; and the reusable socket that
every future sleeve (carry/basis) plugs into.

## 2. Decisions (settled in the brainstorm)

| Question | Decision | Why |
| --- | --- | --- |
| **Combine space** | **Book-return space** (combine the two validated post-governor return streams), NOT forecast space | Inherits the validation already paid for (the exact streams were stress-tested); textbook IDM math; clean sleeve attribution; lowest risk; gate family = exactly the 3 arrays. Forecast-space netting (cheaper, more deployable) is a **deferred refinement** gated on the cost evidence (see §7). |
| **IDM estimation** | **Causal-rolling** correlation → IDM (headline); **static full-sample reported as sensitivity** | Preserves the no-look-ahead invariant every sleeve validation rested on. Noise cost is small (2 sleeves, ρ≈0.37 ⇒ IDM≈1.2, insensitive to ρ wobble). |
| **Sleeve weights** | **Equal-risk 0.5/0.5** (headline); XS-heavy + Sharpe-proportional **reported as sensitivity, not gate-selected** | Both streams already ~20% vol ⇒ equal weight ≈ equal risk. Keeps the gate family honestly {trend, XS, combined} (no selection haircut). Mirrors the forecast-weight-study precedent (PR #443): ship the a-priori default, report the snooped tilts, let the gate decide. |
| **Final governor** | **Apply** a causal 20%-vol governor on top of IDM (toggle `apply_governor=True`) | Carver: IDM is the structural scale-up for correlation diversification; the governor is the realized-vol feedback trim. They target the same 20% from different angles, so the governor stays near 1.0 and keeps the combined book directly comparable (~20% vol) to each sleeve. |

## 3. Architecture

New package `analytics/combine/`, mirroring `forecast/` and `xsmom/`:

```text
analytics/combine/
  config.py    CombineConfig (frozen): sleeve_cfg + combine knobs
  idm.py       pure IDM math (no DB/IO)
  book.py      combine_books(...) -> CombinedBookResult + equity_curve
  replay.py    read-only DuckDB front door (the only DB-touching module)
  report.py    evaluate_combined(...) -> CombineReport + combine_gate_verdict
  __init__.py  eager re-exports
tools/combine_audit.py        driver (+ make buibui-combine-audit)
```

### 3.1 `config.py` — `CombineConfig`

Frozen dataclass:

- `sleeve_cfg: ForecastConfig = ForecastConfig()` — one shared config feeds **both**
  sleeves so fees / speeds / governor-constants match. `xs_dollar_neutral=False`
  (the validated +1.375 original; the trend sleeve ignores the flag).
- `w_xs: float = 0.5`, `w_trend: float = 0.5` — sleeve weights.
- `idm_mode: str = "causal"` — `"causal"` | `"static"`.
- `idm_window: int = 365` — trailing-correlation window (days).
- `idm_min_periods: int = 120` — joint-live obs required before IDM ≠ 1.0.
- `idm_cap: float = 2.5` — Carver cap (won't bind with 2 sleeves).
- `apply_governor: bool = True` — final causal vol governor toggle.

Governor constants (`vol_target_annual`, `gov_window`, `g_min`, `g_max`,
`annualization_days`) are read from `sleeve_cfg`. Optional `from_toml` defers to
`ForecastConfig.from_toml` for the shared honest-cost values.

### 3.2 `idm.py` — pure IDM math

- `idm_value(w_xs, w_trend, corr, cap) -> float` = `1/√(wᵀ ρ w)` capped at `cap`.
  Known cases: ρ=0, equal weights ⇒ 1/√0.5 ≈ 1.414; ρ=1 ⇒ 1.0; degenerate
  (negative-definite / zero denominator) ⇒ cap.
- `static_idm(r_xs, r_trend, w_xs, w_trend, cap) -> float` — full-sample corr over
  the joint-live tail (exclude joint-zero warm-up) → `idm_value`.
- `causal_idm_series(r_xs, r_trend, w_xs, w_trend, window, min_periods, cap) -> pd.Series`
  — trailing-window rolling corr (over joint-live data), `idm_value` per day,
  `shift(1)`, neutral `1.0` before `min_periods`. The series is the per-day IDM
  applied to that day's combined return.

### 3.3 `book.py` — `combine_books`

Both sleeve `portfolio_return` arrays are already causal (post-governor, NaN-free,
warm-up = 0.0) and share an identical `daily_index` (same `load_daily_inputs` /
universe). `combine_books(xs_result, trend_result, cfg)` aligns them on the shared
index (defensively, on the explicit `DatetimeIndex`, not positionally) and applies
three causal steps:

```text
1. weight   pre_d   = w_xs·r_xs,d + w_trend·r_trend,d        (same-day; both r causal)
2. IDM      idm_d   = idm_value(w, ρ_{≤d-1})  capped          (causal_idm_series, shift(1))
            (static mode: idm_d ≡ static_idm, a constant)
3. governor g_d     = clip(vol_target / trailing_vol(pre·idm)_{≤d-1})   (sleeve pattern)
            port_d  = g_d · idm_d · pre_d        (g_d ≡ 1 when apply_governor=False)
```

`CombinedBookResult` (frozen): `daily_index, portfolio_return (post-IDM,
post-governor, NaN-free), pre_idm_return, idm, governor, xs_return_aligned,
trend_return_aligned`. `equity_curve(result)` = `(1+r).cumprod()`.

**Causality invariant (load-bearing).** `port_t` depends only on same-day returns
`r_*,t` (the weighting) and correlation / trailing-vol through `t-1` (IDM /
governor). A perturbation test perturbs a future return `r_*,T` and asserts every
`port_t` for `t<T` is byte-identical — RED without the `shift(1)` on both the IDM
series and the governor. Mirrors the xsmom / forecast middle-bar perturbation tests.

### 3.4 `replay.py` — read-only DB front door

- `load_sleeves(conn, cfg, symbols=None) -> tuple[XSBookResult, ForecastBookResult]`
  — runs both sleeves **once** (read-only). XS via `run_xs_backtest`, trend via
  `run_forecast_backtest`, over the shared `load_daily_inputs(conn, syms)`.
- `replay_combined(conn, cfg, symbols=None) -> CombinedBookResult` —
  `load_sleeves` then `combine_books`.
- `replay_combined_trials(conn, cfg, symbols=None) -> dict[str, np.ndarray]` —
  `{"trend", "xs", "combined"}`, the honest gate family for DSR/PBO.

The audit tool sweeps weight / IDM-mode / cost **variants in-memory** over the
once-loaded sleeve results (the combine math is pure over arrays) — it does not
re-hit the DB per variant. (Cost variants re-run the sleeves under a different
`slippage_pct`; that is a per-cost-point reload, matching the xsmom audit pattern.)

### 3.5 `report.py` — verdict + gate

`evaluate_combined(combined_result, cfg, trial_returns, xs_returns, trend_returns)
-> CombineReport`, mirroring `XSReport`:

- Headline `portfolio.metrics`: `sharpe_annual, sortino_annual, max_dd, calmar,
  annual_return, annual_vol, n_obs`.
- Guards over {trend, XS, combined}: `dsr, pbo, boot_lo, boot_hi, min_trl`
  (same machinery as `evaluate_xs`).
- Diversification reads: `corr_xs_trend, realized_idm (mean of the live IDM series),
  vol_xs, vol_trend, vol_combined_pre_idm, diversification_mult (weighted-avg sleeve
  vol ÷ combined pre-IDM vol), sharpe_xs, sharpe_trend, xs_contribution,
  trend_contribution (w_i·mean(r_i))`.

`combine_gate_verdict(report) -> bool` applies **DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧
boot_lo > 0**.

**Gate honesty.** The headline gate family is exactly {trend, XS, combined} — the
a-priori equal-weight / causal-IDM book, no selection. The sensitivity panel
(XS-heavy, Sharpe-prop, static-IDM, governor-off, cost-sweep) is *reported, not
gate-selected*. A wider-family DSR (incl. the variants) is reported as a stricter
robustness note, with the standard "any tilt is in-sample unless it clears a
selection-aware gate" caveat (PR #443 pattern).

### 3.6 `tools/combine_audit.py` (+ `make buibui-combine-audit`)

Read-only `duckdb.connect(..., read_only=True)`. Tables:

1. **Gate verdict** — {trend, XS, combined} metrics + DSR/PBO/boot/MinTRL + PASS/FAIL.
2. **Diversification read** — corr, realized IDM, vol-reduction, sleeve attribution.
3. **Weights sensitivity** — equal / XS-heavy (0.7/0.3) / Sharpe-prop → combined Sharpe + stamps.
4. **IDM-mode sensitivity** — causal vs static → combined Sharpe.
5. **Cost sensitivity** — 0/2/8/16 bps → combined Sharpe + the **combined cost-drag**
   check (the trigger-condition for a future forecast-space netting refinement).
6. **Breadth contrast** — universe vs majors (mirror the xsmom audit).

Verdict doc → `docs/audits/2026-06-18-p3-trend-xs-combine.md`.

## 4. Causality (no look-ahead) — the non-negotiable

The whole program's credibility rests on no look-ahead. The combine adds exactly one
new place a leak could enter (the IDM), closed by:

- causal-rolling correlation through `t-1` + `shift(1)`;
- the final governor's trailing vol through `t-1` + `shift(1)`;
- a perturbation test proving `port_{t<T}` is invariant to `r_*,T`.

Static-IDM mode is a *reported sensitivity only* and is explicitly a mild
full-sample leak — never the headline.

## 5. Testing (TDD)

- `idm.py`: `idm_value` known cases + cap + degenerate; `static_idm` joint-live tail;
  `causal_idm_series` warm-up neutral, shift, monotonic-with-corr.
- `book.py`: shapes, weighting correctness, IDM application, governor on/off,
  `equity_curve`; **causality perturbation test**; index-alignment when sleeve
  indices differ; degenerate (one sleeve empty / flat).
- `report.py`: all fields, `combine_gate_verdict`, corr / realized-IDM /
  vol-reduction, degenerate-safe (flat curves → 0.0 not NaN).
- `replay.py`: in-memory DuckDB over a tiny synthetic universe; `load_sleeves`,
  `replay_combined`, `replay_combined_trials` keys.

## 6. Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green.
- `make test-regression` goldens **UNMOVED** — guaranteed by construction: a new
  read-only package touching nothing in the backtest pipeline.
- A verdict doc with the honest call (combined beats best-single, or not).
- CLAUDE.md `analytics/combine/` section + `tools/combine_audit.py` row + Makefile
  target + README synced.

## 7. Out of scope (explicitly deferred)

- **Forecast-space netting refinement** — combine per-instrument forecasts into one
  position (nets offsetting trend/XS legs, pays turnover once). Cheaper / more
  deployable, but re-opens validation. Deferred and **gated on the cost evidence**:
  if the cost-sensitivity table shows double-counted turnover is binding on the
  verdict, that is the signal to build it.
- **Sleeve #3 (carry/basis)** — the combine layer is built to accept a third stream,
  but adding it is a separate sub-project.
- **Survivorship rigor audit** — unchanged from the XS verdict: demoted to a
  pre-capital check; the book is already point-in-time-correct via the NaN
  active-set demean.
- **Live deployment / execution realism at size** — this remains read-only replay.

## 8. Risks & mitigations

- *Double-counted turnover masks a real edge.* Unlikely (corr +0.37 ⇒ offsetting is
  not dominant; each sleeve is cost-robust to 16 bps alone). Mitigated by the
  cost-drag report (§3.6 table 5) which is the explicit trigger for §7's refinement.
- *Causal-rolling IDM noise.* Small (IDM≈1.2, insensitive to ρ near 0.37, long
  window). Static-IDM sensitivity column confirms robustness.
- *Multiple-testing inflation from the sensitivity panel.* Avoided by pre-committing
  to the equal-weight / causal-IDM book a-priori; variants are reported, not selected;
  wider-family DSR reported as the stricter note.
