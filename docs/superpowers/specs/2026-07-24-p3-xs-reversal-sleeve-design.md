# P3 Cross-Sectional Short-Horizon Reversal Sleeve — Design (2026-07-24)

**Thesis.** Short-horizon (2–7 day) cross-sectional reversal as a candidate *second
strong edge* — decorrelated from, and additive to, the deployed XS-solo momentum core.
It reuses the XS harness wholesale: only the forecast changes (a sign-flipped
short-horizon return in place of the EWMAC momentum forecast); the cross-sectional
demean, vol-parity leverage, honest-cost book, and 20%-vol governor are shared verbatim.

Spec sibling to `docs/superpowers/specs/2026-06-16-p3-cross-sectional-momentum-sleeve-design.md`
and `docs/superpowers/specs/2026-06-19-p3-carry-sleeve-design.md`.

## 1. Context & motivation

- **The binding constraint, confirmed four times** (exits, trend-weight study, the
  combine layer, funding carry): the system needs a second *strong* edge. XS-solo
  (cross-sectional momentum, +1.375 Sharpe) is the only sleeve that clears the de-biased
  gate; trend (+0.36) and carry (+0.03) are shelved.
- **Basis was the assumed next candidate, and it is largely dead-on-arrival.** On a
  perpetual, funding *is* the spot-perp basis (arbitrage tethers them), and the funding
  carry sleeve already FAILED the gate (cross-sectional Sharpe +0.034, DSR 0.35, PBO 0.78,
  gone by 8 bps of cost). Term-structure carry (dated-futures curve slope) carries
  genuinely new information but is liquid only for BTC/ETH + a handful → **majors-only**,
  the wrong breadth direction (XS's edge is *alt* breadth), and needs roll-stitching of
  data we do not ingest. So the cheapest strong-prior second-edge candidate is not basis.
- **Short-horizon reversal is that candidate.** Short-term reversal / overreaction is one
  of the more robust cross-sectional anomalies in crypto (illiquidity + retail
  overreaction). It sits at a horizon (2–7 days) *below* XS's fastest EWMAC speed
  (8/32-day), so it is plausibly *additive* rather than merely anti-XS. It is
  cross-sectional over the full 25-alt universe (right breadth), decorrelated-to-
  anti-correlated with momentum by construction, uses only data we already hold deeply
  (OHLCV + funding, 2,477 days), and reuses the validated XS book.
- **Honest strategic caveat (kept throughout).** XS-solo already clears the gate and is
  not yet deployed, so a second edge *refines* a blend XS already dominates — it does not
  *unlock* revenue; deployment does. This sleeve is the best **research** use of time given
  deployment is out of scope for this work, not a claim that it is the highest-value move
  overall.

## 2. Goal & success metric (north star — anti-drift)

The one-line success metric: **a standalone short-horizon cross-sectional reversal book
that clears the de-biased gate net of honest costs and is cost-robust.**

- **Primary gate** (same as every sleeve): `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ block-bootstrap-CI
  lower bound > 0`, at honest 2 bps/leg, **and still positive at 8 bps** (cost-robustness —
  the specific bar funding carry failed).
- **Combine value** (secondary, reported not gated here): `corr_to_xs` low or negative
  **and** standalone Sharpe ≳ 0.9 — the carry-verdict threshold for a second edge to
  actually lift the XS-dominated blend.
- **Verdict taxonomy:** **BUILD** (clears gate + additive to XS) / **SHELF** (real but
  sub-bar or not cost-robust — demote, keep as a validated template, like carry) /
  **REDUNDANT** (essentially −XS: very negative `corr_to_xs` *and* fails standalone) /
  **INSUFFICIENT** (underpowered).
- **Pre-registered kill conditions:** (1) if the edge exists only at k=1 / no-skip and
  dies by 8 bps, it is bid-ask microstructure, not alpha → FAIL; (2) if `corr_to_xs` is
  strongly negative *and* the standalone book fails, it is the negative of XS, not a new
  edge → REDUNDANT.

## 3. Non-goals (YAGNI)

- No new data ingestion — closes + funding are already deep; nothing to backfill.
- No live wiring / target book / execution — that is a separately-gated follow-up on a
  BUILD verdict, exactly as XS-solo was.
- No combine/IDM integration in this spec — we only *measure* `corr_to_xs` and report it;
  the actual trend/XS/reversal blend is a later effort if BUILD.
- OI-positioning is **descriptive-only** (see §8), not a gated deliverable.
- Detectors stay frozen — this is a forecast sleeve, not a new TA detector.

## 4. Signal construction (the reversal forecast)

Mirror XS's vol-scaled forecast, sign-flipped. Per instrument `i`, day `d`, formation
window `k` (days):

- `k`-day return: `r_k,i(d) = close_i(d) / close_i(d − k) − 1` (causal — closes through `d`).
- daily return vol: `σ_i(d) = ew_return_vol(close_i, vol_span)` (reused from
  `analytics/forecast/vol.py`; causal, `.shift` baked in).
- horizon-normalized z-score: `z_k,i(d) = r_k,i(d) / (σ_i(d) · √k)` — the `√k` keeps
  different windows on the same footing so an equal-weight combine is sensible.
- single-window forecast: `f_k,i(d) = clip(−scalar · z_k,i(d), ±cap)` — the **minus** is
  the reversal (short recent winners, long recent losers).
- combined forecast: `f_i(d) = clip(fdm · mean_k f_k,i(d), ±cap)` over
  `k ∈ {2, 3, 5, 7}`.
- cross-sectional demean (relative reversal, dollar-neutral): `g_i(d) = f_i(d) −
  mean_{j ∈ active(d)} f_j(d)` — row mean skips NaN so only warmed-up instruments
  contribute (identical to `xs_demeaned_forecasts`).
- leverage: `.shift(1)` then vol-parity `(g/10) · (vol_target / vol_ann)` (identical to
  `xs_leverage`).
- book: the shared honest-cost + 20%-vol-governor book (identical to `run_xs_backtest`).

**k=1 is excluded from the headline family** and reported only as a bid-ask-bounce
diagnostic (see §7).

## 5. Architecture

**Enabling refactor (additive, default-off, regression-golden byte-identical).** The XS
pipeline is `xs_forecasts` (EWMAC momentum) → `xs_demeaned_forecasts` → `xs_leverage`
(shift + vol-parity) → `run_xs_backtest` (book + honest costs + governor). Only the first
step is signal-specific. Make the forecast **injectable**: add an optional keyword-only
`forecasts: pd.DataFrame | None = None` threaded through `xs_demeaned_forecasts`,
`xs_leverage`, and `run_xs_backtest`; when `None`, the chain computes the EWMAC path as
today (byte-identical, guarded by the existing regression goldens + a new equivalence
test). This is the same additive/default-off shape the repo already used for the
`turnover_cost_rate` kwarg, so it does not destabilize the live-wired XS-solo deploy core.

**New package `analytics/xsrev/`** (sibling to `xsmom/`, `carry/`, `combine/`):

| Module | Responsibility |
| --- | --- |
| `config.py` | `ReversalConfig` (frozen; composes `sleeve_cfg: ForecastConfig` for the shared fee/slippage/vol/governor constants — mirrors `CarryConfig`; `formation_windows=(2,3,5,7)`; a-priori `reversal_scalar=10.0`; `fdm=1.25`; `cap`/`vol_target`/`vol_span` from `sleeve_cfg`; `cross_sectional=True`; `from_toml` defers to `ForecastConfig.from_toml`) |
| `forecast.py` | `reversal_forecast_matrix(closes, cfg)` → per-instrument forecast DataFrame aligned to the union daily index, NaN warmup **preserved** (same shape as `xs_forecasts`, so the shared demean/leverage consume it unchanged). Pure, causal |
| `book.py` | `run_xsrev_backtest(closes, fundings, cfg)` — build the reversal forecast, call the shared book via `run_xs_backtest(..., forecasts=matrix)` with `cfg.sleeve_cfg` supplying the sizing/cost constants. Returns the reused `XSBookResult` |
| `replay.py` | `replay_xsrev` / `replay_xsrev_trials` — read-only DB front door reusing `analytics/forecast/replay.load_daily_inputs`; trials = per-`k` books + combined = the DSR/PBO family |
| `report.py` | `evaluate_xsrev` — reuse/wrap `evaluate_xs`: headline + DSR/PBO/block-bootstrap-CI/MinTRL over the `k` family, plus `corr_to_xs`, `xs_sharpe` contrast, per-year persistence, and beta-attribution (reuse `xsmom/diagnostics.py`) |

**Driver.** `tools/xsrev_audit.py` + `make buibui-xsrev-audit`; read-only over
`analytics.db`. Prints: breadth contrast (universe vs majors), cost sensitivity
(0/2/8/16 bps/leg), per-`k` Sharpe, `corr_to_xs`, the k=1 contamination diagnostic, a
scalar-sensitivity table, and the descriptive OI panel (§8).

## 6. Data

Everything is already in `analytics.db`, no backfill:

- Perp OHLCV (1d) — 25 perps, 2,477 days (2019-09 → now). **Deep.**
- Funding (8h, summed to daily by `load_daily_inputs`) — 25 perps, deep. Used only by the
  book's funding-cost accrual (the reversal forecast itself does not read funding).
- Open interest — **shallow** (2026-02-28 → 2026-07-23; ~144 days majors, ~30–60 rest).
  Used only by the descriptive OI panel (§8), never by the gated reversal book.

## 7. Costs & the make-or-break risk

Reversal's Achilles heel is the exact thing that killed funding carry: it churns fast
(daily rank rotation → high turnover → cost-sensitive), and a naive 1-day close-to-close
reversal is often pure **bid-ask bounce** (a close that printed on the bid mechanically
"reverses" up next bar) that evaporates at realistic cost. The design confronts this
head-on:

- **k ≥ 2 in the headline family** — the shortest, most bounce-contaminated window (k=1) is
  demoted to a reported diagnostic.
- **The 0/2/8/16 bps/leg cost sweep is the verdict-decider**, not raw Sharpe.
- **Reuse the size-aware capacity/execution stress** (`analytics/xsmom/execution.py`,
  already built) — reversal turns over faster than momentum, so report the capacity
  ceiling at operator scale.

Honest costs are already in `run_xs_backtest`: turnover `|Δlev|·(fee+slip)` + funding
accrual (shorts receive). No change needed.

## 8. OI-positioning comparison arm (DESCRIPTIVE ONLY — data-limited)

**Finding.** `open_interest` spans only 2026-02-28 → 2026-07-23 (~144 days majors, ~30–60
rest, hourly) — Binance's recent-only OI limitation. That is far short of MinTRL for a
de-biased verdict, and gating a 3-name / 144-day book would be exactly the underpowered
reading the working agreement forbids.

**Therefore the OI arm is not a gated deliverable.** It is included as an explicitly
descriptive exploratory panel in the audit tool: the pre-registered construction

```text
crowding_i(d) = − sign(EWMA funding_i(d)) · zscore(ΔOI_i(d) / OI_i(d − 1))
```

(fade the side the crowd is *building* into — funding sign says which side is crowded, ΔOI
magnitude says how fast), run through the same book over the shallow overlap, reporting
standalone Sharpe + `corr_to_xs` + `corr_to_reversal` **with a loud
"UNDERPOWERED — needs OI backfill" caveat and no BUILD/SHELF verdict.**

A rigorous OI arm is gated on deeper OI history, which is free-data-blocked (Binance free ≈
recent-only; deep OI = CoinGlass ~$29/mo, not justified pre-gate per the data-cost policy).
Filed as a future item.

## 9. Causality / no-look-ahead invariant (load-bearing)

The position held during day `d` is sized only from information through the close of
`d − 1`: the forecast and vol use closes ≤ `d`, then `.shift(1)` before sizing (identical
to XS). A **cross-sectional perturbation test** asserts that bumping `close_i(d)` leaves
day-`d` leverage unchanged across the coupled cross-section (RED without the shift) —
mirrors the xsmom causality test. This is the primary correctness guard.

## 10. Testing & Definition of Done

- TDD; pure / causal / read-only; additive / default-off.
- The xsmom injectable-forecast refactor is **byte-identical** with `forecasts=None` —
  guarded by the existing regression goldens *and* a new equivalence test asserting
  `run_xs_backtest(..., forecasts=None) == run_xs_backtest(...)`.
- `make lint-py` ✓ (ruff), `make typecheck` ✓ (mypy strict), `make test` green,
  `make test-regression` goldens unmoved.
- Unit tests: causality/perturbation (§9); NaN-warmup active-set demean; k=1 excluded from
  the headline; `√k` normalization; cost-monotonicity sanity; the reversal sign
  (positive-momentum instrument → negative forecast).

## 11. Deliverables

- `analytics/xsrev/` package (config / forecast / book / replay / report) + the xsmom
  injectable-forecast refactor.
- `tools/xsrev_audit.py` + `make buibui-xsrev-audit`.
- Verdict doc `docs/audits/2026-07-24-p3-xs-reversal-sleeve.md` (BUILD / SHELF / REDUNDANT /
  INSUFFICIENT, with a plain-English glossary per the operator preference:
  headline → metric → money → backtest-vs-live).
- SoT (`project_todo_master`) + MEMORY updates.

## 12. Reproduce

```bash
make buibui-xsrev-audit        # read-only over analytics.db
```

## 13. Decisions locked

- `formation_windows = (2, 3, 5, 7)`; k=1 diagnostic-only.
- `reversal_scalar = 10.0`, a-priori / governor-normalized (an average `|z| ≈ 1` maps to a
  forecast ≈ ±10 under the ±20 cap — the standard Carver magnitude, **not** crypto-fit; the
  standalone book is vol-governed so its Sharpe is scalar-insensitive; a scalar-sensitivity
  table is reported for honesty, as carry did).
- OI arm descriptive-only (data-limited); no gated verdict.
- Reuse the xsmom book/governor via an additive, default-off `forecasts=` injection;
  goldens byte-identical.
