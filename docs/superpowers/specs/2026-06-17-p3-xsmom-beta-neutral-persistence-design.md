# P3 XS-momentum — beta-neutralization + forward-persistence re-test

Date: 2026-06-17
Status: design (approved, pre-plan)
Scope: `analytics/xsmom/`, `analytics/forecast/config.py`, `tools/xsmom_audit.py`

## Goal (success metric)

Decide, cheaply and entirely in-code / in-data, whether the XS sleeve's
gate-clearing headline (universe Sharpe **+1.375**, DSR 0.997, PBO 0.295) is:

1. **real cross-sectional alpha** — survives stripping the ~9% residual net-long
   (market-beta) tilt; and
2. **persistent** — not concentrated in the 2021 alt-mania,

so it can graduate to the IDM / trend×XS **portfolio-combine layer** (the next
project). If the edge collapses under dollar-neutralization, the headline was
beta; if it is a 2021-only artifact, it will not pay forward.

## Background / why this scope

The G3 verdict (`docs/audits/2026-06-16-p3-xsmom-sleeve.md`) cleared the gate but
flagged three caveats. This project addresses the two that are answerable
in-code/in-data; it explicitly **defers** the third:

- **Beta tilt (addressed here).** Vol-parity sizing re-introduces ~9% net-long
  exposure after the forecast-space demean, carrying ~10% of the return as a
  directional tilt (−0.20 corr to the alt market). This is *not* market-neutral
  alpha and inflates the Sharpe via a bull-market tilt.
- **Forward persistence (addressed here).** A per-period read tells us whether
  +1.375 is a current edge or a 2021 artifact — the thing that actually predicts
  forward P&L.
- **Survivorship / dead names (DEFERRED, demoted).** A spike confirmed Binance
  fapi still serves klines for fully-delisted perps (FTTUSDT/LUNAUSDT/SRMUSDT/…)
  and that `data.binance.vision` lists the full ever-traded roster, so a
  delisting-inclusive test is *feasible* — but it is a data-acquisition project,
  the bias direction for XS momentum is **ambiguous** (catastrophic delistings
  were shorts → their absence *understates*; slow-bleed delistings are noise →
  slightly *overstates*), and the live book always trades currently-listed names
  regardless. It is demoted to an **optional rigor audit to run before allocating
  real capital** (mitigation in the meantime: haircut the Sharpe when sizing).
  The XS book is already point-in-time-correct *given the data* (the active-set
  demean handles entry via warm-up NaN and exit via post-delist NaN), so the
  follow-up is "backfill + add symbols + re-run", needing no membership rewrite.

Everything in this project is **additive and default-off**: the existing XS book,
trend book, and regression goldens are byte-identical when the new flag is off.

## Component 1 — Dollar-neutral re-center (the fix)

**Where:** `analytics/forecast/config.py` + `analytics/xsmom/book.py`.

Add one field to the shared frozen `ForecastConfig`:

```python
xs_dollar_neutral: bool = False  # XS-sleeve only; trend sleeve ignores it
```

Default-off keeps every existing call byte-identical and goldens unmoved. The
audit spins the neutralized variant with `dataclasses.replace(cfg,
xs_dollar_neutral=True)` — the same idiom already used for `speeds` / `weights`
trials.

> Accepted trade-off: this is an XS-only flag living on the trend sleeve's shared
> config. Justified because `analytics/xsmom/` already reuses `ForecastConfig`
> wholesale (it has no config of its own). The alternative — threading a
> `dollar_neutral: bool` param through `xs_leverage` / `run_xs_backtest` /
> `replay_xs*` — keeps the config clean but adds plumbing to four functions and
> breaks the replace-idiom the trials rely on.

**Mechanism — in `xs_leverage`,** after the existing demean → `shift(1)` →
vol-parity step that builds the per-symbol leverage `DataFrame`, when
`cfg.xs_dollar_neutral` is set, subtract the per-day **active-set** mean leverage:

```python
lev_df = lev_df.sub(lev_df.mean(axis=1), axis=0)  # Σ active leverage -> 0
```

