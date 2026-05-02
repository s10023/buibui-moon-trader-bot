# EMA Strategy — Design Spec

**Date:** 2026-05-02
**Status:** Draft — open questions in §10
**Author:** s10023

---

## 1. Overview

Add a **Variant A (pullback)** EMA trend-continuation strategy named `ema`, both directions
(long + short), with an in-strategy `is_trending()` regime gate to suppress range-bound chop.

This is the first detector in buibui that uses moving averages as the primary trigger. All
prior strategies are structural (BOS, FVG, OB), candlestick-pattern (engulfing, pin_bar), or
flow-based (CVD divergence, liquidity sweep). The EMA strategy slots into a new — or expanded —
"Trend" taxonomy bucket (open question §10.1).

The strategy is pure OHLCV math: no Binance / Alpaca / exchange coupling. It transfers
verbatim to the Buibui Wifey Wall Street Bot fork.

---

## 2. What's Decided

| Decision | Choice | Rationale |
| --- | --- | --- |
| Variant | A — pullback to EMA + rejection | Most-traded "trend continuation" interpretation; cleanest fit with existing `tp_r` / structural-SL framework |
| Directions | Long + Short | Symmetric by design; backtest will reveal directional skew |
| Name | `ema` | Short, future-proof for variants (`ema_cross`, `ema_ribbon` ship later as siblings) |
| Range gate | In-strategy `is_trending()` (cross-count + slope) | Helper in `_shared.py`; opt-in for other strategies later; D9 global filter remains a separate future track |
| EMA library | None — hand-written `compute_ema()` | One-line pandas call (`series.ewm(span=N).mean()`); no `ta-lib` / `pandas-ta` dependency |
| Default periods | EMA20 (fast) / EMA50 (slow) | Most common day/swing default; per-TF sweep tunes after baseline |
| Taxonomy bucket | New `"Trend"` group in `STRATEGY_TYPE_GROUPS` | First trend-following detector; will hold future `ema_cross`, `ema_ribbon`, ADX-based work |
| Slope unit | Percent (not ATR-normalised) | Per-symbol/TF tuning happens via WFO sweep; ATR-normalised variant deferred unless backtest shows symbol-bias |
| Pullback wick depth | Touch only at v1 | `min_pullback_atr_pct` added to a follow-up sweep grid after baseline numbers exist |
| Trigger timing | Fire on trigger candle, entry next open | Confluence (D10) is the right layer to add quality; stretching trigger window over-fires per pullback and bleeds avg R |
| Combo strategy | Decided by backtest | No pre-set whitelist or `INCOMPATIBLE_PAIRS` at launch |
| Live rollout | All three `signal_watch*.toml` configs at once | No staging; user preference 2026-05-02 |

---

## 3. Detection Logic

For each candle in the input OHLCV DataFrame, evaluate in order. Skip the candle if any check fails.

### 3.1 Trend filter (which side are we trading?)

```text
trend = "up"   if close > EMA(slow) and EMA(slow).slope > 0
trend = "down" if close < EMA(slow) and EMA(slow).slope < 0
trend = None   otherwise   → skip
```

`slope` = `(EMA(slow)[t] − EMA(slow)[t − slope_lookback]) / EMA(slow)[t − slope_lookback]`,
default `slope_lookback = 10`.

### 3.2 Regime gate (`is_trending()`)

```text
cross_count   = number of price/EMA(fast) crosses in last regime_lookback bars
slope_pct     = |slope of EMA(slow) over slope_lookback bars|, in percent

is_trending = (cross_count ≤ max_crosses) AND (slope_pct ≥ min_slope_pct)
```

Defaults: `regime_lookback = 20`, `max_crosses = 2`, `slope_pct ≥ 0.3 %`.

If `is_trending` is `False` → skip (range/chop).

### 3.3 Pullback (the retrace itself)

Within the last `pullback_lookback` candles (default `5`):

- **Long**: at least one candle's `low ≤ EMA(fast)` AND its `close > EMA(fast)` (touched the
  EMA from above and held)
- **Short**: at least one candle's `high ≥ EMA(fast)` AND its `close < EMA(fast)` (touched
  from below and rejected)

This single rule captures "wick into EMA, body on the trend side" without needing separate
"touch" / "close-near" / "cross-and-recover" variants.

### 3.4 Trigger (the entry candle)

The current candle (the one we evaluate at) must:

