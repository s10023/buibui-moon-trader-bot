# P2 — EWMAC Trend Sleeve → Gate G2 (design)

> Status: approved 2026-06-15. First sub-project of P2 (the breadth pivot).
> Follow-ups (carry sleeve, cross-sectional momentum, range hindsight H1/H4/H5,
> TA-confirmation fold-in) get their own spec → plan cycles.

## 1. Goal & success metric

Reframe the boolean `ema` / `trend_day` detectors into **continuous,
vol-normalised, multi-speed EWMAC trend forecasts** (Carver convention), run them
across the **N3 25-perp universe** on **1d bars** through a
**portfolio-vol-targeted paper book** with **honest costs (turnover + funding)**,
and produce the **gate G2 verdict**.

**Success metric (G2):** trend-sleeve **OOS Sharpe ≥ ~1 on the universe** (not the
3 majors), costs in, **DSR / PBO / bootstrap-CI / MinTRL** gated. If marginal on
majors but positive on breadth → proceed (breadth is the mechanism). The number,
its guard stamps, and the decision are written down in a dated audit doc.

This is **read-only research**. No live daemon, no Telegram, no schema change, no
engine/`_scan_forward` change. The existing backtest pipeline and its golden
fixtures are untouched.

### Why this sub-project, and why now

- G1 was **not cleared** by TA-book sizing + exits (`docs/audits/2026-06-15-exit-policy-ab-v1.md`):
  the binding constraint is **breadth** — ~90 % of the alert stream is cap-skipped
  because the live universe is one BTC/ETH/SOL majors cluster. P1 (sub-A) and the
  exit replay (sub-B) independently converge on the same conclusion.
- Time-series momentum / trend, vol-scaled, is the **#1 ranked edge** in both
  redesign docs — "best first build… needs breadth, not cleverness" — and the only
  structural edge buildable **right now with zero new data**: the N3 universe is
  already backfilled (1.95 M OHLCV rows, deep history incl. 1w, 0 % gaps,
  lifecycle-aware survivorship guard).
- The existing **research guards** (DSR/PBO/MinTRL/bootstrap) finally get pointed
  at a category with a real economic prior, rather than a TA book that returned
  0/123 DSR passes.

## 2. Background & constraints

### Reusable shelf (do not rebuild)

| Component | Path | Role here |
| --- | --- | --- |
| N3 universe loader | `analytics/universe.py::load_universe` | the 25 perps + lifecycle metadata |
| OHLCV + funding store | `analytics/store/` (market_data) | read-only 1d bars + funding history |
| Curve metrics | `portfolio/metrics.py` | Sharpe / Sortino / max-DD / Calmar / annual ret+vol / attribution (curve-based, instrument-agnostic) |
| Research guards | `research_guards/` (dsr, pbo, mintrl, bootstrap, haircut, psr) | the G2 gate stack |
| Regime classifier | `analytics/regime.py::classify_series` | regime-conditioned attribution |
| Cost params | `[backtest]` in `config/strategy_params.toml` | `fee_pct`, `slippage_pct` (2 bps/leg) |

### Hard constraints (from the SoT stop-doing list + guardrails)

- **No new boolean TA detectors.** This is a *reframe* of existing trend signals
  into continuous forecasts, not a new pattern.
- **No tp_r / gate / threshold sweeps on the TA book.**
- **Forecasts are continuous and vol-normalised before they ever reach sizing.**
- **Causality everywhere** — every estimate (vol, FDM, governor) uses data strictly
  before the bar it sizes. No look-ahead. This is the same discipline P1's vol
  governor already enforces.
- **Parameter-light by design** — use Carver's published, *non-crypto-fit*
  constants for speeds and forecast scalars. The fewer crypto-fitted knobs, the
  smaller the overfit surface the guards must reject, and the more the curve is
  "OOS by construction".

## 3. Architecture

### 3.1 New package `analytics/forecast/` (mirrors `analytics/exits/`)

```text
analytics/forecast/
  ewmac.py     # pure forecast math — no DB, no IO
  vol.py       # causal exponentially-weighted volatility estimators
  config.py    # ForecastConfig (frozen) + from_toml
  book.py      # ForecastBook / run_forecast_backtest — daily-rebalanced engine
  replay.py    # ONLY DB-touching module — load universe, run engine (read-only)
  report.py    # terminal renderer (portfolio + per-instrument + per-regime)
  __init__.py  # eager re-exports
```

Driver: **`tools/forecast_audit.py`** — read-only CLI mirroring
`tools/exit_audit.py` / `tools/strategy_edge_audit.py`. Prints the G2 verdict
(portfolio Sharpe/Sortino/max-DD, per-instrument + per-regime attribution,
DSR/PBO/CI/MinTRL stamps, cost-sensitivity table). A `buibui forecast` CLI
subcommand is **deferred** until the sleeve clears G2.

