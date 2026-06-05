# P1 — Position Sizing & Paper Portfolio (spec)

**Date:** 2026-06-05 · **Status:** design / discussion (no code yet) · **Parent:** `docs/redesign/2026-06-05-top-tier-quant-redesign.md` (Layer L6 + L11) · **Equivalent to:** Fork B step 3 (Carver sizing/risk/exits).

## 0. Goal

Turn the existing discrete-alert stream into a **sized paper portfolio** and produce the system's **first risk-adjusted number** (Sharpe / max-DD / turnover) — with **zero live risk**. We replay the real, de-biased `signal_alert_outcomes` ledger (OOS alerts) through a sizing model and measure the equity curve. This is the cheapest, highest-leverage build in the whole redesign: it switches on the P&L multiplier that has never been on (40% expiry, no sizing today), and it makes everything downstream *measurable*.

**Non-goals (P1):** live execution (P5), continuous forecasts/conviction (P2), full IDM correlation matrix (P1.5 refinement), options/vol sizing.

## 1. Why fixed-fractional-on-stop is the right unit here

This system is already **R-based**: every alert carries an entry, a stop (structural or pct-fallback), and the outcome ledger scores realized R. The natural sizing unit is therefore **risk a fixed fraction of equity to the stop**:

```text
units      = risk_capital / |entry − stop|
pnl_on_exit = units × (exit − entry) × side = risk_capital × R_realized
```

So **account return per trade = r_eff × R_realized**. The R-ledger maps directly to account P&L — no new alpha needed, just a sizing weight. Because the stop is ATR-based, position notional is already **inversely proportional to instrument volatility** — i.e. we get instrument-level vol scaling for free. What's missing (and what P1 adds) is the **portfolio-level** governor: vol target + correlation control.

