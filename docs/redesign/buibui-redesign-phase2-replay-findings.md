# v2 Phase 2 — Regime gate backtest replay findings

**Run date:** 2026-05-10
**Tool:** `tools/regime_gate_replay.py`
**Output:** `/tmp/regime_gate_replay.csv`
**DB:** `analytics.db` (~708K backtest_trades total)

## TL;DR

**DO NOT FLIP. The gate is dropping winners.**

Replaying the v2 Phase 2 regime gate against historical `backtest_trades`
shows the **suppressed subset has positive avg_r and the kept subset has
negative avg_r** — the opposite of what the §6 hypothesis predicted.

```text
SUPPRESSED aggregate: n=40,606  avg_r=+0.0285   (range/high_vol cells)
KEPT aggregate:       n=43,416  avg_r=-0.1293   (trend + unknown cells)
```

Hard mode would *destroy* edge. The gate's mapping is **inverted** relative
to what these three strategies (`bos`, `ema`, `fib_golden_zone`) actually do.

## Per-cell breakdown

| Strategy | Regime | Suppressed | n | avg_r | Win% |
| -------- | ------ | ---------- | -----: | -------: | ----: |
| bos | high_vol | **YES** | 17,691 | −0.0277 | 30.9% |
| bos | range | **YES** | 17,707 | **+0.0774** | 33.7% |
| ema | high_vol | **YES** | 385 | −0.1426 | 23.6% |
| ema | range | **YES** | 385 | **+0.2257** | 33.8% |
| fib_golden_zone | high_vol | **YES** | 1,971 | **+0.1001** | 37.0% |
| fib_golden_zone | range | **YES** | 2,467 | +0.0201 | 33.6% |
| bos | trend | no | 35,123 | **−0.1272** | 27.8% |
| bos | unknown | no | 2,047 | −0.2066 | 25.1% |
| ema | trend | no | 1,086 | **−0.2088** | 22.5% |
| ema | unknown | no | 41 | −0.4497 | 17.1% |
| fib_golden_zone | trend | no | 4,834 | **−0.1135** | 30.6% |
| fib_golden_zone | unknown | no | 285 | +0.2385 | 46.0% |

**Pattern:** for every one of the three "continuation" strategies, the
**range** regime is more profitable than the **trend** regime. The §6
mapping (continuation only in trend) suppresses the better cells and
keeps the worse cells.

## Why is this counter-intuitive

The §6 design rests on the intuition: *"continuation strategies need a
trend; suppress them in range/chop."* The data says no. Three plausible
explanations:

1. **Trend regime captures exhaustion, not opportunity.** A 4h EMA-50
   slope ≥ 0.5% over 10 bars ≈ ~12 bars of strong directional move. By
   the time the regime classifier flags `trend`, the move is already
   extended. Continuation entries chase the tail and mean-revert.
2. **"Continuation" mislabels these detectors.** `bos` (break of
   structure) is a *breakout* signal — most profitable when breaking out
   *of a range*, not extending an existing trend. Same for `fib_golden_zone`
   (BOS-anchored OTE entry on a pullback within a range, not a runaway
   trend).
3. **`ema` is broadly broken.** The earlier WFO sweep already showed only
   ETH/1h/tue_thu is profitable. The replay just confirms: even after
   regime-slicing, ema's positive-cell volume is tiny (n=385) and its
   negative-cell volume dominates (n=1,086 in trend alone).

The gate didn't fail because of a wiring bug — the unit + integration
tests pass and the cycle ran cleanly. It failed because the **§6
hypothesis is empirically wrong for the live strategy mix**.

## Decision

1. **Do not flip soft → hard.** Leave `mode = "soft"` indefinitely.
2. **Do not delete the gate.** It's a useful instrument; the *mapping*
   needs revisiting, not the framework.
3. **Open follow-up: regime_mapping_v2.** Three options worth considering,
   in order of effort:
   - **Invert** the current map: `bos`/`ema`/`fib_golden_zone` allowed in
     `range`/`high_vol`, dropped in `trend`. Predicts +0.16R/trade lift on
     ~84K replayed trades. **Tempting, but extrapolating "I think the
     gate should run backwards" from one replay is exactly the kind of
     after-the-fact rationalisation the §6 design avoided.** Needs WFO
     IS/OOS validation before shipping.
   - **Refine the classifier**, not the mapping. Maybe the 4h EMA slope
     threshold (0.5%) is too lax and labels exhaustion as trend.
     Sensitivity sweep on `_SLOPE_TREND_THRESHOLD` against the same
     trade set.
   - **Drop the v1 mapping and skip to Phase 2 step 2.** The unified
     `SignalCandidate` model collapses these into 4 setup_types, at
     which point the §6 matrix can be re-derived from per-setup_type
     edge data instead of inheriting from the current type taxonomy.
4. **Re-run this replay** after any detector or regime-classifier change
   to verify the mapping decision against fresh data.

## What this validates

- The Phase 0 audit was right to verdict 0 KILL / 0 DEMOTE / 19 KEEP —
  every "continuation" strategy has its profitable cells; categorical
  cuts would have killed those.
- The "soft mode for 2 weeks" plan would have **avoided shipping a bad
  flip**, but at the cost of 2 weeks of opportunity-cost waiting. The
  replay achieves the same outcome in minutes.
- The replay tool generalises: any future regime-mapping change can be
  validated the same way before merge, no live observation required.

## What this does NOT cover

- The replay only sees the 3 strategies the gate currently suppresses
  (`bos`, `ema`, `fib_golden_zone`). It says nothing about whether the
  gate could be *expanded* to suppress cells in the other 16 strategies
  it currently lets through everywhere (`flow`/`structural`/
  `price_action`/`candlestick`/`session`).
- The replay uses the same 4h regime classifier that the live gate uses.
  If the classifier itself is wrong (option 3.b above), the replay
  inherits that flaw.
- `ote_entry` has no rows in `backtest_trades` — its suppressed cells
  could not be evaluated.