- **Long**: be bullish (`close > open`) AND `close > EMA(fast)` AND body ≥ `min_body_pct` of range
- **Short**: be bearish (`close < open`) AND `close < EMA(fast)` AND body ≥ `min_body_pct` of range

Default `min_body_pct = 0.5` (50 % of high-low range).

Entry: next candle open.

### 3.5 SL and TP

| Side | SL | TP |
| --- | --- | --- |
| Long | Low of the pullback wick (lowest low among pullback candles in §3.3) | `entry + tp_r × (entry − sl)` |
| Short | High of the pullback wick (highest high among pullback candles) | `entry + tp_r × (sl − entry)` (negative) |

`tp_r` default `3.0`. WFO sweep tunes per timeframe (per `feedback_sweep_tools_distinction.md`,
trust `/wfo-sweep` over full-dataset sweep for production).

---

## 4. Parameters (`ParamSpec`)

| Param | Default | Range for sweep |
| --- | --- | --- |
| `fast_period` | 20 | 9, 13, 20, 21, 34 |
| `slow_period` | 50 | 50, 89, 100, 200 |
| `slope_lookback` | 10 | 5, 10, 20 |
| `regime_lookback` | 20 | 10, 20, 30 |
| `max_crosses` | 2 | 1, 2, 3, 4 |
| `min_slope_pct` | 0.003 (0.3 %) | 0.001, 0.002, 0.003, 0.005 |
| `pullback_lookback` | 5 | 3, 5, 7, 10 |
| `min_body_pct` | 0.5 | 0.3, 0.5, 0.7 |
| `tp_r` | 3.0 | 2.0, 2.5, 3.0, 4.0, 5.0 |

Constraint: `fast_period < slow_period` always. Sweep enforces.

---

## 5. Files to Create / Modify

Per the `/new-strategy` 4-file checklist (CLAUDE.md):

| File | Action | Purpose |
| --- | --- | --- |
| `analytics/strategies/_shared.py` | Modify | Add `compute_ema()`, `is_trending()`, `ema_cross_count()` helpers |
| `analytics/strategies/ema.py` | **Create** | `detect_ema()` — main detector |
| `analytics/strategies/_registry.py` | Modify | Add `STRATEGY_REGISTRY` entry, `DETECTOR_REGISTRY` mapping, `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS` |
| `signals/registry.py` | Modify | Add `SIGNAL_REGISTRY` entry (Telegram alert plugin) |
| `tests/test_strategies_ema.py` | **Create** | Unit tests — synthetic OHLCV fixtures for: trend long fires, trend short fires, range chop suppressed, pullback absent → no fire, slope flat → no fire, body < 50 % → no fire |
| `analytics/strategies/_shared.py` (helper tests) | New tests in `tests/test_strategies_shared.py` | Unit-test `compute_ema`, `is_trending`, `ema_cross_count` directly with mock series |

No edits to `signals/cooldown_store.py`, `signals/alert_formatter.py` (alert format auto-inherits
from `SignalEvent`), or `analytics/signal/scanner.py` (scanner reads from `STRATEGY_REGISTRY`).

---

## 6. STRATEGY_REGISTRY Entry (sketch)

```python
StrategySpec(
    name="ema",
    detector="ema",                     # maps to detect_ema in DETECTOR_REGISTRY
    strategy_type="trend",              # NEW "Trend" group in STRATEGY_TYPE_GROUPS
    description="EMA pullback continuation",
    params=ParamSpec(
        fast_period=20,
        slow_period=50,
        slope_lookback=10,
        regime_lookback=20,
        max_crosses=2,
        min_slope_pct=0.003,
        pullback_lookback=5,
        min_body_pct=0.5,
    ),
    tp_r=3.0,
    tp_r_long=None,                     # populate after directional sweep
    tp_r_short=None,
    volume_suppress=False,              # to be set after /volume-sweep
    volume_spike_boost=False,
)
```

`KNOWN_STRATEGY_TYPES` adds a new `"trend"` value; `STRATEGY_TYPE_GROUPS` adds a `"Trend"` group containing `["ema"]`.

`INCOMPATIBLE_PAIRS` — no entries at launch. Revisit after backtest if `ema` and any specific
strategy fire together with worse outcomes than either alone.

---

## 7. Telegram Alert (auto-inherited)

`signals/alert_formatter.py` formats from `SignalEvent` — no per-strategy code path needed.
Alert subject: `"📈 EMA Pullback Long — BTCUSDT 1h"` (auto-built from `name` + `direction`).
Confluence blockquote (D10) auto-renders if other strategies co-fire on the same candle.

