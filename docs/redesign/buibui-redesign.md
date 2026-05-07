# Buibui v2 — Focused Architecture Redesign

Audit anchor: `/tmp/buibui-system-breakdown.md` (2026-05-06).

---

## 1. CORE DESIGN PHILOSOPHY

**The edge: liquidity reversion + structural pullback continuation, gated by HTF trend and confirmed by derivatives positioning.**

In one sentence: *the bot fades liquidity raids at obvious levels, OR enters with HTF trend on an OTE-zone pullback after BOS — nothing else.*

**Why this fits the constraints**:

- A 9–5 job + 1–2 hour US-session window means **the bot must be selective, not prolific**. Fewer, higher-conviction signals beat noisy fan-out. Reversal/liquidity setups cluster around session opens and HTF structure — exactly the windows you trade.
- "No overnight holding" → the bot must respect a hold-time budget. Pullback continuation has a clean invalidation (BOS swing); liquidity reversal has a clean invalidation (sweep wick). Both are short-fuse setups that fit a 4–12 h hold.
- HYBRID preference (lean reversal but don't miss trend) is solved by **two setups, one model**: same trigger logic, opposite context. Reversal = sweep into HTF resistance/support; continuation = pullback in HTF trend direction.
- Manual now, auto later → the architecture must produce **decisions, not opinions** (one row per actionable trade with explicit entry/SL/TP/expectancy). Today the user reads it; tomorrow an executor reads the same row.

**The system intentionally IGNORES**:

- Single-candle patterns (pin/hammer/engulfing/doji/marubozu/inside/star) as standalone triggers — these become *confirmation features only*.
- Synthetic CVD (we have no real delta — drop it).
- Strategies that fire too often (every detector that produces >1 signal/day per symbol/TF on average).
- Asia-session signals on USDT pairs (low liquidity, low edge, no user attention).
- 1m/5m timeframes (forever — no user oversight, no auto-exec yet).
- Funding-rate-only setups (`funding_extreme` is dead — don't resurrect it; use funding only as a *modifier*).

---

## 2. SYSTEM STRUCTURE — DUAL MODE

### Mode A — Swing / Hands-off (daytime / 9–5)

- **Timeframes**: **4h primary, 1d for HTF bias** only.
- **Strategy**: Structural pullback continuation (OTE 0.618–0.786 retracement after 4h BOS) + liquidity reversion at 1d EQH/EQL when HTF is range-bound.
- **Holding time**: 12–48 h target. Hard cap = 72 h (auto-flatten or "stale" alert).
- **Signal frequency target**: **0–3 signals per symbol per WEEK**. Anything more means the gate is leaking.
- **Risk style**: Wider structural SL (4h swing), smaller size, `tp_r ∈ [3, 5]`. One alert during the day at most. **No "act now" pressure** — alert is a candidate for the evening review.

### Mode B — Session / Active (US session, ~21:00–02:00 MYT / NY 09:00–14:00)

- **Timeframes**: **15m primary, 1h for context, 4h+1d for HTF bias** only (read-only, no triggers).
- **Strategy**: Liquidity sweep reversion at session H/L (Asia high/low, prior day H/L, weekly H/L) + 15m OTE pullback inside the 1h trend.
- **Holding time**: 1–6 h. Hard cap = US session close (must be flat by 03:00 MYT until proven).
- **Signal frequency target**: **0–3 signals per session across all symbols** (not per symbol).
- **Risk style**: Tight SL (sweep wick or fast EMA), `tp_r ∈ [1.5, 3]`, can take partials at 1R, runners to structure.

**Why two modes (not one)**: same edge, different *trigger fidelity*. Mode A is a **filter** (HTF only — survives the 9-hour blackout). Mode B is a **trigger** (LTF, fast invalidation, runs only when you can watch). They share the same context layer but emit alerts on different schedules. Mode A may pre-arm a level overnight; Mode B fires the actual trigger when liquidity is taken in NY.

---

## 3. STRATEGY REFACTOR — AGGRESSIVE CUTS

> **AMENDED 2026-05-07** — the wholesale-delete plan below is superseded by the data-driven action map in [`buibui-redesign-phase0-findings.md`](buibui-redesign-phase0-findings.md) §1. Phase 0 audit (698,758 trades) found **0 KILL / 0 DEMOTE / 19 KEEP** at the strategy-wide level: every detector has positive-edge slices somewhere. The operative cuts are now per-(strategy × timeframe), not strategy-wide.
>
> Headline contradictions of the original §3 below:
>
> - `liquidity_sweep` is **NOT** a "core edge" — only 1d works; 15m/1h/4h are 0–22% positive (DROP_TF).
> - `cvd_divergence` is **NOT** synthetic fiction — 1h is 80% positive, +0.304 wgt_avg_r (KEEP 1h).
> - `pin_bar`/`engulfing`/`doji` are **NOT** bloat — all work on 1h+ at ≥58% positive (KEEP alerter).
> - `wick_fill`, `marubozu`, `fvg` ARE universally dead → original §3 deletion is the only one fully justified.
>
> The categories below are preserved as the original architectural intent but are **not** the operative Phase 1 plan. Phase 1 cleanup must follow the action map in the findings doc.

### Family 1: Liquidity & Structure (the core)

- **KEEP — `liquidity_sweep`** (fib-extension mode, 1.27/1.13). This is the cleanest reversion edge.
- **KEEP — `eqh_eql`** (1h+4h+1d). Equal-level liquidity pools are the textbook reversion target.
- **KEEP — `smt_divergence`** (BTC↔ETH, 15m+1h+4h only). Highest-conviction reversion confirmation.
- **KEEP — `bos` (market_structure)** but **NOT as a signal — as a *context* component**. BOS becomes the prerequisite for OTE/pullback entries, not a standalone alert.

### Family 2: Trend / Pullback Continuation

- **KEEP — `ote_entry`** (0.618–0.786 after BOS). The single trend-continuation setup.
- **REMOVE — `fib_golden_zone`** (0.5–0.618). Duplicates `ote_entry` with a shallower zone. Pick one (OTE wins — deeper zone = better R).
- **REMOVE — `ema` pullback detector**. WFO killed it (only 1 of 24 cells passes; gate axes falsified 2026-05-06). EMA stays as a *context indicator only*.
- **REMOVE — `bos` standalone alerts**. Already a continuation prerequisite; alerting on raw BOS is noise.

### Family 3: Order Blocks / FVG

- **SECONDARY — `order_block`** (4h, 1d only). Used as a *confluence boost* for OTE/sweep setups; never alerts standalone.
- **SECONDARY — `fvg`** (4h only). Confluence boost only. Drop 15m/1h FVG entirely.

### Family 4: Candlestick Patterns (the bloat)

- **REMOVE all from production**: `pin_bar`, `engulfing`, `hammer_hanging_man`, `doji`, `inside_bar`, `marubozu_retest`, `morning_evening_star`, `wick_fills`, `trend_day`.
- **REPURPOSE**: keep the *shape detectors* as a `confirmation_features` library. Pin/engulfing become a +1 score on the unified signal; never a standalone alert.

### Family 5: Session

- **REMOVE — `orb` (Opening Range Breakout)**. UTC-anchored ORB is meaningless for crypto with NY-driven flow. Replaced by Mode B's session-extreme sweep logic which is anchored to *Asia/London/NY* sessions.

### Family 6: Volume / Flow

- **REMOVE — `cvd_divergence`**. Synthetic CVD is a fiction. Resurrect only when real Spot/Futures delta is wired (CoinGlass).
- **REMOVE — `funding_extreme`**. Dead (no funding data). Funding is reborn as a *modifier* in §5.

### Net result

**From 20 detectors → 4 alerting setups + 4 confluence-only features.**

| Active alerters (4) | Setup |
| --- | --- |
| `sweep_reversion` | liquidity_sweep + eqh_eql merged |
| `smt_reversion` | smt_divergence (BTC/ETH only) |
| `ote_continuation` | OTE 0.618–0.786 after BOS |
| `session_sweep` (Mode B only) | sweep of session H/L during NY |

| Confluence-only features | Used as score boosts |
| --- | --- |
| Order block proximity | +1 if entry inside OB |
| FVG fill | +1 if entry inside unfilled FVG |
| Candlestick trigger (pin / engulfing) | +1 if signal candle has clean rejection |
| HTF EMA bias (F8) | already present — kept as gate |

---

## 4. NEW UNIFIED SIGNAL MODEL

One model. One scoring function. Four setup types feed it.

```text
SignalCandidate {
  setup_type     : sweep_reversion | smt_reversion | ote_continuation | session_sweep
  symbol, tf, direction
  entry, sl, tp_levels[]   (TP1=1R, TP2=structure, TP3=tp_r * sl_dist)
  context_score  : 0..3   (HTF bias, regime fit, level significance)
  confirm_score  : 0..3   (volume, OB/FVG confluence, candlestick trigger)
  derivatives    : 0..3   (OI delta, funding, liquidation spike — see §5)
  total_score    : 0..9
  expected_R     : float   (from cached backtest of this setup_type at this TF/symbol)
  hold_budget_h  : float   (Mode A: 48; Mode B: 6)
}
```

### Decision flow (single path, no parallel detectors)

1. **Candle close** on 15m / 1h / 4h / 1d.
2. **Regime classifier** runs first (§6) → returns `{trend|range|high_vol|low_vol}` for that symbol/TF.
3. **Setup detector dispatch** based on regime:
   - `trend` → only `ote_continuation` and `session_sweep` (with-trend) are eligible.
   - `range` → only `sweep_reversion`, `smt_reversion`, `session_sweep` (counter-trend) are eligible.
   - `high_vol` → only sweep types eligible (continuation off — too whippy).
4. **Setup detected** → produces `SignalCandidate` with entry/SL/TP and a **`level_significance` score** (HTF level > LTF level; weekly EQH > daily EQH > 4h EQH).
5. **Context score**: HTF EMA-50 alignment (F8, kept) + level significance + regime fit → 0–3.
6. **Confirmation score**: `_is_volume_spike` on signal candle + OB/FVG confluence within entry zone + candlestick trigger shape (pin/engulfing) → 0–3.
7. **Derivatives score**: OI delta + funding + liquidation spike (§5) → 0–3.
8. **Hard gates** (any fails → drop):
   - HTF EMA F8 opposes (deadband 0.3%, hard).
   - ADR consumed ≥ 80% (existing gate).
   - **NEW**: Hold-time budget — would the trade still be open at 03:00 MYT? In Mode B, drop trades whose `expected_hold_h > hours_until_session_close`.
   - **NEW**: News blackout window (FOMC/CPI/NFP ±30 min) — drop unconditionally.
9. **Total score gate**: `total_score ≥ 6/9` for Mode A, `≥ 5/9` for Mode B (sample-size larger on LTF).
10. **EV gate**: `expected_R ≥ 0.5` from cached per-setup backtest.
11. **Cooldown / dedup**: per (symbol, setup_type) — 4 hours.
12. **Confluence stack**: if 2 candidates fire on same symbol/TF/direction within 2 candles, merge into one alert with summed score.

The four detectors call into the same scoring function. **No more "20 parallel signals". One pipeline.**

---

## 5. DERIVATIVES INTEGRATION (mandatory, exact usage)

### 5.1 Open Interest (OI) — already syncs, never used

Compute on the signal candle: `oi_delta_pct = (oi[t] - oi[t-N]) / oi[t-N]` over a window matched to the TF (N=8 for 15m, N=6 for 1h, N=6 for 4h).

| Setup | Bullish trigger | Bearish trigger | Score effect |
| --- | --- | --- | --- |
| `sweep_reversion` (long) | sweep low + price up + OI **down** = short squeeze | — | +2 (highest-conviction reversion) |
| `sweep_reversion` (short) | — | sweep high + price down + OI **down** = long squeeze | +2 |
| `ote_continuation` (long) | pullback + price up + OI **up** = real flow | — | +1 (continuation needs new flow) |
| `ote_continuation` (short) | — | pullback + price down + OI **up** = real flow | +1 |
| All setups | OI direction **agrees with price** on reversion setup | (suspect — likely trapped continuation) | -1 |

**Hard gate**: `sweep_reversion` with OI ↑ on the sweep candle is a **DROP** — that's not a squeeze, that's a breakout you're fading on the wrong side.

### 5.2 Funding rates — fetcher exists, never wired

Wire `fetch_funding_rates()` into `data_sync.sync()` (8h cadence). Compute z-score over rolling 30 days.

| Funding state | Effect on `sweep_reversion` short | Effect on `sweep_reversion` long | Effect on continuation |
| --- | --- | --- | --- |
| Funding z ≥ +2 (very positive — longs crowded) | **+2** confirmation | -1 (longs already paying — fading them is later) | -1 on long continuation, +1 on short continuation |
| Funding z ≤ -2 (very negative — shorts crowded) | -1 | **+2** | +1 on long, -1 on short |
| `\|z\| < 1` | 0 | 0 | 0 |

This is a **positioning extremity score**, not a standalone signal. It modifies conviction.

### 5.3 Liquidation spikes (optional — needs CoinGlass or estimator)

Without CoinGlass: **proxy liquidations from a wick + volume spike on the signal candle**: `liq_proxy = (wick_vs_direction / range > 0.6) AND (volume > 3 × 20-bar mean)`.

| State | Effect on `sweep_reversion` | Effect on continuation |
| --- | --- | --- |
| `liq_proxy = true` and aligned with sweep direction | +1 (cascading stops cleared) | 0 |
| `liq_proxy = true` and against sweep | DROP (still cascading; don't catch knife) | DROP |

When CoinGlass is wired (G2c), replace proxy with real long-liq / short-liq dollar volume on the signal candle.

### 5.4 Net derivatives behavior

Derivatives never *create* a signal. They **gate** (OI hard rule), **boost** (funding extremity, liq alignment), or **veto** (liq cascade against). This is the single most under-utilized data source today and the cheapest big lift.

---

## 6. REGIME CLASSIFIER (simple, pragmatic)

Compute once per scan cycle per (symbol, 4h):

```text
regime(symbol, tf=4h):
  ema50_slope_pct  = (EMA50[t] - EMA50[t-10]) / EMA50[t-10]
  atr_pct          = ATR14 / close
  atr_pct_p80      = rolling 90-day 80th percentile of atr_pct

  if atr_pct >= atr_pct_p80:           return "high_vol"
  elif abs(ema50_slope_pct) >= 0.005:  return "trend"   # 0.5% over 10 bars
  else:                                return "range"
```

The 4h regime is the primary state for both modes. 1d slope used as a tie-breaker for long-hold Mode A trades.

### Strategy enablement matrix

| Regime | sweep_reversion | smt_reversion | ote_continuation | session_sweep |
| --- | --- | --- | --- | --- |
| `trend` | with-trend only (counter-trend dropped) | with-trend only | **enabled** (this is its regime) | with-trend only |
| `range` | **enabled both directions** | **enabled** | dropped (no trend = no continuation) | **enabled** |
| `high_vol` | enabled but **size ÷ 2** | enabled, size ÷ 2 | dropped (whippy) | enabled, size ÷ 2 |

Behavior changes:

- In `high_vol`, widen SL to `1.5 × structural` and halve risk.
- In `range`, prefer 4h levels (cleaner) over 1h.
- In `trend`, the F8 EMA gate becomes **strict** (deadband → 0.001).

This replaces the current diffuse "F8 + ADR + day_filter" stack with a **single regime decision** that drives both strategy gating and risk sizing.

---

## 7. RISK & PORTFOLIO LAYER (manual-first, auto-ready)

Lightweight rules, deterministic, can be promoted to an executor verbatim.

### Per-trade risk

- **Risk = 0.5–1.0% of equity** per trade (lower in `high_vol`, upper in `range` with high score).
- Position size formula: `qty = (equity × risk_pct) / sl_distance` — emit in alert as `Position size: 0.012 BTC ($X)`.
- **Round-down** to symbol step size; **abort** if computed qty < exchange min (don't take a trade you can't size).

### Concurrency caps

- **Max 2 concurrent trades** total (one for each of you and Mr. Watch).
- **Max 1 per symbol** at any time, regardless of direction (BTC long + BTC short on different TFs = NO — pick higher score).
- **Max 1 per direction across correlated symbols** (BTC long + ETH long counts as 2; with cap=2 total this is the explicit ceiling).

### Score-based sizing

| total_score | risk_pct | tp split |
| --- | --- | --- |
| 5/9 (Mode B floor) | 0.5% | TP1=1R (50%), TP2=2R (50%) |
| 6–7/9 | 0.75% | TP1=1R (33%), TP2=2R (33%), TP3=tp_r (33%) |
| 8–9/9 | 1.0% | TP1=1R (25%), TP2=2R (25%), TP3=tp_r (50%) |

### Multi-signal handling

- Two candidates same symbol/TF/direction → **merge** (already in §4 step 12).
- Two candidates same symbol opposite direction → **emit higher-score, drop lower; if tied, drop both** (uncertain market).
- Two candidates different symbols → both eligible up to concurrency cap; **rank by `total_score × expected_R`**.

### Daily loss circuit-breaker

- **`daily_R_realized ≤ −2R` → block all new alerts until next UTC day.** Logged + Telegram-notified.
- **3 consecutive losses on the same `setup_type` → mute that setup for 24 h** ("local cold streak" gate).

### Manual-first translation

All of the above runs in alert formatter today: alert says "✅ ELIGIBLE — risk 0.75% / size 0.012 BTC" or "🚫 BLOCKED — daily loss limit reached". When you flip the switch to auto-exec, the same logic moves to the executor without re-derivation.

---

## 8. EXECUTION MODEL (manual now, auto later)

### Order placement

- **Entry: LIMIT order at signal candle close ± 5 bps** (favor inside the candle range).
- **Validity window**: 1 candle. If unfilled at next close → cancel and abandon (the setup is stale; don't chase).
- **SL: stop-market** at structural level (already computed).
- **TP: limit orders, partial fills as per §7 score table**.

### Slippage handling

- Budget `0.05%` slippage on entry, `0.10%` on stop (market). Reflect in backtest fee model.
- If LIMIT fills at price worse than `entry + 5 bps` (long) — **abandon trade** (post-fill veto).

### Alert structure (manual exec — designed to also be machine-readable)

```text
🔵 LONG  BTCUSDT  4h  ote_continuation  ★★★★☆  6/9
─────────────────────────────────────────────
Entry  : 67,420  (limit, valid until 04:00 UTC)
SL     : 66,580  (-1.25%)   |  Risk: 0.75% = $X
TP1    : 68,260  (1R, 33%)
TP2    : 69,100  (2R, 33%)
TP3    : 70,360  (3.5R, 34%)  Expected hold: ~14h
─────────────────────────────────────────────
Setup       : BOS-up @ 4h, OTE 0.71 retrace
Context     : 1d EMA-50 slope +0.7% (trend) | ADR 38%
Confirm     : OB confluence ✓ | Volume spike 3.2× ✓
Derivatives : OI +2.1% (real flow) | Funding z=+0.4 (neutral)
─────────────────────────────────────────────
Concurrency : 1/2 used | Daily R: +0.4 | Setup mute: off
```

The alert is now a **trade decision sheet**, not a description. Same fields will be a JSON payload to the executor in Phase 4.

---

## 9. WHAT TO REMOVE / SIMPLIFY (delete list)

> **AMENDED 2026-05-07** — Phase 0 audit found only 3 detectors universally dead (`wick_fill`, `marubozu`, `fvg`). The remaining 11 detectors slated for deletion below have positive-edge slices on at least one timeframe. The operative Phase 1 plan is **TF-level disable in `signal_watch*.toml` `strategy_timeframes`**, not detector-file deletion. See `buibui-redesign-phase0-findings.md` §3 for the exact TOML diff.

### Detectors to delete

- `wick_fills`
- `marubozu_retest`
- `trend_day`
- `pin_bar` (as alerter — keep shape func as confirmation feature)
- `hammer_hanging_man` (as alerter — keep shape func)
- `doji` (as alerter — keep shape func)
- `engulfing` (as alerter — keep shape func)
- `inside_bar` (as alerter — keep shape func)
- `morning_evening_star` (as alerter — keep shape func)
- `cvd_divergence` (delete entirely — synthetic, dishonest)
- `funding_extreme` (delete entirely — dead, will reappear as a feature)
- `fib_golden_zone` (delete — duplicates `ote_entry`)
- `bos` standalone alerts (becomes a context component)
- `orb_breakout` (delete — UTC anchor irrelevant; replaced by session_sweep)

### Indicators / helpers to remove

- `_compute_atr14` ATR SL path in backtest engine — **already dead** (F9). Delete the branch and the `atr_sl_multiplier` config knob entirely.
- `seasonality` package — never alerts; surface in Stats UI only or delete.
- `compute_ema` cross-counter for `is_trending()` — replaced by regime classifier.
- All per-strategy `tp_r_*` overrides on deleted strategies (huge TOML simplification).

### Logic to simplify

- **Replace day_filter / ADR / F8 / DOW soft suppress** stack with the single regime classifier (§6) that subsumes the same intent in fewer rules.
- **Replace 3 TOML files** (`signal_watch.toml`, `signal_watch_weekdays.toml`, `signal_watch_all.toml`) with **2** (`mode_a_swing.toml`, `mode_b_session.toml`). The current trio fragments tp_r calibration across day_filters with no good reason.
- **Replace `min_avg_r` per-strategy gate** with per-`setup_type` gate (4 entries instead of ~20).
- **Drop `signals.outcome` forward-scan TODO and instead** persist the alert directly with the realized live outcome from the executor (Phase 4) — short-circuits the whole "live vs backtest" calibration drift.
- **Remove `INCOMPATIBLE_PAIRS`** — irrelevant once setups are unified.

Net code delta (estimate): **~40% of `analytics/strategies/` deleted**, ~30% of `signal_watch.toml` deleted, +1 small `regime.py` module, +1 `derivatives_score.py`.

---

## 10. IMPLEMENTATION ROADMAP

### Phase 1 — Cleanup (1 week, no behavior risk)

1. Branch `feat/v2-cleanup`. Delete the 11 detectors listed in §9. Delete the dead ATR SL path. Delete `cvd_divergence` and `funding_extreme`.
2. Collapse the 3 `signal_watch_*.toml` configs into `mode_a_swing.toml` (4h+1d, no day_filter — regime handles it) and `mode_b_session.toml` (15m+1h, NY session window).
3. Move `pin_bar`/`engulfing`/`hammer`/`inside_bar`/`doji`/`marubozu`/`morning_evening_star` shape functions into `analytics/features/candlestick.py` as boolean confirmation helpers. No alerter exposure.
4. Update `STRATEGY_REGISTRY` to 4 entries: `sweep_reversion`, `smt_reversion`, `ote_continuation`, `session_sweep` (`session_sweep` initially aliases `sweep_reversion` with a session window — split in Phase 2).
5. Run `make db-update` + regression. Goldens will change — regenerate intentionally; tag the commit `v2-baseline`.

### Phase 2 — Core edge consolidation (1–2 weeks)

1. Add `analytics/regime.py` with the 4h regime classifier (§6). Wire it into `scan_symbol`. Replace F8/ADR/day_filter logic with regime gating.
2. Implement the unified `SignalCandidate` model (§4) and rewire the 4 setups to feed it. Delete the parallel-detector loop.
3. Build `confirmation_score` from existing volume gate + OB/FVG confluence + candlestick features. Build `context_score` from HTF EMA + level significance + regime fit.
4. WFO sweep `tp_r` per `setup_type` × TF (12 cells, not ~150). Apply via `/wfo-sweep`. Recalibrate stars.
5. Add session-aware `session_sweep` detector (Asia H/L, prior day H/L, previous week H/L as level sources; trigger only during NY session window in Mode B).

### Phase 3 — Derivatives integration (1–2 weeks)

1. **Wire funding into `data_sync.sync()`** (this is a 1-day fix and unblocks the funding score).
2. Implement `derivatives_score.py` with the OI delta, funding z-score, and liq-proxy logic from §5.
3. Backtest each `setup_type` with derivatives_score thresholds — measure uplift. Promote to `total_score` only if `Δavg_r ≥ +0.05R`.
4. Stand up CoinGlass adapter as design-only stub (per existing G2c plan) — schema, fetcher signature, UI placeholder. Don't subscribe yet.

### Phase 4 — Risk + execution improvements (2–3 weeks)

1. Implement the risk engine in `risk/engine.py`: per-trade risk %, concurrency caps, daily loss circuit-breaker, setup mute on cold streaks. Run in shadow first — alert says "would block" without actually blocking, log accuracy for 2 weeks.
2. Restructure the alert format to the §8 decision-sheet layout. Add `Position size: X` line that resolves the manual sizing burden.
3. Build a thin `executor/` package (limit-order placement, SL stop-market, partial TP, post-fill slippage veto). Wire it to consume the same `SignalCandidate` JSON the alerter emits — **zero new logic**, just a different sink.
4. **Auto-mode rollout**: dry-run for 30 days (executor runs, places paper orders, no real fills) before flipping `LIVE=true`. Daily P&L delta vs alerts is the acceptance test.

### Out-of-roadmap (do later, deliberately)

- D10 confluence layers / cross-TF combo backtests (already heavily explored — diminishing returns until v2 is stable).
- Overnight holding (only after auto-exec has 30 sessions of green dry-run).
- Equity additions (Wifey Wall Street fork) — port v2 architecture to Alpaca only after Phase 4 ships.

---

**Bottom line**: the system today is a 20-detector polling loop bolted to a Telegram channel. v2 is a single decision pipeline with two operating modes, 4 setups, derivatives-aware scoring, and a deterministic risk layer that translates 1:1 to an executor. The audit's biggest finding (no edge concentration, no risk engine, no execution layer) is exactly what these 4 phases fix in order.
