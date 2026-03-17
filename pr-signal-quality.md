# PR: Signal Quality — Structural SL/TP, Candle Context, Conflict Suppression, Confluence Stacking

**Branch:** `feat/signal-quality` → `main`
**Commit:** `b173c13`
**Tests:** 319 passed (17 new tests added)

---

## Summary

Four interconnected improvements that make alerts actionable and noise-resistant:

1. **Structural SL/TP** — each strategy now computes its own invalidation level instead of using a hardcoded 2% from price
2. **Candle context** — alerts show which candles formed the pattern so you can find them on the chart
3. **Conflict suppression** — when the same symbol/timeframe produces opposing signals simultaneously, both are silently dropped
4. **Confluence stacking** — when multiple strategies agree on direction for the same symbol/timeframe in the same scan cycle, they are merged into a single alert

---

## 1. Structural SL/TP

### Before

SL was computed in `alert_formatter.py` as a flat percentage from entry price, regardless of strategy:

```python
sl_price = event.price * (1 - sl_pct)   # always 2% below for longs
tp_price = event.price * (1 + sl_pct * tp_r)
```

This meant:
- A FVG signal with a gap 0.05% below entry showed `SL: 2.0%` — making the R:R display meaningless
- A liquidity sweep with its wick low 0.3% below showed `SL: 2.0%` — the stop was 6× wider than necessary
- The "2.0x R" label was technically correct but derived from an arbitrary risk, not the actual trade setup

### After

Each detector emits `sl_price` — the structural invalidation level specific to that pattern. The formatter uses it when valid and falls back to `sl_pct` only when `sl_price == 0` (currently only `funding_reversion`, which has no structural candle level).

| Strategy | SL logic | Rationale |
|---|---|---|
| `fvg` long | `gap_bot` | Price closing below the gap bottom means the imbalance is rejected |
| `fvg` short | `gap_top` | |
| `wick_fill` long | `zone_bot` (wick low) | Below the wick low = the wick is not holding as support |
| `wick_fill` short | `zone_top` (wick high) | |
| `marubozu` long | `row_low` of marubozu candle | Below the marubozu candle = order block invalidated |
| `marubozu` short | `row_high` of marubozu candle | |
| `orb` long | `range_low` | Below the opening range = breakout failed |
| `orb` short | `range_high` | |
| `liquidity_sweep` long | `candle_low` (wick of sweep candle) | Re-sweeping the low = fake reversal |
| `liquidity_sweep` short | `candle_high` | |
| `bos` / `choch` long | `last_sl` (prior swing low) | Losing the last swing low breaks the structure the other way |
| `bos` / `choch` short | `last_sh` (prior swing high) | |
| `smt_divergence` long | `curr_p_low` (the diverging low) | Below the stop-hunt low = it wasn't a fake-out |
| `smt_divergence` short | `curr_p_high` | |
| `funding_reversion` | falls back to `sl_pct` | No candle-level invalidation point; rate-based signal |

**Fallback safety check:** `_resolve_sl()` validates that the structural `sl_price` is actually below entry for longs (above for shorts). If not, it falls back to pct — this guards against edge cases where the detector emits an SL that crosses price.

**R:R is now honest.** TP is computed as `entry ± (entry - structural_SL) × tp_r`, so a `2.0x R` label means exactly 2× the actual risk to the structural stop.

### Example

```
# Before
SIGNAL — SOLUSDT 5m
Direction: LONG 🟢  Strategy: fvg
Reason: fvg_long@94.59-94.73
Price: 94.67
SL: 92.78 (2.0%)  TP: 98.46 (4.0% | 2.0x R)   ← SL 2% away, ignores the gap

# After
SIGNAL — SOLUSDT 5m
Direction: LONG 🟢  Strategy: `fvg`
Reason: `fvg_long@94.59-94.73`
Gap: 17-Mar 10:00 · 17-Mar 10:05 · 17-Mar 10:10
Price: 94.67  |  14:25 UTC
SL: 94.59 (0.1%)  TP: 94.75 (0.2% | 2.0x R)   ← SL at gap_bot, tight and structural
```

---

## 2. Candle Context

### Before

The `reason` string contained pattern parameters (e.g. `fvg_long@94.59-94.73`) but no information about *when* the pattern was formed. To find the relevant candles on a chart you had to scroll back through the timeframe and guess.

`SignalEvent` had no field for extra pattern metadata. `SIGNAL_COLUMNS` in `indicators_lib.py` was `["open_time", "direction", "reason"]`.

### After

`SIGNAL_COLUMNS` is now `["open_time", "direction", "reason", "sl_price", "context"]`.

Each detector populates `context` with a human-readable string pointing to the formation candles:

| Strategy | Context string | Example |
|---|---|---|
| `fvg` | Timestamps of all three gap-forming candles | `Gap: 17-Mar 10:00 · 17-Mar 10:05 · 17-Mar 10:10` |
| `wick_fill` | Timestamp of the candle with the significant wick | `Wick: 17-Mar 09:45` |
| `marubozu` | Timestamp of the marubozu candle | `Marubozu: 17-Mar 09:30` |
| `orb` | Timestamp of the opening range candle | `Range: 17-Mar 13:00` |
| `bos` / `choch` / `liquidity_sweep` / `funding_reversion` / `smt_divergence` | `""` — the signal candle IS the pattern candle, so no extra context needed |

The alert always shows the signal candle time (`open_time`) as `|  17-Mar 14:25 UTC`. Context is shown as an additional line when non-empty.

