# Phase 0a — Strategy Triage Findings

**Date:** 2026-04-25
**Scope:** Read-only audit of all 21 strategies in `analytics/indicators_lib.py` (3,152 LOC) and their test coverage in `tests/test_indicators_lib.py` + `tests/test_candle_patterns.py`. No code changes.
**Severity tags:** `[critical]` bleeding now · `[high]` logic bug not bleeding · `[medium]` source drift / suboptimal · `[low]` cosmetic.

> **Update 2026-04-27:** the `[high]` test-coverage gap on `fib_golden_zone` / `ote_entry` (cross-cutting observation 1, top-N finding 3) was a false alarm. The audit only inspected `tests/test_candle_patterns.py`; both detectors are in fact covered by `tests/test_fib_strategies.py` (`TestDetectFibGoldenZone` and `TestDetectOteEntry`). The corresponding lines below are struck through. No other findings change.

## Registry inventory

- `STRATEGY_REGISTRY`: 21 entries (canonical list).
- `DETECTOR_REGISTRY`: 18 OHLCV-only detectors. Excludes `seasonality` (returns stats), `funding_reversion` (needs funding feed), `smt_divergence` (needs secondary symbol). `fibonacci_retracement` legacy detector is commented out and superseded by `fib_golden_zone`.
- `INCOMPATIBLE_PAIRS`: only 2 — `{fib_golden_zone, bos}` and `{ote_entry, bos}` (both share `_find_bos_swing()`).

## Per-strategy findings

### 1. seasonality (`strategy_type=session`)

- Not actionable — returns DOW/HOD/WOM stats only. Excluded from `DETECTOR_REGISTRY` and signals registry. **Status: keep as-is.**

### 2. wick_fill (`price_action`) — `detect_wick_fills`

- Source: ICT-adjacent (wick zone retest, no canonical reference).
- **[low]** No canonical written source — internal heuristic. Document as "internal" in Phase 3 to set source-fidelity expectation correctly.
- Test class `TestDetectWickFills` present. Confirm ≥3 cases incl. negative.

### 3. marubozu (`price_action`) — `detect_marubozu_retest` (Nison)

- `max_wick_ratio=0.1` (wick ≤10% of body) is reasonable Nison drift.
- Tests cover bullish + bearish + wick-too-large + retest-window. Looks adequate.
- **[low]** D17 already shipped a wick-ratio warning at alert layer; ensure consistency between detector threshold (0.1) and alert warning threshold.

### 4. orb (`session`) — `detect_orb_breakout` (Crabel)

- **[medium]** Source drift from Crabel: original ORB anchors on equity-session open (e.g., 09:30 ET) with 5/15/30/60-min range bars. This implementation anchors on **00:00 UTC** with `range_candles=2` first candles of the day. Defensible adaptation for 24/7 crypto, but **document the deviation explicitly** in the docstring and Phase 3 audit doc.
- **[low]** Legacy params `session_hour_utc` and `timeframe_minutes` are silently ignored (kept for backwards compat). Mark as deprecated in Phase 1 cleanup; remove after one release.
- Tests `TestDetectOrbBreakout` cover too-few-candles, range build, breakout direction, dedup. Adequate.

### 5. liquidity_sweep (`structural`) — `detect_liquidity_sweep` (ICT)

- Two modes (`use_fib_extension=True` default vs pivot-only). Fib levels 1.13/1.27 default. Reasonable.
- `require_close_rejection=True` enforces close-back-inside — matches ICT raid-and-reject logic.
- **[low]** Many params (`fib_require_range_close`, `use_fib_extension`, `require_close_rejection`, `swing_n`, `lookback`) — surface area large, hard to sweep all combos. Phase 3 audit should pick a canonical default set and drop one or two of these.
- Test class present.

### 6. fvg (`structural`) — `detect_fvg`

- Standard 3-bar gap (ICT FVG). Tests cover bullish/bearish fill, returns-empty-on-<3 candles. Looks correct.
- **[low]** Verify CE (consequent encroachment, midpoint) vs full-fill behaviour — current short test uses `high ≥ CE` for entry. Document choice.

### 7. bos (`structural`) — `detect_market_structure` (ICT BOS + CHoCH)

- **[medium]** Docstring says "min_swing_pct: ... Default 0.0 keeps the original behaviour" but signature is `min_swing_pct: float = 0.005`. Either docstring or default is stale. Fix in Phase 3 (probably the docstring — the 0.005 default was likely a deliberate filter add).
- Centred rolling window for swings (size `2*swing_lookback+1`) is lookahead-safe only because the iteration uses fully-formed pivots — confirm in tests. **[high]** Worth adding a "no future leakage" regression test if not already present.
- Test class `TestDetectMarketStructure` has zigzag-up / zigzag-down constructions; coverage looks decent but verify edge cases (single swing, equal highs).

