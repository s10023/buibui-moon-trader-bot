# Top-Tier Crypto Quant System — Fresh-Eyes Design & Gap Analysis

**Date:** 2026-06-05 · **Status:** discussion / design (no code or config change) · **Lens:** experienced systematic-quant / PM review.

This document answers three questions: (1) what does a top-tier crypto trading system actually consist of, (2) where does this codebase sit against that reference, and (3) what is the exhaustive target design and roadmap to get there. It is deliberately critical. It is a **superset** of the existing "Fork B" plan — Fork B is steps inside Layer 6/8 here.

---

## 0. Thesis (read this first)

This codebase is an **excellent technical-analysis pattern-alerting system** with unusually strong engineering, test rigor, and — rare for retail — an honest, de-biased out-of-sample outcome ledger. Measured as a **systematic quant trading system**, however, it occupies only ~40% of the value chain, and the 40% it occupies is the lowest-edge part.

The honest live result (**−0.12R/alert, ~5/20 strategies positive**) is **not a tuning failure**. It is two structural failures stacked:

1. **Category failure** — the entire alpha library is *single-symbol, time-series, technical-pattern* detection (candlesticks, FVG/OB, sweeps, BOS). This is the most crowded, weakest, fastest-decaying category of edge in all of trading. The conditional-edge test (2026-06-04) already proved context does not rescue it.
2. **Missing-stack failure** — everything *downstream* of "a signal fired" — position sizing, portfolio construction, risk overlay, execution, live risk control, attribution — barely exists. That downstream stack is where Carver-style systems earn most of their risk-adjusted return. With 40% of alerts expiring and zero sizing, the **largest P&L multiplier in the system has never been switched on**.

**Top-tier = two pivots, done together:**

- **Alpha pivot:** from "alert when a pattern fires" to "produce a continuous, volatility-scaled *forecast* per instrument across a universe, sourced from where crypto's structural edges actually live" (time-series momentum, cross-sectional momentum, carry/funding, basis).
- **Stack pivot:** build the full chain — forecast combination → risk-parity / vol-target sizing → execution & cost model → live portfolio risk → attribution & decay monitoring.

The good news: the *infrastructure* (DuckDB data layer, backtest engine with WFO + golden regression, the OOS ledger, clean typed packages, CI) is a genuinely strong base to build both pivots on. We are not rewriting — we are extending the value chain and re-pointing the alpha.

---

## 1. Reference architecture — the 11 layers of a top-tier system

For each layer: what it is, what "top-tier" means, and the crypto-specific spin a generalist would miss.

### L1 — Market & microstructure data

- OHLCV multi-venue/multi-TF; **trades tape**; **L2/L3 order book**; perp **funding**, **open interest**, **basis** (perp–spot), term structure.
- Top-tier: point-in-time correctness (no lookahead, candle-close semantics, exchange clock drift handling), gap/outlier detection, multi-venue consolidation, symbol-lifecycle handling (listings/delistings/renames — the crypto analog of corporate actions, and a major survivorship-bias trap).
- Crypto spin: 24/7 (no "weekend" by clock, but weekend *is* a distinct liquidity regime — your own DOW split confirms this); funding settles on a schedule; liquidation cascades are a first-class price driver.

### L2 — Alternative & derivative data

- **Options surface**: DVOL/implied vol, skew, term structure, gamma positioning. **Liquidation feed.** **On-chain**: exchange in/outflows, stablecoin supply/peg, whale flows. Social/sentiment (low priority).
- Top-tier: these feed *both* alpha (vol-risk-premium, flow signals) and *risk* (de-peg, cascade warning).

### L3 — Alpha research framework (the lab)

- Rigorous IS/OOS separation: **walk-forward (have), purged k-fold + embargo (Lopez de Prado, missing)**.
- **Multiple-testing control**: deflated Sharpe ratio, White's reality check / SPA, **Probability of Backtest Overfitting (PBO)**. Running 20 strategies × many params/cells with *no* multiple-testing correction is the textbook recipe for an in-sample mirage that decays to ~0 OOS — which is exactly your observed pattern.
- Feature store + reproducibility + experiment tracking.

### L4 — Signal / forecast library