For the equity fork's wife channel: `direction="long"` rows survive the dispatcher filter and
the label rewrite turns "Long" → "Buy" in the subject and body. No special EMA code path.

---

## 8. Backtest Plan

Sequence (each step gates the next):

1. **Baseline (gate OFF)** — `make buibui-backtest CONFIG=config/signal_watch.toml SAVE=1`
   with `is_trending` always returning `True`. Establishes "EMA pullback without regime filter"
   numbers per symbol × TF.
2. **Gate ON, defaults** — re-run with §3.2 defaults active. Compare avg_r, win rate, and
   trade count vs baseline. Acceptance: gate must improve avg_r or maintain it while cutting
   trade count by > 30 %.
3. **WFO sweep** — `/wfo-sweep` chain on the full param set in §4. Picks per-TF optimal
   `(fast_period, slow_period, tp_r)` and writes `tp_r` to `signal_watch.toml`.
4. **Volume sweep** — `/volume-sweep` to set `volume_suppress` and `volume_spike_boost` flags.
5. **Directional sweep** — re-run sweep with split `tp_r_long` / `tp_r_short` if directional
   skew is large (per `plan_directional_volume_suppress.md` precedent).
6. **Recalibrate** — `make db-update` chain refreshes `confidence_ratings` star ratings and
   regression goldens.
7. **Combo refresh** — `make buibui-backtest CONFIG=config/signal_watch.toml COMBO=1 SAVE=1`
   so `ema` enters the combo confluence picture. Memory currently flags `backtest_combos` as
   stale anyway — this clears that debt.

Acceptance threshold for shipping live: ≥ 3-star rating after recalibrate on at least one
(symbol, TF) combo, no combo with avg_r < 0.

---

## 9. Implementation Sequence

1. Branch `feat/ema-strategy` from `main`.
2. **Helpers** — add `compute_ema`, `is_trending`, `ema_cross_count` to `_shared.py` + tests.
3. **Detector** — write `detect_ema()` in `analytics/strategies/ema.py` + unit tests.
4. **Registry wiring** — `_registry.py` (5 entries) + `signals/registry.py`.
5. **Lint + typecheck + test** — `make lint-py typecheck test`.
6. **Backtest §8 step 1–2** — sanity check before opening PR. PR description includes
   baseline-vs-gate numbers.
7. **Open PR** for review. Squash-merge on green CI + green numbers.
8. **Post-merge**: `/wfo-sweep` (§8 step 3), `/volume-sweep` (step 4), `/recalibrate`,
   combo refresh (step 7). Each is its own small PR or direct-to-main commit per
   `feedback_strategy_changes_backtest.md`.

---

## 10. Resolved Decisions (2026-05-02)

1. **Taxonomy bucket** → New `"Trend"` group in `STRATEGY_TYPE_GROUPS`. First entry; future siblings (`ema_cross`, `ema_ribbon`, ADX-based work) join it.
2. **Slope unit** → Percent. WFO sweep tunes per symbol/TF; ATR-normalised variant deferred unless backtest shows symbol-bias.
3. **Pullback wick depth** → Touch only at v1. Add `min_pullback_atr_pct` to the sweep grid in a follow-up branch after baseline numbers exist.
4. **Trigger timing** → Fire on trigger candle, entry next open. Rationale: confluence (D10) is the right layer to add quality; stretching the trigger window over multiple bars over-fires per pullback. Revisit only if `signal_test` replay shows under-firing on multi-bar reactions.
5. **Combo strategy** → Backtest decides. No pre-set whitelist or `INCOMPATIBLE_PAIRS` at launch.
6. **Live rollout** → All three `signal_watch*.toml` configs at once, no staging.

---

## 11. Out of Scope (this design)

- **HTF EMA gate (F8)** — global D1 EMA slope filter applied to all strategies. Distinct
  feature; lives in its own spec when it ships.
- **EMA cross variant (B)** and **EMA ribbon variant (C)** — sibling detectors, not part of
  this design. Will reuse `compute_ema()` from `_shared.py` when they land.
- **D9 regime detector** — global trend/range classifier with strategy-type routing. The
  `is_trending()` helper here is intentionally narrower (binary, in-strategy) and graduates
  later if/when D9 promotes it.
- **ADX / `pandas-ta` dependency** — replacement for the cross-count + slope heuristic.
  One-line swap if backtest shows the current heuristic underperforms; not adopted upfront.

---

## 12. Next

All design questions resolved. Spin `feat/ema-strategy` and execute §9.