### 8. funding_reversion (`flow`) — `detect_funding_extreme`

- **[critical]** Strategy is silently skipped at runtime — `fetch_funding_rates()` exists in `data_fetcher.py` but is never called from `data_sync.py`. No funding rows in DB → detector returns empty every cycle. Already known (MEMORY: P3 deferred). Either wire it (small lift), or remove from `STRATEGY_REGISTRY` and CLI in Phase 1 to stop pretending it's available. Recommend **remove** unless we're committing to wire G2.
- Detector itself is well-tested in isolation (`TestDetectFundingExtreme`).

### 9. smt_divergence (`flow`) — `detect_smt_divergence` (ICT)

- **[high]** MEMORY notes: "mostly negative; only ETH/1h shows edge." This is a backtest finding, not a logic bug, but consider gating SMT to ETH/1h in Phase 3 or marking as off-by-default.
- Centred swing window matches `eqh_eql`; confirmation requires `swing_n` right-candles. Lookahead-safe.
- Trend filter via EMA(50) on primary — reasonable.
- Tests present.

### 10. order_block (`structural`) — `detect_order_block` (ICT)

- Standard ICT OB: last opposite candle before displacement; retest enters body zone.
- `displacement_pct=0.003` (0.3%) is a reasonable default but tunable. Phase 3: WFO-sweep on this.
- Tests cover both directions + signal-columns + too-few-candles. Adequate.

### 11. eqh_eql (`structural`) — `detect_eqh_eql` (ICT)

- `tolerance_pct=0.003` for "equal" highs/lows is reasonable.
- Centred window pivots; numpy `searchsorted` for performance — recently optimised.
- Tests cover the basic raid-and-reject. **[low]** Verify negative tests (wick beyond but close also beyond → no raid) exist.

### 12. cvd_divergence (`flow`) — `detect_cvd_divergence`

- Uses `taker_buy_volume` from Binance OHLCV — proxy for CVD, not real CVD (tick data).
- **[medium]** Document explicitly: this is *taker-buy-volume divergence*, not true CVD. MEMORY/D10 notes G2c (CoinGlass) is the path to real CVD. Rename or annotate to avoid implying it's tick-CVD-grade.
- Tests guard against all-NaN, empty input, etc. Adequate.

### 13. trend_day (`structural`) — `detect_trend_day`

- Body-pct ≥ 0.65 + small wicks → high-conviction directional close.
- **[low]** Source: Crabel-adjacent (NR4/Trend Day concept) — document inspiration. No bug.
- Tests cover bullish/bearish/doji-rejection. Adequate.

### 14. engulfing (`candlestick`, Nison) — `detect_engulfing`

- Standard 2-candle engulfing: prev opposite color, current body fully engulfs prev body.
- Tests confirm both directions + columns.
- **[low]** Volume confirmation not used here (other detectors use `volume_confirm()`); consider for Phase 3 sweep.

### 15. pin_bar (`candlestick`, Nison) — `detect_pin_bar`

- `wick_ratio=2.0` is a standard 2:1 pin bar; some Nison/Maitland variants require 3:1. Reasonable default.
- Tests: bullish, bearish, big-body rejection, SL placement. Adequate.

### 16. inside_bar (`candlestick`) — `detect_inside_bar`

- **[high]** Body-only containment (uses `max(open,close)` / `min(open,close)`) — *not* high/low containment. This is a deliberate choice (avoids wick noise), but **deviates from the standard inside-bar definition** which uses high ≤ prev high AND low ≥ prev low. Either rename to "inside-body bar" or switch to high/low containment. Could materially change signal count.
- Source drift from canonical Nison/price-action lit. Phase 3: A/B backtest body vs high-low containment.

### 17. hammer_hanging_man (`candlestick`, Nison) — `detect_hammer_hanging_man`

- Same shape as bullish pin bar + context (downtrend → hammer; uptrend → hanging man) via `close[i] vs close[i - context_lookback]`.
- **[low]** `context_lookback=10` for trend determination is arbitrary. Phase 3: sweep against EMA-slope or the F8 HTF gate.
- Body=0 short-circuit avoids div-by-zero. Good.

### 18. doji (`candlestick`, Nison) — `detect_doji`

- **[medium]** `body_threshold=0.1` (body ≤ 10% of range) is generous; canonical Nison ≤ 5%. Phase 3: backtest 0.05 vs 0.1.
- Requires next-candle confirmation (`confirm_body_pct=0.6`) — sound.
- Tests cover bull/bear confirm + weak-confirm rejection. Adequate.