This is the same skipna idiom as the forecast demean (`xs_demeaned_forecasts`):

- NaN cells (warm-up / post-delist / undefined vol) stay NaN — they neither
  contribute to the row mean nor receive a position.
- Active cells are re-centered so each day's leverage row sums to ~0 → truly
  dollar-neutral positions, removing the ~9% net exposure.
- It is a **same-day cross-sectional reduction on already-`shift(1)`-ed
  leverage**, so it introduces no look-ahead. The relative cross-sectional
  structure (who is long/short, weighted by 1/vol) is preserved up to the
  additive constant; the downstream 20%-vol governor re-scales the overall level.

`run_xs_backtest` is unchanged — it calls `xs_leverage`, which now honors the
flag. `XSBookResult` shape is unchanged.

## Component 2 — Beta-attribution diagnostic (quantify what was removed)

**New module:** `analytics/xsmom/diagnostics.py` — pure (numpy + stdlib only),
no DB/IO.

```python
def equal_weight_market_return(closes: dict[str, pd.Series]) -> pd.Series: ...
    # active-set mean of per-instrument close.pct_change(), union daily index

@dataclass(frozen=True)
class BetaAttribution:
    alpha_annual: float        # intercept * annualization_days
    beta: float
    alpha_tstat: float         # intercept t-stat = alpha_hat / SE(alpha_hat)
    beta_hedged_sharpe: float  # annualized Sharpe of (port_ret - beta*mkt_ret)
    r_squared: float

def beta_attribution(
    port_ret: np.ndarray, mkt_ret: np.ndarray, ann_days: float = 365.0
) -> BetaAttribution: ...
    # full-sample OLS r_port = alpha + beta*r_mkt + eps; degenerate-safe
    # (zero-variance market -> beta 0.0, beta_hedged == port).
```

> `beta_hedged_sharpe` is the Sharpe of the **beta-hedged return stream**
> `r_port − β·r_mkt` (= α + residual), NOT of the raw OLS residual — the residual
> has zero mean by construction, so its Sharpe is ~0 and meaningless. The hedged
> stream retains the alpha mean and is the deployable "beta-stripped" return.

Full-sample OLS is appropriate: this is an **attribution diagnostic**, not a
causal sizing input. Run it on **both** the original and dollar-neutral books,
against **two** market proxies:

- the equal-weight "alt market" (`equal_weight_market_return`), and
- BTC (`closes["BTCUSDT"].pct_change()`), reported secondarily.

Expectation if the alpha is real: under dollar-neutralization `beta` collapses
toward 0 and `beta_hedged_sharpe` stays near the headline. If instead the
headline Sharpe falls with `beta`, the edge was the bull tilt.

## Component 3 — Forward-persistence diagnostic

Same `diagnostics.py` module:

```python
@dataclass(frozen=True)
class PersistenceReport:
    by_year: dict[int, float]   # calendar-year annualized Sharpe
    trailing_2y: float
    trailing_1y: float
    n_obs: int

def subperiod_sharpe(
    port_ret: np.ndarray, index: pd.DatetimeIndex, ann_days: float = 365.0
) -> PersistenceReport: ...
    # slice by index.year; trailing windows = last 730 / 365 calendar days;
    # any sub-slice with < 2 obs or ~0 std -> 0.0 (never NaN)
```

Reuses the project's annualized per-period Sharpe convention
(`mean/std(ddof=1) * sqrt(ann_days)`, consistent with `report._ann_sharpe`).

## Component 4 — Wiring (read-only)

`analytics/xsmom/__init__.py`: export the new diagnostics
(`beta_attribution`, `BetaAttribution`, `subperiod_sharpe`, `PersistenceReport`,
`equal_weight_market_return`).

`analytics/xsmom/replay.py`: **no signature change** — the flag rides on `cfg`,
so `replay_xs` / `replay_xs_trials` produce the neutralized book whenever passed
a neutralized `cfg`.

`tools/xsmom_audit.py`: the audit loads daily inputs once
(`load_daily_inputs`, already public) for the market proxies, then:

