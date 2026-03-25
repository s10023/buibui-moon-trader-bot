# Strategy Documentation Audit

**Date:** 2026-03-25
**Scope:** All 21 active strategies registered in `STRATEGY_REGISTRY`

---

## Critical Issues

### 1. ORB — Single-candle range, only checks next candle, wrong session anchor (FIXED)

`detect_orb_breakout` uses a single candle at `session_hour_utc=13` (NYSE open) as the opening
range. Real ORB (Toby Crabel) marks the high/low across the first N minutes of the session.
The function also only checks the immediately next candle for breakout — all later candles on
the same day are ignored. `timeframe_minutes` is never passed by callers (`signal_lib.py` line
239, `backtest_runner.py`), so the guard `if timeframe_minutes >= 60` never fires; ORB runs
on 1h/4h/1d data and produces meaningless signals.

**Status:** Fixed and merged to main (#188) — rewritten with 00:00 UTC anchor, 2-candle range, all-day scan, per-day dedup, TP.

### 2. `funding_reversion` — no funding data ever in DB (SILENT FAILURE)

`fetch_funding_rates()` in `data_fetcher.py` is never called by `data_sync.py`. No funding rows
are ever written to the DB. `get_funding_rates()` always returns empty. The strategy silently
skips every cycle (debug log only). Its 4★ rating is misleading — the strategy has never fired
in production. Additionally `sl_price = 0.0` would produce a broken backtest (infinite SL
distance for longs) if data were ever present. Tracked in MEMORY.md Deferred Issues.

### 3. `fib_golden_zone` / `ote_entry` — multiple signals per BOS leg (NEEDS_REVIEW)

Consecutive candles closing inside the golden/OTE zone each generate an independent signal for
the same BOS leg. In backtest this creates overlapping trades on the same setup, inflating
signal count and distorting win-rate statistics. `_signals_to_df` dedup only removes exact
`open_time` duplicates.

---

## Summary Table

| # | Key | Full Name | Confidence | In SIGNAL_REGISTRY | Verdict |
| --- | ----- | ----------- | ------------ | --------------------- | --------- |
| 1 | `seasonality` | Seasonality Statistics | 2★ | No | CORRECT |
| 2 | `wick_fill` | Wick Fill | 2★ | Yes | CORRECT |
| 3 | `marubozu` | Marubozu Open Retest | 2★ | Yes | CORRECT |
| 4 | `orb` | Opening Range Breakout | 3★ | Yes | FIXED |
| 5 | `liquidity_sweep` | Liquidity Sweep + Reversal | 4★ | Yes | CORRECT |
| 6 | `fvg` | Fair Value Gap | 4★ | Yes | CORRECT |
| 7 | `bos` | Break of Structure / CHoCH | 3★ | Yes | CORRECT |
| 8 | `funding_reversion` | Funding Rate Mean Reversion | 4★ | Yes | **BROKEN** |
| 9 | `smt_divergence` | SMT Divergence | 5★ | Yes | CORRECT |
| 10 | `eqh_eql` | Equal Highs / Equal Lows | 4★ | Yes | CORRECT |
| 11 | `order_block` | ICT Order Block Retest | 4★ | Yes | CORRECT |
| 12 | `cvd_divergence` | CVD Divergence | 4★ | Yes | CORRECT |
| 13 | `trend_day` | Trend Day | 3★ | Yes | NEEDS_REVIEW |
| 14 | `engulfing` | Bullish / Bearish Engulfing | 2★ | Yes | CORRECT |
| 15 | `pin_bar` | Pin Bar | 2★ | Yes | CORRECT |
| 16 | `inside_bar` | Inside Bar Breakout | 2★ | Yes | CORRECT |
| 17 | `hammer_hanging_man` | Hammer / Hanging Man | 2★ | Yes | CORRECT |
| 18 | `doji` | Doji + Confirmation | 2★ | Yes | CORRECT |
| 19 | `morning_evening_star` | Morning / Evening Star | 3★ | Yes | NEEDS_REVIEW |
| 20 | `fib_golden_zone` | Fibonacci Golden Zone (BOS) | 4★ | Yes | NEEDS_REVIEW |
| 21 | `ote_entry` | OTE Entry (0.618–0.786) | 4★ | Yes | NEEDS_REVIEW |

Totals: 15 CORRECT · 4 NEEDS_REVIEW · 1 FIXED · 1 BROKEN

---

## Detailed Strategy Entries

### 1. seasonality

| Field | Value |
| ------- | ------- |
| **Source** | Statistical / quantitative analysis |
| **Definition** | Aggregate return stats by day-of-week, hour-of-day, and week-of-month to identify recurring seasonal patterns. |
| **Implementation** | `seasonality_stats(df)` groups OHLCV by timestamp breakdown, computes `avg_return_pct`, `win_rate`, `count` per bucket. Returns a stats DataFrame, not signals. |
| **Entry** | N/A — not a signal detector |
| **SL / TP** | N/A |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | No — intentionally excluded; produces stats, not actionable entry signals |
| **Known Issues** | None. |
| **Verdict** | CORRECT |

---

### 2. wick_fill

| Field | Value |
| ------- | ------- |
| **Source** | Price action / market microstructure — wicks represent liquidity grabs and unfilled imbalance |
| **Definition** | When a candle has a significant wick (≥ ratio × body), price tends to return and fill that wick zone. Signal fires when a later candle enters the zone and closes back inside. |
| **Implementation** | `detect_wick_fills` (line ~655). For each candle with `wick >= min_wick_body_ratio × body` (default 1.5×), scans next `lookback` (default 20) candles. Long: future candle's low enters lower wick zone AND closes above zone_bot. Short: symmetric. SL = wick tip. One signal per qualifying wick. |
| **Entry** | Next candle open after fill candle |
| **SL** | Structural: wick tip (zone_bot for long, zone_top for short) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | No context filter — fires on any wick without confirming S/R proximity or trend. |
| **Verdict** | CORRECT |

---

### 3. marubozu

| Field | Value |
| ------- | ------- |
| **Source** | Steve Nison — *Japanese Candlestick Charting Techniques* (1991). Marubozu = nearly wickless candle indicating conviction. |
| **Definition** | The open price of a bullish Marubozu (≤ 10% wicks) acts as support. Signal fires on first retest of that open price within `lookback` candles. |
| **Implementation** | `detect_marubozu_retest` (line ~734). Qualifies candles where both wicks ≤ `max_wick_ratio × body` (10%) AND body ≥ `min_body_pct × open` (0.5%). Long signal: future candle's low touches the open AND closes above it. Short: symmetric. SL = wick tip. |
| **Entry** | Next candle open after retest candle |
| **SL** | Structural: wick tip (very tight) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | SL at wick tip is extremely tight (can be < 0.1% on 4h) — noise stop-outs likely inflate loss rate. |
| **Verdict** | CORRECT |

---

### 4. orb

| Field | Value |
| ------- | ------- |
| **Source** | Toby Crabel — *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990). |
| **Definition** | Mark the high/low of the first 5–30 min of the session. Enter long on close above range high, short on close below range low. SL = opposite range extreme. |
| **Implementation** | `detect_orb_breakout`. Rewritten (#188): uses 00:00 UTC as session anchor, marks range from first 2 candles of each day, scans all remaining candles that day for breakout, per-day dedup prevents multiple signals. TP now calculated as `tp_r × sl_dist`. |
| **Entry** | First candle closing beyond range H/L on the same day |
| **SL** | range_low (long) or range_high (short) |
| **TP** | `tp_r × sl_dist` |
| **Confidence** | 3★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | DST: 00:00 UTC anchor is fixed; no adjustment for daylight saving transitions (tracked as A7). Re-run backtest to validate new logic. |
| **Verdict** | FIXED (merged #188) |

---

### 5. liquidity_sweep

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — "stop hunt" / liquidity grab. Price sweeps a swing level to trigger stops, then reverses. |
| **Definition** | Price wick exceeds a rolling swing high/low (stop cluster) but candle closes back inside → stop hunt → reversal signal. |
| **Implementation** | `detect_liquidity_sweep` (line ~890). Rolling high/low over `lookback` bars (shifted 1, no lookahead). Short: `candle_high > rolling_high × (1 + min_sweep_pct)` AND `close < rolling_high`. Long: symmetric. SL = candle wick tip. Monday context tag appended to reason string. |
| **Entry** | Next candle open after sweep candle |
| **SL** | Structural: candle_high (short sweep), candle_low (long sweep) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | No trend direction filter. Monday tag is informational only, not a gate. |
| **Verdict** | CORRECT |

---

### 6. fvg

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — 3-candle imbalance zone where `prev_high < next_low` (bullish) or `prev_low > next_high` (bearish). |
| **Definition** | Imbalance between candle[N-1] and candle[N+1] creates an unfilled zone. Signal fires when price returns to fill at the central equilibrium (CE = 50% of gap). |
| **Implementation** | `detect_fvg` (line ~956). Scans 3-candle windows. Gap filtered by `min_gap_pct` (0.1%). Scans next `lookback` (50) candles for first fill: `low <= CE AND close > gap_bot` (bullish). SL = gap_bot (long), gap_top (short). |
| **Entry** | The fill candle itself |
| **SL** | Structural: gap edge |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | CE entry (50% of gap) gives better R than gap-edge entry. No gap invalidation if price traded through it before returning. |
| **Verdict** | CORRECT |

---

### 7. bos

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — BOS = continuation; CHoCH = first reversal break. |
| **Definition** | Price breaks above last swing high (uptrend BOS) or below last swing low (downtrend BOS). CHoCH = first BOS against the established trend. |
| **Implementation** | `detect_market_structure` (line ~1042). Rolling non-centred swing detection using `2 × swing_lookback + 1`. Tracks `last_sh`, `last_sl`, `trend` state. Signal fires at swing confirmation candle. SL = last structural swing. |
| **Entry** | Next candle open after swing candle |
| **SL** | Structural: last swing low (long BOS), last swing high (short BOS) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 3★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Fires at the BOS swing point (already displaced). ICT BOS setups typically require a pullback before entering. Conceptual entry timing is aggressive; no mitigation. SL can be very wide. |
| **Verdict** | CORRECT (implementation matches stated design) |

---

### 8. funding_reversion

| Field | Value |
| ------- | ------- |
| **Source** | BitMEX blog (Oct 2017) — extreme positive funding (longs pay shorts) → market overloaded with longs → contrarian short. |
| **Definition** | When perpetual funding rate is extreme positive (> threshold) → short. Extreme negative → long. |
| **Implementation** | `detect_funding_extreme` (line ~1141). Merges OHLCV to funding via `pd.merge_asof`. Positive rate > `threshold` (0.001) → short. Negative < −threshold → long. `sl_price = 0.0` (no structural SL). |
| **Entry** | Next candle open after first candle following each funding period |
| **SL** | None (`sl_price = 0.0` → backtest uses `sl_pct` fallback, but `sl_pct` fallback also broken when `sl_price = 0.0` — sets infinite SL distance for longs) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | **CRITICAL: `data_sync.py` never calls `fetch_funding_rates()`.** No funding data in DB. Strategy silently skips every cycle. 4★ rating is misleading — strategy has never fired in production. `sl_price = 0.0` is also invalid. |
| **Verdict** | **BROKEN** |

---

### 9. smt_divergence

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — two correlated assets should make highs/lows together; divergence implies a stop hunt. |
| **Definition** | Bearish SMT: primary makes new swing high, secondary does not → primary's high is a stop hunt → short. Bullish: symmetric. |
| **Implementation** | `detect_smt_divergence` (line ~1212). Inner join primary+secondary on `open_time`. Rolling max/min (shifted 1). `trend_filter=1` (default) requires `close_p < ema50` for shorts, `close_p > ema50` for longs. SL = swing extreme. |
| **Entry** | Next candle open |
| **SL** | Structural: `curr_p_high` (short), `curr_p_low` (long) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 5★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Per backtest findings: mostly negative; only ETH/1h shows edge. Inner join silently produces no signals if timestamps don't align. Pair quality critical. |
| **Verdict** | CORRECT |

---

### 10. eqh_eql

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — double-tops/bottoms create visible stop clusters that get swept before reversal. |
| **Definition** | Two swing highs within tolerance (EQH) form a stop pool. When a candle's wick sweeps above both but closes below → stop hunt → short. |
| **Implementation** | `detect_eqh_eql` (line ~1305). Only checks the LAST candle in `df`. Scans prior `lookback` (50) candles for swing pairs within `tolerance_pct` (0.3%). SL = max high above EQH level. |
| **Entry** | The signal candle (last candle in df) |
| **SL** | Structural: max high above swept level |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Swing high detection uses `>=` — can produce many spurious swing points on flat markets. |
| **Verdict** | CORRECT |

---

### 11. order_block

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — the last candle opposing the displacement before a large move holds institutional orders. |
| **Definition** | Bearish OB: last bullish candle before significant bearish displacement. Its body is the OB zone. Short on retest. |
| **Implementation** | `detect_order_block` (line ~1449). Bearish: `ob_close > ob_open` AND `closes[i+1] < ob_low × (1 - displacement_pct)`. OB zone = `[ob_open, ob_close]`. Retest: future candle enters zone AND closes below zone_top. SL = `ob_high`. |
| **Entry** | The retest candle |
| **SL** | Structural: `ob_high` (bearish), `ob_low` (bullish) |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Displacement check is single-candle only (`closes[i+1]`). ICT concept allows multi-candle impulse. "Last bullish candle before displacement" only looks at immediate predecessor `i`. |
| **Verdict** | CORRECT |

---

### 12. cvd_divergence

| Field | Value |
| ------- | ------- |
| **Source** | Market microstructure — CVD (Cumulative Volume Delta) measures net buying vs selling pressure. |
| **Definition** | Bearish: price makes higher swing high, CVD makes lower swing high → buyers weakening → short. Bullish: symmetric. |
| **Implementation** | `detect_cvd_divergence` (line ~1545). Requires `taker_buy_volume`. CVD = `cumsum(2 × taker_buy_vol - vol)`. Checks last two swing highs/lows within `cvd_lookback` (50) candles. SL = structural swing extreme. |
| **Entry** | Swing high/low candle showing divergence |
| **SL** | Structural: swing extreme |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Only checks last two swings — ambiguous recent swing can produce false signals. CVD resets per call (tail cumsum), so absolute CVD not comparable between calls. |
| **Verdict** | CORRECT |

---

### 13. trend_day

| Field | Value |
| ------- | ------- |
| **Source** | Market profile literature — a trend day opens near one extreme and closes near the other with minimal pullback. |
| **Definition** | Strongly directional session: body ≥ 65% of range, minimal wicks. Signals continuation in the trend day's direction. |
| **Implementation** | `detect_trend_day` (line ~1649). `body_pct = abs(close-open)/(high-low) >= 0.65` AND `lower_wick_pct <= 0.15`. SL = candle_low (bullish), candle_high (bearish). |
| **Entry** | Next candle open after trend day candle |
| **SL** | Structural: candle low/high |
| **TP** | `tp_r × risk_distance` |
| **Confidence** | 3★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | **Conceptual gap:** entering next candle open after a trend day is entering at the likely top/bottom of the trend day move — chasing, not anticipating. Correct use is identifying the trend day *while forming* or using it as a filter to trade pullbacks. No daily TF filter (fires on 15m candles). |
| **Verdict** | NEEDS_REVIEW |

---

### 14. engulfing

| Field | Value |
| ------- | ------- |
| **Source** | Steve Nison — *Japanese Candlestick Charting Techniques* (1991). 2-candle reversal pattern. |
| **Definition** | Bullish: bearish candle followed by larger bullish candle whose body fully engulfs prior body → reversal. |
| **Implementation** | `detect_engulfing` (line ~1723). Body-only check (wicks ignored). `curr_open < prev_body_bot AND curr_close > prev_body_top`. `volume_confirm()` called but result is informational tag only — does not gate signal. SL = `entry × (1 ± sl_pct)`. |
| **Entry** | Engulfing candle close |
| **SL** | Percentage: `entry × (1 ± sl_pct)` |
| **TP** | Pre-computed: `entry ± sl_dist × tp_r` |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | No trend context filter. Body-only ignores wick story. Volume informational only. |
| **Verdict** | CORRECT |

---

### 15. pin_bar

| Field | Value |
| ------- | ------- |
| **Source** | Price action — Nial Fuller, Steve Nison. Small body + long rejection wick = strong level rejection. |
| **Definition** | Candle with `lower_wick >= wick_ratio × body` AND `upper_wick <= body` (bullish pin). Bearish: symmetric. |
| **Implementation** | `detect_pin_bar` (line ~1814). `volume_confirm()` informational only. SL = `entry × (1 ± sl_pct)`. |
| **Entry** | Pin bar candle close |
| **SL** | Percentage: `entry × (1 ± sl_pct)` |
| **TP** | Pre-computed |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | No S/R proximity or trend filter. Canonical SL is below/above wick tip (structural), not %. |
| **Verdict** | CORRECT |

---

### 16. inside_bar

| Field | Value |
| ------- | ------- |
| **Source** | Toby Crabel, general price action. Consolidation/indecision → breakout = direction resolution. |
| **Definition** | Inside bar's body contained within prior mother bar body. Breakout close above mother top → long; below bottom → short. |
| **Implementation** | `detect_inside_bar` (line ~1894). Body-only comparison. Signal fires on breakout candle (i+1). SL = `entry × (1 ± sl_pct)`. |
| **Entry** | Breakout candle close |
| **SL** | Percentage |
| **TP** | Pre-computed |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Body-only comparison (vs full H/L). No trend context. Canonical SL = below mother bar's low (structural). |
| **Verdict** | CORRECT |

---

### 17. hammer_hanging_man

| Field | Value |
| ------- | ------- |
| **Source** | Steve Nison — same shape as bullish pin bar; context determines reversal direction (hammer after downtrend, hanging man after uptrend). |
| **Definition** | Hammer: small body + long lower wick ≥ 2× body, appears after downtrend → bullish reversal. Hanging Man: same shape after uptrend → bearish reversal. |
| **Implementation** | `detect_hammer_hanging_man` (line ~1970). Same shape check as pin_bar. Context: `close[i] < close[i-N]` → downtrend → Hammer (long). `close[i] >= close[i-N]` → Hanging Man (short). SL = `entry × (1 ± sl_pct)`. |
| **Entry** | Candle close |
| **SL** | Percentage |
| **TP** | Pre-computed |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Trend context uses single lookback comparison — weak proxy for trend (one-tick difference qualifies). Canonical: check sequence of lower lows or EMA slope. |
| **Verdict** | CORRECT |

---

### 18. doji

| Field | Value |
| ------- | ------- |
| **Source** | Steve Nison — doji alone is indecision; requires a confirming candle. |
| **Definition** | Doji (open ≈ close, body ≤ 10% of range) followed by a strongly directional candle (body ≥ 60% of range) signals confirmed reversal/continuation. |
| **Implementation** | `detect_doji` (line ~2055). Doji: `abs(close-open) <= 0.1 × range`. Confirmation: `abs(next_close-next_open) >= 0.6 × next_range`. Signal fires on confirmation candle. SL = `entry × (1 ± sl_pct)`. |
| **Entry** | Confirmation candle close |
| **SL** | Percentage |
| **TP** | Pre-computed |
| **Confidence** | 2★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | No trend/S/R context. Confirmation candle (body ≥ 60%) heavily overlaps with `trend_day` — both may fire simultaneously. |
| **Verdict** | CORRECT |

---

### 19. morning_evening_star

| Field | Value |
| ------- | ------- |
| **Source** | Steve Nison — 3-candle reversal. Middle "star" gaps away from first candle; third reverses back into first candle's body. |
| **Definition** | Morning Star: large bearish → small-body star → large bullish closing above midpoint of first candle. Evening Star: symmetric. |
| **Implementation** | `detect_morning_evening_star` (line ~2147). `star_body <= star_body_max × star_range` (30%). Morning: `a_c < a_o` AND star qualifies AND `b_c > b_o` AND `b_c > midpoint(a_o, a_c)`. **No gap check** (crypto 24/7, no true gaps). SL = `entry × (1 ± sl_pct)`. |
| **Entry** | Third (confirming) candle close |
| **SL** | Percentage |
| **TP** | Pre-computed |
| **Confidence** | 3★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | **Gap condition missing** — canonical Morning/Evening Star requires the star to gap away from the first candle. Without this, the pattern is far more common and less selective. No minimum size requirement for first or third candle. |
| **Verdict** | NEEDS_REVIEW |

---

### 20. fib_golden_zone

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — fib 50%–61.8% is the "golden zone" for entries after a BOS-confirmed swing. |
| **Definition** | After a confirmed BOS, price retraces into the 50%–61.8% Fibonacci zone of the BOS swing leg. Enter in BOS direction. TP = 1.618 extension. |
| **Implementation** | `detect_fib_golden_zone` (line ~2467). `_find_bos_swing` helper identifies dominant swing H/L + BOS confirmation. Signal fires on each candle where close falls within `[fib_0.618, fib_0.5]` (long) or symmetric (short). SL = `swing_low` / `swing_high`. TP = 1.618 extension. |
| **Entry** | Candle close in golden zone |
| **SL** | Structural: swing_low (long), swing_high (short) |
| **TP** | 1.618 Fibonacci extension |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | **Multiple signals per BOS leg** — consecutive candles closing in the zone each generate a signal. Dedup only removes exact `open_time` duplicates. In backtest this creates overlapping trades on the same setup, inflating signal count and distorting win-rate stats. `fib_golden_zone` (50%–61.8%) overlaps at boundary with `ote_entry` (61.8%–78.6%) — a candle at exactly 61.8% triggers both strategies. |
| **Verdict** | NEEDS_REVIEW |

---

### 21. ote_entry

| Field | Value |
| ------- | ------- |
| **Source** | ICT (Michael Huddleston) — "Optimal Trade Entry" at 61.8%–78.6% retracement after BOS. More selective than golden zone. |
| **Definition** | Same as `fib_golden_zone` but targets the deeper 61.8%–78.6% retracement zone after confirmed BOS. |
| **Implementation** | `detect_ote_entry` (line ~2571). Identical to `fib_golden_zone` but uses `[fib_0.786, fib_0.618]` entry band. Same `_find_bos_swing` helper. SL = swing extreme. TP = 1.618 extension. |
| **Entry** | Candle close in OTE zone |
| **SL** | Structural: swing_low (long), swing_high (short) |
| **TP** | 1.618 extension |
| **Confidence** | 4★ |
| **In SIGNAL_REGISTRY** | Yes |
| **Known Issues** | Same multi-signal-per-BOS-leg issue as `fib_golden_zone`. Boundary overlap at 61.8% triggers both strategies simultaneously. |
| **Verdict** | NEEDS_REVIEW |

---

## Backtest Engine Notes (`analytics/backtest_lib.py`)

- **Entry:** Always `entry_idx = sig_idx + 1` (next candle open). Candlestick strategies embed entry in the signal; `run_backtest` still uses next candle open correctly.
- **SL source:** Prefers per-signal `sl_price` when present. Falls back to `sl_pct`. Structural strategies (fvg, order_block, liquidity_sweep, eqh_eql, fib_golden_zone, ote_entry, bos) provide `sl_price`.
- **TP:** Always `tp_r × risk_distance` from entry. Default `tp_r = 2.0`.
- **Fee:** `fee_drag_r = 2 × fee_pct / sl_pct` (uses global `sl_pct` for consistency).
- **Win/Loss:** SL checked before TP on each candle. If both touch same candle, SL wins.
- **`funding_reversion` `sl_price = 0.0` bug:** `run_backtest` uses `sl_price = 0.0` as structural SL → infinite SL distance for longs → backtest results meaningless even if funding data were available.

---

## CLAUDE.md Corrections

1. **`best_tfs` field:** `StrategySpec` dataclass has no `best_tfs` field. Not present in codebase.
2. **SIGNAL_REGISTRY count:** 20 entries confirmed correct (seasonality + fibonacci_retracement excluded).
3. **`funding_reversion` wiring gap:** Already noted in MEMORY.md Deferred Issues. Confirmed broken.
4. **ORB:** Confirmed BROKEN (fixed in A10 worktree).