### 19. morning_evening_star (`candlestick`, Nison) — `detect_morning_evening_star`

- 3-candle pattern; star body ≤ `star_body_max=0.3` of A's body; B closes past midpoint of A.
- Tests cover both. Adequate.
- **[low]** `star_body_max=0.3` is loose — Nison says 1:3 ratio, so ~0.33 is in range, fine.

### 20. fib_golden_zone (`fib`) — `detect_fib_golden_zone` (ICT)

- 0.5–0.618 retracement of last swing, SL at 0.786, TP at swing extreme.
- Already in `INCOMPATIBLE_PAIRS` with `bos` (shared swing detection logic).
- **[medium]** Per `project_f10_fib_golden_zone`, MEMORY suggests testing 1.382 as TP1 — Phase 3 backlog item, not a bug.
- Tests in `test_fib_strategies.py::TestDetectFibGoldenZone` (4 cases incl. negative). *Original audit said `test_candle_patterns.py` — incorrect.*

### 21. ote_entry (`fib`) — `detect_ote_entry` (ICT OTE)

- 0.618–0.786 OTE retracement after confirmed BOS; TP at 1.618 extension.
- Stricter than fib_golden_zone (deeper zone). Logic mirrors it.
- **[low]** Same swing-detection sharing as fib_golden_zone (already in INCOMPATIBLE_PAIRS with bos).
- Tests in `test_fib_strategies.py::TestDetectOteEntry` (covers shallow-retrace, OTE hit, registry/signal wiring).

## Cross-cutting observations

- ~~**No detector unit-tests directory `tests/test_indicators_lib.py` for `fib_golden_zone` and `ote_entry`?** They live in `test_candle_patterns.py` (per imports there: `detect_doji, detect_engulfing, detect_fibonacci_retracement, detect_hammer_hanging_man, detect_inside_bar, detect_morning_evening_star, detect_pin_bar`). `fib_golden_zone` and `ote_entry` are not in that import list — verify they have test coverage somewhere; if not, that's a gap. **[high]**~~ **Resolved 2026-04-27 (false alarm):** both detectors are covered by `tests/test_fib_strategies.py` (`TestDetectFibGoldenZone`, `TestDetectOteEntry`). The audit only inspected `test_candle_patterns.py`. No gap.
- **`INCOMPATIBLE_PAIRS` is undersized.** Only 2 pairs. Likely candidates to add (Phase 3 — confirm via co-fire correlation): `{fib_golden_zone, ote_entry}` (overlap ≥61.8% retracement zones), `{eqh_eql, liquidity_sweep}` (both fade swing-extreme raids).
- **No "no future leakage" property test** that runs every detector against a randomised partial-history slice and asserts the same signals fire as on the full history. Add in Phase 3 — would have caught any centred-window misuse before live.
- **HTF-first ordering bug already fixed (PR #303)** — confluence relies on Phase 3 processing 1d→4h→1h→15m. Worth a regression test pinning ordering.

## Top-N critical/high findings (sorted)

1. **[critical] funding_reversion is dead code** — wired into registry but no funding data ever reaches the detector. Decide: wire G2 or remove from registry. (Strategy 8.)
2. **[high] inside_bar uses body-only containment** — deviates from canonical inside-bar definition. Backtest impact unknown. Either rename or restore high/low containment. (Strategy 16.)
3. ~~**[high] Possible test-coverage gap on fib_golden_zone and ote_entry** — not in `test_candle_patterns.py` import list; verify in Phase 3 kickoff before audit. (Strategies 20, 21.)~~ **False alarm (2026-04-27):** both detectors are covered by `tests/test_fib_strategies.py::TestDetectFibGoldenZone` / `TestDetectOteEntry`.
4. **[high] No "no-future-leakage" property test for centred-window detectors** (BOS, eqh_eql, smt_divergence) — single broken slice could ship undetected.
5. **[high] smt_divergence mostly negative in backtest** (MEMORY, P3) — gate to ETH/1h or default off until D10 confluence layer earns its keep.
6. **[medium] BOS docstring vs default mismatch** — `min_swing_pct` documented as 0.0 but default is 0.005.
7. **[medium] cvd_divergence is taker-buy proxy, not real CVD** — rename or annotate.
8. **[medium] doji body threshold 0.1 vs canonical 0.05** — sweep in Phase 3.
9. **[medium] orb session anchor 00:00 UTC vs Crabel session-open** — document deviation.

Phase 3 (full strategy audit) absorbs all `[medium]` and `[low]` findings; `[critical]` and `[high]` items are candidates for hot-fix during Phase 1 if they prove easy.