- Top-tier expresses each signal as a **continuous, vol-normalized forecast** (e.g. Carver's −20…+20 scale), *not* a discrete boolean alert. A forecast carries **direction + conviction**, which is what lets you size.
- Crypto's structurally-supported edges (where you have ~zero coverage):
  - **Time-series momentum / trend** with vol targeting — the single most robust crypto edge across the last decade.
  - **Cross-sectional momentum** — rank a universe, long strongest / short weakest.
  - **Carry (funding harvest)** — persistent, capturable; you *ingest* funding but only use it as a TA "extreme".
  - **Basis / cash-and-carry** — perp-vs-spot, delta-neutral yield.
  - **Vol-risk-premium** (options) — sell rich implied vol.

### L5 — Forecast combination

- Combine many weak forecasts into one number per instrument: forecast weights (vol-scaled, correlation-aware), forecast diversification multiplier, capping. *Not* "alert if any of N fires" (which over-fires correlated signals and has no conviction scale).

### L6 — Portfolio construction & position sizing  ← **biggest gap, biggest payoff**

- **Volatility targeting**: scale every position so the *portfolio* hits a target annualized vol. **Risk parity** across instruments. **Instrument Diversification Multiplier** (Carver). **Correlation-aware** sizing — BTC and alts run ~0.7–0.9 correlated; naive per-signal sizing silently 3–5×'s your true risk on one bet.
- Kelly-fraction (heavily haircut), strategy-level risk budgeting, position/leverage/concentration caps.

### L7 — Execution & OMS

- Smart routing, passive-vs-aggressive, maker rebates, TWAP/VWAP/POV for size, iceberg, anti-adverse-selection; **slippage + market-impact + funding-accrual cost model** that is *fed back into the backtest*.
- Reconciliation: live positions must equal exchange truth; idempotency; partial fills; state recovery.

### L8 — Risk management

- Pre-trade (limits), live (real-time P&L, **drawdown control loop**, margin/liquidation-distance monitor, **kill-switch / dead-man's switch**), portfolio (VaR/ES, stress, correlation-breakdown, tail hedge).
- Crypto-specific: **counterparty/exchange risk** (FTX taught this the hard way), custody, **stablecoin de-peg**, funding spikes, **liquidation-cascade** exposure, exchange-outage handling.

### L9 — Backtest & simulation

- Realistic fills, funding accrual, transaction costs, point-in-time data; **Monte-Carlo / bootstrap confidence intervals** on the equity curve; regime-conditional eval; combinatorial purged CV. (You have a strong base here already.)

### L10 — Live ops

- Robust event loop, heartbeats, health monitoring/alerting, reconciliation, graceful degradation, secrets hygiene.

### L11 — Performance attribution & strategy lifecycle

- Sharpe / Sortino / Calmar, turnover, **capacity**, per-strategy & factor P&L attribution, slippage attribution, **live-vs-backtest tracking & decay monitoring** (you started this — the OOS ledger), formal lifecycle: idea → OOS validate → paper → small live → scale → monitor decay → retire.

---

## 2. Gap matrix — where this codebase sits

| Layer | Status | Detail |
| --- | --- | --- |
| L1 Market data | **HAVE (partial)** | OHLCV multi-TF (Binance/OKX), funding, OI → DuckDB. **Missing:** order book, trades tape, multi-venue consolidation, symbol-lifecycle/survivorship handling. |
| L2 Alt/deriv data | **MISSING** | No options surface, no liquidation feed, no on-chain. |
| L3 Research framework | **HAVE (partial)** | WFO + golden regression + recalibration = strong. **Missing:** purged CV/embargo, and critically **multiple-testing control (deflated Sharpe / PBO / reality check)** — the missing guardrail behind the −0.12R decay. |
| L4 Signal library | **HAVE — but wrong category** | 20 TA/price-action detectors, expressed as **discrete booleans**. **Missing:** TS-momentum-as-forecast, cross-sectional alpha, carry harvest, basis, vol-premium. Funding/OI ingested but used only as TA extremes. |
| L5 Forecast combination | **MISSING** | Current model is "alert if any fires" + combo *annotations* (not gates). No conviction scale, no forecast weighting. |
| L6 Sizing / portfolio | **MISSING** | No position sizing at all. No vol targeting, risk parity, IDM, correlation awareness, or capital allocation. **The single highest-leverage gap.** |
| L7 Execution / OMS | **MISSING** | Manual `trade/open_trades.py` only; no automation, no slippage/impact model fed back to backtest, no reconciliation. |
| L8 Risk mgmt | **MISSING** | No live portfolio risk, drawdown control, kill-switch, margin/liq monitor, or counterparty/de-peg framework. Position monitor is read-only. |
| L9 Backtest/sim | **HAVE (strong)** | Sweep/combo/cross-TF, WFO, golden regression, live-parity gate porting. **Missing:** cost/funding accrual in sim, bootstrap CIs, PBO. |
| L10 Live ops | **HAVE (partial)** | Cron + OKX keyless + Telegram, signal-state persistence. Signals-only; no exec loop / reconciliation. |
| L11 Attribution/lifecycle | **HAVE (partial)** | avg_r, win-rate, star ratings, **de-biased OOS ledger (genuinely top-tier)**. **Missing:** Sharpe/turnover/capacity, factor attribution, formal lifecycle gates. |

**Net:** strong on L1/L3/L9/L10/L11 *infrastructure*; the **alpha is in the weakest category (L4)** and the **entire monetization half of the stack (L5–L8) is absent.**

---

## 3. The critical critique (be brutal)

1. **You are A/B-testing the wrong half.** Every recent cycle (prune, direction overlay, gate tuning, ATR/TP sweeps) optimizes L4 signal *selection*. Carver's central finding is that diversification + sizing + risk control contribute *more* risk-adjusted return than signal selection. You are sanding the cheapest lever while the expensive ones (L5–L8) sit at zero.
2. **TA patterns are a category with a structurally weak prior.** The market for "spot the engulfing candle" is infinitely crowded and the payoff is near-zero before costs. The conditional-edge test confirmed even your *positive* in-sample cells decay OOS. No amount of gating fixes a near-zero base rate.
3. **No multiple-testing correction → guaranteed in-sample mirage.** 20 strategies × directions × TFs × symbols × param grids is thousands of trials. Without deflated-Sharpe/PBO, the "best" cells are *selected noise*. The OOS ledger is catching this after the fact; you want to catch it *before* committing a config.
4. **Booleans throw away conviction.** A signal that's "barely true" and one that's "screaming" fire identical alerts. You can't size what you don't measure. Forecasts (L4→L5) are the prerequisite for L6.
5. **Correlation is unmodeled risk.** Firing BTC + ETH + SOL longs on the same setup is *one* bet at ~3× the risk you think. Until L6 models correlation, your realized vol and drawdowns are uncontrolled.
6. **The edge you already ingest is unused.** Funding is a real, persistent crypto carry edge. You pull it and reduce it to a TA "extreme" boolean instead of harvesting it.

**Credit where due (this is a strong base, not a teardown):** the architecture, typing, CI, WFO discipline, golden-regression tests, and especially the **honest de-biased OOS ledger** are top-decile for a system this size. Most blow-ups come from people who *can't* see −0.12R. You can. That honesty loop is the thing to build the rest on.

---

## 4. Where crypto's real edges live (target alpha set)

Ranked by robustness / capacity / implementability on your current data:

1. **Time-series momentum / trend, vol-scaled** — implementable *now* (you have OHLCV; reframe `ema`/`trend_day` from booleans into a multi-speed EWMAC forecast). Highest-confidence first build.
2. **Cross-sectional momentum** — needs a *universe* (10–30 liquid perps) and a ranking/portfolio layer. High edge, modest data lift.
3. **Carry / funding harvest** — you already ingest funding; needs basis data + delta-neutral execution. Persistent, lower-vol.
4. **Basis / cash-and-carry** — delta-neutral perp-vs-spot yield; needs spot venue + funding; low risk, capacity-rich.
5. **Vol-risk-premium / options** — highest data lift (L2 options surface); defer.
6. **Mean-reversion (microstructure)** — needs order book (L1+); defer.

TA patterns don't vanish — they become **low-weight confirmation forecasts inside L5**, never standalone position-makers.

---

## 5. Target architecture (the "max" design)

```text
            ┌────────────────────────────────────────────────────────────┐
  L1/L2     │  Data: OHLCV · funding · OI · basis · (book/trades/options) │
            │        point-in-time store (DuckDB) + symbol lifecycle      │
            └──────────────────────────┬─────────────────────────────────┘
                                       ▼
  L4        │  Forecast library: per-instrument continuous forecasts      │
            │  ┌ TS-momentum (EWMAC multi-speed)                          │
            │  ┌ XS-momentum (universe rank)                              │
            │  ┌ Carry (funding)   ┌ Basis   ┌ TA-confirmation (low wt)   │
            └──────────────────────────┬─────────────────────────────────┘
                                       ▼
  L5        │  Forecast combination → one capped forecast / instrument    │
                                       ▼
  L6        │  Portfolio construction: vol target · risk parity · IDM ·   │
            │  correlation matrix · caps → target positions               │
                                       ▼
  L7        │  Execution/OMS: order sizing vs current · cost model ·      │
            │  routing · fills · reconciliation                          │
                                       ▼
  L8        │  Live risk overlay: drawdown control · margin/liq monitor · │
            │  kill-switch · de-peg/counterparty guards (can VETO L6/L7)  │
                                       ▼
  L11       │  Attribution + decay: Sharpe/turnover/capacity · OOS ledger │
            │  · per-forecast P&L · lifecycle gates  ──── feeds back ────▶ L3
```

Key design rules:

- **Forecasts are continuous and vol-normalized** before they ever reach sizing.
- **L8 is a veto layer** sitting above execution — it can flatten/halt regardless of L4–L6.
- **The OOS ledger (L11) closes the loop** into research (L3) — it already exists; wire it as the lifecycle gate.
- **Costs live in one model** shared by backtest (L9) and live (L7) so sim and reality can't diverge.

---

## 6. Phased roadmap (each phase ships standalone value)

**Phase 0 — Honesty & guardrails (research hygiene).** Add multiple-testing control (deflated Sharpe + PBO) to the sweep pipeline; add cost+funding accrual to the backtest fill model; add bootstrap CIs to equity curves. *Outcome:* stop committing in-sample mirages. Cheap, high-leverage, no live risk.

**Phase 1 — Sizing on what exists (Fork B step 3, Carver core).** Build L6 minimally: vol-target + per-instrument vol scaling + a correlation-aware cap, applied to the *current* signal set as paper sizing. Build L11 metrics (Sharpe/turnover/drawdown) on the paper book. *Outcome:* first real risk-adjusted number; the 40%-expiry leak gets a sizing/exit policy. **This is the highest payoff for the least new alpha.**

**Phase 2 — Forecast reframe.** Convert `ema`/`trend_day` → continuous **EWMAC multi-speed TS-momentum forecast**; build L5 forecast combination; fold surviving TA strategies in as low-weight confirmations. *Outcome:* first structurally-supported edge, expressed correctly.

**Phase 3 — Universe & cross-sectional.** Expand to a 10–30 perp universe; add **XS-momentum** ranking and a portfolio layer (L6 full). *Outcome:* diversification multiplier + a second uncorrelated edge.

**Phase 4 — Carry / basis.** Harvest funding; add basis/cash-and-carry (delta-neutral). *Outcome:* low-vol return stream that diversifies the momentum book.

**Phase 5 — Execution & live risk.** Automate L7 (cost-aware) + L8 (drawdown control, kill-switch, margin/liq + de-peg guards) + reconciliation. *Outcome:* go from "alerts" to a controlled live book.

**Phase 6 — L2 data & vol strategies (optional/long-horizon).** Options surface, on-chain, microstructure → vol-premium + mean-reversion.

**Sequencing logic:** Phases 0–1 are pure risk-adjusted-return wins on *existing* alpha (do first, they're cheap and they're Fork B). Phases 2–4 fix the *category* problem. Phase 5 is the operational leap to automation (highest operational risk — last among the core). Phase 6 is optional upside.

---

## 7. Anti-goals / risks to manage

- **Don't automate execution (Phase 5) before L8 risk + reconciliation exist.** An un-risk-managed auto-executor is how accounts die.
- **Don't scale a new edge on in-sample alone** — Phase 0 guardrails gate every promotion; the OOS ledger is the lifecycle gate.
- **Don't let the universe import survivorship bias** — symbol-lifecycle handling (L1) is a prerequisite for XS work (Phase 3).
- **Counterparty risk is existential** — multi-venue and withdrawal discipline before scaling capital.
- **Beware complexity debt** — each layer must keep the current architecture's typing/test/regression discipline or the honesty loop rots.

---

## 8. Relationship to existing plans

- **Fork B (prune → direction overlay → Carver sizing)** = Phase 0/1 here. Prune-to-core stays valid: it's the right move *for the TA book* while L4 gets reframed. The direction overlay is a (short-biased) forecast tilt that survives into L4.
- **`project_profitability_regroup` / `conditional-edge-test`** already established that more *gating* of the TA book won't reach positive EV. This design accepts that verdict and says: the way out is **new edge categories + the monetization stack**, not more TA tuning.
- This doc does not replace the to-do; it reframes it. The next concrete action remains **Phase 0 guardrails + Phase 1 sizing**, because they are cheap, low-risk, and unlock measurement for everything after.