### 3.2 Data flow

```text
load_universe() ─┐
                 ├─► replay.py: per-symbol 1d OHLCV + funding (read-only DuckDB)
analytics.db ────┘            │
                              ▼
        ewmac.py: raw EWMAC per speed → vol-adjust (vol.py) → scale+cap
                              │
                              ▼
        ewmac.py: multi-speed combine (equal weight) × FDM → cap [-20,+20]
                              │
                              ▼
        book.py: per-instrument position = (forecast/10)·(k/σ_inst)
                 pnl_t = pos_{t-1}·ret_t  (causal)
                 − turnover cost  − funding accrual
                 → portfolio vol governor (causal, 20% target)
                 → daily portfolio return series + per-instrument attribution
                              │
                              ▼
        portfolio/metrics.py + research_guards/  →  report.py / audit doc
```

## 4. The forecast math (`ewmac.py`, `vol.py`)

All functions pure, operating on `pd.Series` indexed by bar time. Causal.

### 4.1 Volatility (`vol.py`)

- `ew_return_vol(prices, span)` → exponentially-weighted stdev of daily simple
  returns, σ_% (causal — `.ewm(span).std()` shifted so bar t uses returns ≤ t−1).
  Default **span = 32** bars.
- `price_vol(prices, span)` → σ_price = σ_% · price (for the forecast denominator).
- Annualisation factor **√365** (crypto trades 365 d — consistent with
  `portfolio/metrics.py`).

### 4.2 Raw + scaled forecast (`ewmac.py`)

For a fast/slow EWMA pair:

```text
raw_ewmac      = EWMA(price, fast) − EWMA(price, slow)
vol_adj        = raw_ewmac / price_vol            # comparable across symbols/time
scaled         = vol_adj × forecast_scalar[speed] # long-run avg |forecast| ≈ 10
capped         = clip(scaled, −20, +20)
```

- **Speeds (multi-speed library):** `{(8,32), (16,64), (32,128), (64,256)}`. On 1d
  bars these span ~weeks → ~1 year. The 64/256 pair (~1 yr lookback) is the natural
  test of **H2** ("50W EMA flip = cycle bias" ≈ a slow EWMAC in disguise).
- **Forecast scalars:** Carver's published per-speed constants (fixed; *not*
  crypto-fit → no look-ahead). Robustness check: expanding-window estimated scalar
  (`10 / mean(|vol_adj|)` over data ≤ t−1).

### 4.3 Multi-speed combination (L5, `ewmac.py`)

```text
combined = FDM × Σ_i ( w_i · capped_i )   # equal weights w_i = 1/N
final    = clip(combined, −20, +20)
```

- **FDM (Forecast Diversification Multiplier):** combining correlated forecasts
  pulls the average abs value below 10; FDM scales it back. **v1 uses a fixed
  constant (default 1.25)** — a standard value for ~4 correlated trend speeds. A
  fixed FDM is a pure constant (no look-ahead) and only affects results
  second-order through cap saturation, so it is the right v1 choice. An
  expanding-window correlation-based FDM estimator (Carver method, capped ≈
  1.0–2.5) is a deferred robustness variant.

## 5. The engine (`book.py`)

`run_forecast_backtest(forecasts, returns, funding, cfg) → ForecastBookResult`.
Return-space, single causal forward pass over the daily index:

1. **Per-instrument position:** `pos_{i,t} = (final_{i,t} / 10) · (k / σ_{i,t})`,
   where σ is the instrument's annualised return vol and `k` sets the per-instrument
   base risk so forecast = 10 targets an equal risk slice. Equal risk weight across
   instruments. Instruments not yet listed / `DELISTED` carry `pos = 0` (lifecycle
   guard — the survivorship metadata is already in the store).
2. **Causal P&L:** `pnl_{i,t} = pos_{i,t−1} · ret_{i,t}` (position set on info ≤ t−1
   earns bar-t return). Raw book return `R_t = Σ_i pnl_{i,t}`.
3. **Costs (honest):**
   - turnover: `cost_{i,t} = |pos_{i,t} − pos_{i,t−1}| · (fee_pct + slippage_pct)`;
   - funding accrual on held perp positions: `−sign(pos_{i,t−1}) · funding_{i,t} ·
     |pos_{i,t−1}|` (long pays positive funding / short receives), read from the
     `funding_rates` table; graceful **0.0** when absent (e.g. instruments without
     funding history). Net `R_t = raw − turnover − funding`.