`_fmt_time(ts_ms)` is a small private helper in both `indicators_lib.py` and `alert_formatter.py` that formats Unix ms → `"17-Mar 14:25"`.

---

## 3. Conflict Suppression

### Before

All strategies ran independently. In the same scan cycle, for the same symbol and timeframe, the system could (and did) fire both a LONG and a SHORT alert — each sent to Telegram separately. The user received contradictory instructions with no indication of the conflict.

Real example that prompted this change:
```
SIGNAL — BTCUSDT 5m  Direction: LONG 🟢   Strategy: liquidity_sweep
SIGNAL — BTCUSDT 5m  Direction: SHORT 🔴  Strategy: bos
```
Both sent within seconds of each other.

### After

In `run_scan_cycle`, after `scan_symbol` returns events for a `(symbol, tf)` pair, the events are split by direction:

```python
long_events = [e for e in events if e.direction == "long"]
short_events = [e for e in events if e.direction == "short"]
if long_events and short_events:
    logger.info("Conflict suppressed: %s %s ...", symbol, tf, ...)
    continue
```

If both directions are present, **neither** is sent. A `INFO` log records which strategies conflicted. Cooldowns are NOT consumed — the conflict is discarded cleanly.

**Why not send either?** A conflicted market has no clear directional edge. Sending one arbitrarily is worse than sending nothing, because it creates false confidence.

---

## 4. Confluence Stacking

### Before

Each strategy that passed the cooldown check fired as a separate Telegram message. Two agreeing strategies on the same symbol/tf sent two messages.

### After

After conflict suppression, all remaining events share one direction. Each strategy's cooldown is still checked independently. All strategies that *pass* cooldown are collected into `passing_events` and formatted as **one message** via `format_confluence_alert()`.

```python
passing_events = [
    e for e in direction_events
    if store.is_new_candle(symbol, tf, e.strategy, e.open_time)
    and store.is_off_cooldown(symbol, e.strategy, e.direction)
]
# ... record all, then:
msg = format_confluence_alert(passing_events, sl_pct=sl_pct, tp_r=tp_r)
```

The alert format adapts based on count:

**Single strategy** — same layout as before (no "Confluence" label, no bullet list):
```
*SIGNAL — SOLUSDT 5m*
Direction: LONG 🟢  Strategy: `fvg`
Reason: `fvg_long@94.59-94.73`
Gap: 17-Mar 10:00 · 17-Mar 10:05 · 17-Mar 10:10
Price: 94.67  |  14:25 UTC
SL: 94.59 (0.1%)  TP: 94.75 (0.2% | 2.0x R)
```

**Multiple strategies** — confluence header with bullet list:
```
*SIGNAL — BTCUSDT 5m*
Direction: LONG 🟢  Confluence: 2 strategies
• `fvg` — `fvg_long@94100.00-94250.00`  (Gap: 17-Mar 10:00 · 10:05 · 10:10)
• `liquidity_sweep` — `sweep_low@74057.50`
Price: 74,067.80  |  14:25 UTC
SL: 94,100.00 (0.1%)  TP: 94,334.00 (0.2% | 2.0x R)
```

**SL in confluence mode** uses `_tightest_sl()` — the structural SL closest to entry across all stacked strategies:
- Long: `max(valid_sl_prices below price)` — highest floor, smallest risk
- Short: `min(valid_sl_prices above price)` — lowest ceiling, smallest risk

This means the confluence alert has the tightest (most conservative) stop of all the participating strategies.

---

## Files Changed

### `analytics/indicators_lib.py`
- Added `from datetime import UTC, datetime`
- `SIGNAL_COLUMNS`: `["open_time", "direction", "reason"]` → `["open_time", "direction", "reason", "sl_price", "context"]`
- Added `_fmt_time(ts_ms: int) -> str` helper
- Updated all 8 active detectors (`detect_wick_fills`, `detect_marubozu_retest`, `detect_orb_breakout`, `detect_liquidity_sweep`, `detect_fvg`, `detect_market_structure`, `detect_funding_extreme`, `detect_smt_divergence`) to include `sl_price` and `context` in every signal dict

### `signals/alert_formatter.py`
- `SignalEvent`: added `sl_price: float = 0.0` and `context: str = ""` (both default to backward-compatible values)
- Added `_fmt_time()` helper (same as in `indicators_lib.py`)
- Added `_resolve_sl()` — validates structural SL before using it, falls back to pct
- Added `_tightest_sl()` — computes tightest valid structural SL across a list of events
- `format_signal_alert()` — now delegates to `format_confluence_alert([event])`, backward compatible
- Added `format_confluence_alert(events, sl_pct, tp_r)` — handles single and multi-event alerts; always shows signal time; shows context when non-empty; shows confluence bullet list when `len(events) > 1`

### `analytics/signal_lib.py`
- `scan_symbol`: passes `sl_price` and `context` from signals DataFrame row into `SignalEvent`
- `run_scan_cycle`: replaced flat per-event loop with:
  1. Split events by direction
  2. Conflict detection → `continue` if both directions present
  3. Per-strategy cooldown filtering → `passing_events`
  4. Record alerts for all passing strategies
  5. Single call to `format_confluence_alert(passing_events)`
- Import changed from `format_signal_alert` to `format_confluence_alert`

### `tests/test_signal_lib.py`
- Added 7 tests to `TestFormatSignalAlert`: structural SL for long/short, pct fallback, context rendering, signal time in alert
- Added new `TestFormatConfluenceAlert` class (5 tests): single vs multi layout, tightest SL for long/short, context in bullet lines