1. Runs the **full DSR/PBO/boot-CI/MinTRL gate stack on the dollar-neutral book**
   (the headline question) and prints it **beside** the original book — reuse
   `build_xs_report_row` with a `xs_dollar_neutral` parameter, so the existing
   breadth/cost/per-speed tables can be rendered for either variant.
2. Adds a **beta-attribution table**: rows = {original, dollar-neutral} ×
   {market proxy, BTC proxy}; cols = alpha_annual, beta, alpha_tstat,
   beta_hedged_sharpe, r².
3. Adds a **forward-persistence table** for the dollar-neutral book: per-year
   Sharpe + trailing-2y + trailing-1y.

Read-only throughout; the audit is not part of the regression goldens.

## Component 5 — Verdict doc

`docs/audits/2026-06-17-p3-xsmom-beta-neutral-persistence.md`, hand-written after
reading the audit output. Must answer:

- Does the gate (DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0) **survive**
  dollar-neutralization?
- How much of +1.375 was beta (alpha vs beta vs residual Sharpe, both proxies)?
- Is it **persistent** (per-year + recent windows) or a 2021 artifact?
- Verdict: **graduate to the IDM / trend×XS combine layer — yes/no.**

## Data flow

```text
load_daily_inputs(conn, universe) -> closes, fundings
cfg          = ForecastConfig(slippage_pct=...)              # original
cfg_neutral  = replace(cfg, xs_dollar_neutral=True)          # neutralized
run_xs_backtest / replay_xs_trials  for BOTH cfgs            # books + DSR/PBO family
evaluate_xs(...)                    for BOTH                 # gate verdict
diagnostics.equal_weight_market_return(closes)              -> mkt_ret
diagnostics.beta_attribution(book.portfolio_return, mkt_ret) for BOTH × {mkt, BTC}
diagnostics.subperiod_sharpe(neutral_book.portfolio_return, neutral_book.daily_index)
tools/xsmom_audit.py prints the tables -> hand-written verdict doc
```

## Testing (TDD)

New / extended tests in `tests/`:

- **Re-center math:** with `xs_dollar_neutral=True`, every active leverage row
  sums to ~0 (within float tol); with it off, `xs_leverage` output is
  byte-identical to current.
- **Active-set correctness:** a warm-up (NaN) instrument neither drags the row
  mean nor receives a position; the re-center mean is taken over active cells.
- **Causality (extend the existing XS perturbation test):** perturbing a *future*
  bar's close leaves today's neutralized leverage unchanged (RED without the
  pre-existing `shift(1)`).
- **`beta_attribution`:** recovers known α/β on synthetic `r = a + b*mkt + noise`;
  degenerate (zero-variance market) returns beta 0.0 and residual == port without
  crashing.
- **`equal_weight_market_return`:** active-set mean of `pct_change`, skipna; union
  index alignment.
- **`subperiod_sharpe`:** correct per-`index.year` slicing; trailing windows;
  empty / single-obs sub-slice → 0.0, never NaN.
- **Default-off byte-identical:** `XSBookResult` from `run_xs_backtest` is
  unchanged when `xs_dollar_neutral=False`.
- **Regression goldens UNMOVED** (additive, default-off).

## Definition of Done

- `make lint-py` ✓ · `make typecheck` ✓ (mypy strict) · `make test` green
- `make test-regression` goldens **unmoved** (additive + default-off — no
  intentional behaviour change to the existing path)
- `make lint-md` ✓ (new spec + verdict doc)
- Verdict doc written; CLAUDE.md `xsmom/` section synced if the package surface
  changed (new `diagnostics.py` + `ForecastConfig.xs_dollar_neutral`).

## Out of scope (explicit)

- Delisting-inclusive dead-name backfill (demoted to pre-capital rigor audit).
- Beta-weighted (Σ levᵢ·βᵢ = 0) or BTC-specific causal hedging — dollar-neutral
  re-center is the agreed mechanism; if the ex-post regression shows material
  residual per-name-beta dispersion after re-centering, that is a note for the
  follow-up, not this project.
- The IDM / trend×XS portfolio-combine layer (the next project).
- Any live-daemon / schema / golden change.