(Carver's continuous vol-target formula is the right model once we hold continuous forecasts in P2; for a discrete stop-based alert stream, fixed-fractional-on-stop is the faithful discrete analog and reconciles to the same portfolio-vol outcome via the governor below.)

## 2. The two-layer sizing model

**Layer A — per-trade risk unit (instrument vol-scaled):**

```text
risk_capital_t = r_eff_t × equity_t
units_t        = risk_capital_t / |entry − stop|
```

**Layer B — portfolio governor (vol target + correlation):** `r_eff` is a base fraction modulated and then capped:

```text
r_eff = r_base × g_vol × g_regime × g_location × g_conviction
        └─ then clipped by concurrent-risk and cluster caps ─┘
```

- `r_base` — base risk per trade (default **0.25%** of equity).
- `g_vol` — **vol-target governor** = `clamp(σ_target / max(σ_realized, ε), g_min, g_max)`, where `σ_realized` is the trailing annualized stdev of the paper equity curve (e.g. 30-trading-day window). Targets a **portfolio** vol (default **σ_target = 20%/yr**); shrinks risk when the book runs hot (too many correlated bets firing), expands when cold. Clamp default `[0.5, 1.5]`.
- `g_regime` — regime modulator (see §6). **Default: high_vol → 0.5, else 1.0.** (Risk-halving in high-vol is the one regime use the data supports; we do NOT down-weight by trend/range mapping — see §6.)
- `g_location` — location-quality modulator (see §7). **Default 1.0 in P1** (the TA book); becomes active when forecasts arrive.
- `g_conviction` — **1.0 in P1** (booleans carry no conviction). Optional, guarded: a coarse multiplier from the *live OOS* per-(strategy,tf,direction) avg_r with an n-floor (≥30) — never the in-sample star rating (overfit). Flagged as a P1.5 experiment, default off.
- **Concurrent-risk cap** — `Σ r_eff over open positions ≤ R_open_max` (default **2%**). New trade scaled down or skipped if it would breach.
- **Cluster cap** — per correlation cluster (default cluster = majors `{BTC, ETH, SOL}` since they run ρ≈0.7–0.9), `Σ r_eff ≤ R_cluster_max` (default **1.0%**). This is the **minimal correlation control** — it stops "BTC+ETH+SOL all fire long" from silently becoming a 3× bet. (Full Carver IDM = P1.5.)

## 3. Exit policy (needed to compute the curve)

Sizing and exits are coupled — you can't draw an equity curve without an exit. **P1 reuses the existing exits**: the outcome ledger already resolves each alert to W / L / E (expiry) with a realized R via `backfill_outcomes` (structural-or-pct SL, tp_r TP, time-expiry marked at mark-to-market R). P1 consumes those resolved R values as-is. **Exit *improvement* (trailing stops, partial TP, the 40%-expiry leak) is a sibling task** — flagged here, specced separately, because it changes the ledger's R distribution and should be A/B'd against the baseline curve P1 establishes.

## 4. Data source & concurrency

Source: `signal_alert_outcomes` (cross-symbol, resolved rows). Schema verified 2026-06-05 — every column needed is present:

| Need | Column |
| --- | --- |
| entry time | `candle_ts_ms` (entry candle open_time, ms) |
| **exit time** | **`outcome_filled_at_ms`** (candle open_time of the resolving bar) |
| realized R | `outcome_r` (win=+rr_ratio, loss=−1.0, expired=MTM) |
| outcome | `outcome` (win/loss/expired) |
| prices / side | `entry_price`, `sl_price`, `tp_price`, `rr_ratio`, `direction` |

**Concurrency RESOLVED — build true concurrency directly (no sequential fallback).** `outcome_backfill.py::_scan_forward` writes `outcome_filled_at_ms` as the candle open_time of the resolving bar for *all three* outcomes (loss→SL-hit candle, win→TP-hit candle, expired→last bar of the `max_hold_bars` window). So every resolved row carries a clean holding interval **`[candle_ts_ms → outcome_filled_at_ms]`**, both on the candle clock. `book.py` models overlapping positions, applies concurrent + cluster caps in real time, and computes portfolio realized vol from the time-overlapped curve.

Two rules: (1) use **`candle_ts_ms`** (not wall-clock `fired_at_ms`) as the entry anchor — same clock as the exit; (2) filter to rows with **non-NULL `outcome_r`**, which inherently excludes the old NULL-`tp_price` unscoreable rows (see `project_outcome_ledger_tp_hole`).

## 5. Module structure (matches repo conventions: pure libs + thin wrapper, DI, typed, tested)

```text
portfolio/
  __init__.py
  sizing.py    SizingConfig (frozen) · instrument_risk() · position_size()
               · vol_governor() · apply_caps() · effective_risk_fraction()
  book.py      PaperBook — open/close/mark, equity curve, open-interval ledger
  replay.py    replay_ledger(conn, cfg) -> (EquityCurve, list[SizedTrade])
  metrics.py   sharpe/sortino/calmar/max_drawdown/turnover/exposure/realized_vol
cli/portfolio.py   `buibui portfolio replay --config <toml> --capital N --vol-target 0.20`
tests/             test_portfolio_sizing.py · test_portfolio_replay.py · test_portfolio_metrics.py
```

- Pure functions take dependencies as args (conn, config) — tests pass `duckdb.connect(":memory:")` + seeded rows, mirroring analytics tests.
- `make buibui-portfolio-replay` Makefile target.
- **L11 metrics** computed on the paper curve: Sharpe, Sortino, Calmar, max-DD, turnover, avg exposure, realized vs target vol, per-strategy P&L attribution (decompose the curve by strategy/tf/direction). Later: a **Stats UI card** + suggested-size annotation in the Telegram alert (read-only, pre-execution).

## 6. Regime — what exists, and the trap to avoid

**Already implemented** (answer to "not sure if done"): `analytics/regime.py::classify_series` labels each candle `trend / range / high_vol / unknown` (high_vol > trend > range priority; EMA50 slope for trend, ATR%-p80 for high_vol). It's wired as a **soft-mode gate** (Step −1 of the bias chain) via `analytics/signal/gates.py::_apply_regime_gate`, with a v1 **type→regime routing map** (`trend`/`fib`/`bos` → trend-only; flow/structural/candlestick/etc → all regimes).

**The trap in "range→a,b,c / trend→c,d,e":** that *is* the v1 mapping, and it was **empirically falsified**. `tools/regime_gate_replay.py` replayed it over ~708K trades: the cells it would **keep** (trend) ran **−0.13R**; the cells it would **suppress** (range/high_vol) ran **+0.029R**. `bos`, `ema`, `fib_golden_zone` all perform **better in range than in trend** — the inverse of the intuition. So regime stays in **soft mode indefinitely**; do **not** hard-route strategies by the obvious trend↔trend mapping.

**The right framing for the redesign:** regime is **not a binary router** — it's a **size/weight modulator** (the `g_regime` term in §2), and the only robust use today is **high_vol → halve risk**. Per-strategy×regime weights must be *earned by data* (regime is already a column in `backtest_trades`; the edge audit can fit them), not assumed. In the L4/L5 forecast world, regime scales a forecast's weight; it never flips it on/off.

## 7. "Where I want to make business" (location) + the pyramiding tension

Your instinct — pre-define the **locations** (zones) you'll trade and filter everything else — is sound, and partially supported: the conditional-edge test found **liquidity locations win, imbalance (FVG/OB) locations lose**. The geometry already exists in `analytics/zones_lib.py` (FVG, OB, EQH/EQL, BOS, Fib, OTE, swings) and the `is_trading_into_wall()` resistance-proximity idea.

**But location-gating ≠ losing the ability to pyramid.** The tension you raised (only trading at levels removes scaling into a runner) dissolves once you see that **location and momentum are two different edges with two different sizing rules — they belong in two different books:**

- **Location / value book (mean-reversion-flavored):** enter *at* a quality zone, defined risk beyond the level, **size once** (no pyramiding). This is your *winning* core today — `liquidity_sweep`, `eqh_eql`, `fib_golden_zone`. Here, the location-quality score is a `g_location` modulator (liquidity-weighted > imbalance-weighted, per the edge test).
- **Momentum / trend book (P2, EWMAC):** operates **continuously, not at fixed levels**, and **adds size as the forecast strengthens** — which is exactly *disciplined, non-FOMO pyramiding*: the forecast (not emotion, not a level) authorizes the add, and trails the stop. This is where "correctly chase a trend by adding size" lives.

So: **location-gate the mean-reversion book; do not location-gate the momentum book.** They're often anti-correlated → running both *diversifies* (good). The pyramiding ability you don't want to lose is simply relocated to the book that's built for it. "Where I want to make business" becomes an **evidence-weighted zone-quality map** (liquidity > imbalance) feeding `g_location` for the mean-reversion forecasts — not a global gate that strangles the trend book.

## 8. Rollout

1. **P1 — paper replay with concurrency** (this spec): open-interval tracking from `[candle_ts_ms → outcome_filled_at_ms]`, concurrent + cluster caps, vol governor on the time-overlapped curve → first Sharpe/DD/turnover. No live risk. (Schema verified — concurrency buildable from day one; no sequential-fallback stage needed.)
2. **P1.5 — IDM + optional conviction**: full rolling-correlation IDM; guarded OOS-avg_r conviction multiplier.
3. **Live annotation**: emit a **suggested position size** in the Telegram alert (read-only, human still executes).
4. **Execution (P5)**: only after the L8 live-risk overlay (drawdown control, kill-switch, margin/liq, de-peg) exists.

## 9. Defaults (all overridable via TOML `[portfolio]`)

| Param | Default | Note |
| --- | --- | --- |
| `capital` | 10,000 | paper notional |
| `r_base` (risk/trade) | 0.25% | fixed fractional on stop |
| `vol_target_annual` | 20% | portfolio vol governor target |
| `vol_window_days` | 30 | trailing realized-vol window |
| `g_vol` clamp | [0.5, 1.5] | governor bounds |
| `R_open_max` | 2.0% | concurrent open-risk cap |
| `R_cluster_max` | 1.0% | per-cluster cap (majors) |
| `high_vol_risk_mult` | 0.5 | the one robust regime use |
| `g_location`, `g_conviction` | 1.0 | off in P1; on in P1.5/P2 |
| compounding | on | report fixed-notional curve too |

## 10. Open decisions (need answers before coding)

1. ~~Ledger schema: exit timestamp?~~ **RESOLVED 2026-06-05** — `outcome_filled_at_ms` is a clean per-row exit timestamp (candle clock). Build concurrency directly; no sequential-fallback stage.
2. **Compounding vs fixed notional** as the headline curve (report both; pick one for the Sharpe headline).
3. **Cluster definition:** static majors cluster for P1, or derive clusters from rolling correlation now?
4. **Conviction in P1.5:** do we trust the live OOS avg_r (n≥30) enough to size on it, or stay uniform until P2 forecasts?
5. **Exit improvement** (40%-expiry leak): sibling task — spec trailing/partial-TP separately and A/B against the P1 baseline curve.