4. **Portfolio vol governor (causal):** scale the whole book by `g_t` so the
   *trailing* realised vol of the net book return strictly before t hits the **20 %
   annualised** target. Caps the leverage of a correlated book — this is what
   replaces the full IDM/correlation optimiser (deferred to P3), per the approved
   "portfolio-level vol target" decision.
5. **Outputs:** net daily portfolio return series (→ `portfolio/metrics.py`),
   per-instrument attribution, gross/net/turnover/funding decomposition, realised
   vol, and the governor path.

`ForecastConfig` (`config.py`, frozen dataclass + `from_toml`): speeds, weights,
FDM mode/cap, vol span, annualisation, vol target, fee/slippage, governor lookback.
Defaults match the values above and P1's `SizingConfig` where they overlap (20 %
vol target, 2 bps/leg).

## 6. G2 evaluation methodology (`replay.py`, `report.py`, `tools/forecast_audit.py`)

Because the system is parameter-light with fixed textbook speeds and every estimate
is causal/expanding, the equity curve is **effectively OOS by construction** (no
future leakage). The guard stack still reports honestly:

- **DSR** (deflated Sharpe) on the portfolio curve, deflated against the count of
  design variants actually tried (speed-set, FDM mode, scalar source).
- **CSCV-PBO** over universe × time folds — does the in-sample-best config stay good
  OOS.
- **Block / stationary bootstrap CI** on portfolio Sharpe — is the lower bound > 0?
  > 1?
- **MinTRL** — is the track record long enough to trust Sharpe ≥ 1?
- **Cost sensitivity sweep** — 0 / 2 / 8 / 16 bps per unit turnover (trend's whole
  claim is cost-robustness; recent 150-pair study stays > 2.0 Sharpe at 8 bps).
- **Attribution** — per-instrument and **per-regime** (via `classify_series`): does
  the trend Sharpe concentrate in trend regimes, as theory predicts?
- **Breadth contrast** — full universe vs majors-only, to show breadth is the
  mechanism.
- **H2 finding** — does the slow (64/256) speed carry the BTC cycle bias?

Verdict written to **`docs/audits/2026-06-15-p2-ewmac-trend-g2.md`** (adjust date
to ship date): the portfolio numbers, guard stamps, cost table, attribution, the H2
result, and an explicit G2 PASS / MARGINAL / FAIL decision with reasoning.

## 7. Out of scope (YAGNI / deferred to later sub-projects)

- Full **IDM + correlation-matrix** portfolio optimiser → **P3**.
- **Cross-sectional momentum** (universe ranking) → **P3**.
- Standalone **carry sleeve** → **Q2** (funding is still netted as a cost here).
- Folding **surviving TA strategies in as low-weight confirmation forecasts** →
  follow-up *after* trend stands alone at G2 (adding them now muddies the trend
  read).
- **Range hindsight study** (H1, H4, H5 rotation) → range follow-up sub-project.
- `buibui forecast` **CLI subcommand**, **live wiring**, Telegram → post-G2 only.

## 8. Testing & Definition of Done

- **TDD throughout:**
  - forecast math (`ewmac.py`, `vol.py`) vs hand-computed EWMAC values and Carver
    reference numbers; cap behaviour; FDM scaling;
  - **causal-governor no-look-ahead assertions** (position at t depends only on data
    ≤ t−1; perturbing a future bar must not change an earlier position);
  - cost + funding **sign** correctness (long pays positive funding, short
    receives; turnover charged on |Δpos|);
  - tiny **synthetic-universe** end-to-end book (a known trending series ⇒ positive
    net, a chop series ⇒ near-zero / negative after costs).
- Analytics tests use `duckdb.connect(":memory:")`; no real network, no real
  `analytics.db` mutation.
- **DoD gate (state each result plainly):** `make lint-py` ✓ · `make typecheck` ✓
  (mypy strict) · `make test` green · `make test-regression` **goldens UNMOVED**
  (new read-only package touches nothing in the existing backtest pipeline; if a
  golden moves, something is wrong).

## 9. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Over-levering a correlated book inflates Sharpe | portfolio vol governor on *realised* book vol absorbs correlation; report majors-only contrast |
| Survivorship / look-ahead from delisted names | lifecycle guard zeroes positions outside listed window; expanding-window estimates only |
| Forecast-scalar look-ahead | use Carver's fixed non-crypto constants as primary; expanding-window only as robustness check |
| Funding data gaps on smaller perps | graceful 0.0 accrual; flag coverage in the audit |
| "OOS by construction" over-claims | report DSR/PBO/CI/MinTRL anyway; CSCV folds give a real overfit probability |
| Scope creep into P3 portfolio layer | IDM/correlation/XS-momentum explicitly out of scope (§7) |
