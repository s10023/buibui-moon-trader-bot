# Prune-to-positive-core — review draft + the combo-rescue question (2026-06-04)

**Status: Group A APPLIED 2026-06-04 (pin_bar 1h + trend_day 4h-short; `make db-update`
done, regression 3/3 green). Groups B/C still pending.** Concrete per-cell prune proposal
for the live `signal_watch*.toml` configs, plus a direct answer to the "bleeders strike
gold in combo, so don't remove them" objection.

**Applied:** `pin_bar` 1h removed on signal_watch + weekdays (already absent on weekend);
`trend_day` 4h-short cut on signal_watch + all (mon_fri left flat). Group B = direction
overlay (step 2). Group C whole-strategy retirements = gated on the fixed-partner OOS
combo test.

## The combo-rescue objection — and why the current system already answers it

The objection: a strategy can be negative standalone yet net-positive when it co-fires with
a partner, so cutting it destroys combo edge.

Three facts from the code + the live ledger settle this **for the system as built**:

1. **Live combos are annotations, not triggers.** `scan_symbol` decides the alert on the
   standalone path (`scanner.py:731-734` hard-mode gate → `:1080-1090` alert appended). The
   co-fire lookup runs *after* (`:1027-1077`) and only attaches a `ConfluenceData`
   blockquote. It **never lowers the bar or un-suppresses** a signal.
2. **A bleeder must pass its own standalone gate to combo at all.** Signals are persisted
   *post-gate* (`scanner.py:910-914` writes the already-filtered `passing_events`), and a
   pruned cell is `continue`-skipped *before detection* (`:173-183`, `:222-229`). The combo
   lookup reads the signals table — so a cell that fails standalone is absent from the DB
   and cannot be a partner for anyone.
3. **The live −0.12R ledger was produced WITH combo-tagging live in production.** Every
   bleeder alert in `signal_alert_outcomes` already had the co-fire blockquote available.
   The bleeders bled anyway. So "combos rescue them" is not a hypothetical we'd be
   foreclosing — it is a hypothesis that **already ran in production and failed.**

Conclusion: removing a bleeder destroys **no combo edge that is currently being captured.**
Its combo'd alerts were standalone bets we already counted in the −0.12R.

### The version of your idea that is NOT yet falsified

A *different architecture* — **confluence-as-trigger**: fire the bleeder *only* when it
co-fires with a known-good partner, even if it would fail standalone. That is worth
considering. But the only evidence for it today is `combo_max_avg_r` from the edge audit,
and that is the **single most overfit statistic in the dataset**: it is the **MAX over ~19
candidate partners × multiple TFs** of an *in-sample* avg_r. Max-of-many is a biased-high
estimator — it looks spectacular in-sample and will decay OOS exactly as the standalone
candlestick edge did (audit in-sample doji +0.157 → live −0.089). And it **cannot be
OOS-confirmed** because combos aren't a live action, so there is no realized combo outcome
to check against.

**My recommendation:** do not bet config changes on `combo_max_avg_r`. Before *permanently
retiring* any strategy, run the proper test — a **fixed (pre-specified) partner, n≥30,
IS/OOS split** combo backtest via `/confluence-backtest` + `tools/combo_health.py`. If a
specific pair shows robust OOS edge, build confluence-as-trigger for *that pair* and keep
the strategy. If not (expected), retire it. The prune below is structured to respect this.

## How the prune overlaps the direction overlay (Fork B step 2)

The bulk of the bleeding is the **long side**, which the direction overlay
(`suppress_long` / F8) already targets. Once step 2 does the long-side cutting, the
*residual* standalone-prune work is small — only **both-direction-negative** or
**short-side-negative** cells the overlay won't catch. So steps 1 and 2 are largely the
same lever; the genuinely-new step-1 cuts are few.

## Day-of-week-resolved evidence (2026-06-04)

Live `signal_alert_outcomes`, resolved (`outcome IS NOT NULL`), `avg_r = AVG(outcome_r)`,
bucketed by the **UTC weekday of `candle_ts_ms`** into the three config scopes
(tue_thu = `signal_watch.toml`, mon_fri = `signal_watch_weekdays.toml`, weekend =
`signal_watch_all.toml`). Resolved rows: tue_thu 1135 / mon_fri 698 / weekend 593; 0 rows
needed a `fired_at` fallback. Format `avg_r(n)`:

| strategy | tf | dir | tue_thu | mon_fri | weekend |
| --- | --- | --- | --- | --- | --- |
| pin_bar | 1h | long | −0.29 (23) | −0.08 (6) | −0.69 (8) |
| pin_bar | 1h | short | −0.63 (16) | −0.30 (26) | −0.45 (5) |
| trend_day | 4h | short | −0.25 (12) | +0.06 (9) | −0.78 (11) |
| inside_bar | 15m | long | −0.53 (15) | −0.72 (15) | −0.80 (27) |
| inside_bar | 15m | short | −0.05 (116) | −0.02 (79) | **+0.31 (56)** |
| morning_evening_star | 15m | long | −0.33 (34) | −0.56 (32) | −0.66 (18) |
| morning_evening_star | 15m | short | +0.31 (121) | +0.26 (65) | +0.10 (56) |
| engulfing | 15m | short | −0.12 (76) | −0.25 (47) | **+0.25 (25)** |
| hammer_hanging_man | 15m | short | −0.30 (41) | −0.07 (22) | **+0.13 (24)** |
| pin_bar | 15m | short | +0.16 (95) | +0.07 (64) | −0.05 (43) |
| orb | 1h | long | +0.39 (11) | +0.28 (6) | −1.00 (3) |
| orb | 1h | short | +0.28 (15) | +0.01 (4) | — |
| bos | 15m | short | +0.38 (61) | +1.05 (27) | **−0.83 (21)** |
| bos | 1h | short | +2.36 (20) | +0.88 (15) | **−0.40 (5)** |
| bos | 15m | long | +0.12 (29) | −0.57 (30) | −0.80 (15) |

**The split flips the prune — cuts must be per-config, not uniform:**

- **Weekend is a different regime.** Several candlestick *shorts* that lose on weekdays go
  **positive on weekend** (inside_bar +0.31, engulfing +0.25, hammer +0.13). And the
  flagship positive-core cell — **bos shorts — inverts to negative on weekend** (15m −0.83
  n=21, 1h −0.40). A pooled-data prune would have wrongly cut the weekend candlestick
  shorts and wrongly trusted weekend bos.
- The **long side is negative in every config** (inside_bar/m_e_s/pin_bar/engulfing 15m
  long all < −0.3 across all three buckets) → confirms the direction overlay (step 2) is
  the right primary lever, applied globally.
- The positive core (liquidity_sweep, fib_golden_zone, eqh_eql, cvd) **drops below n=10 per
  config** when split — it is real but very low frequency.

### Group A — cut now (sign-consistent across the relevant configs)

| cut | configs | evidence |
| --- | --- | --- |
| `pin_bar` remove `1h` | **all 3** | both directions negative in every bucket |
| `trend_day` cut `4h short` | tue_thu + weekend | −0.25 (12) / −0.78 (11); mon_fri +0.06 → leave |

```toml
# signal_watch.toml, signal_watch_weekdays.toml, signal_watch_all.toml — [strategy_timeframes]
pin_bar = ["15m", "4h", "1d"]     # was [...,"1h",...] — 1h L/S negative every config

# signal_watch.toml + signal_watch_all.toml only — [strategy_timeframes_short]
trend_day = ["1d"]                # 4h short −0.25/−0.78; mon_fri leaves 4h (flat +0.06)
```

### Group A′ — weekend-specific flag (NEW, from the split)

`bos` short inverts negative on weekend (15m −0.83 n=21). bos is the positive core on
tue_thu/mon_fri, so do **not** touch it there — but on `signal_watch_all.toml` it is
**not** the reliable cell it appears in the pooled view. Watch it; candidate for a
weekend bos-short suppression once n grows. Conversely, **keep** candlestick shorts on
weekend (they are the weekend carry).

### Group B — long-side cuts (direction overlay, step 2 — do NOT duplicate here)

Long is negative in all three configs (inside_bar/m_e_s/pin_bar/engulfing 15m long, bos
1h long). Handle globally via `suppress_long` / F8, not per-tf `strategy_timeframes_long`.
Listed here only so they aren't double-counted against Group A.

### Group C — whole-strategy retirement candidates → GATE ON THE COMBO TEST FIRST

These are net-negative live across the board and are the ones your objection is really
about. **Do not delete from `strategies = [...]` yet.** Run the fixed-partner OOS combo
test; cut only those with no robust OOS combo edge:

`engulfing` (−0.127, n=250), `pin_bar` (−0.139, n=349), `inside_bar` (−0.151, n=441),
`hammer_hanging_man` (−0.298, n=144), `order_block` (−0.374, n=57), `trend_day` (−0.357,
n=56), `doji` (−0.089, n=63), `orb` (−0.041, n=66 — actually 1h is positive both ways,
keep 1h), `morning_evening_star` (−0.002, n=487 — near-flat, short side carries it).

## Levers NOT recommended right now

- **Raising global `min_avg_r`** (`strategy_params.toml:156`, currently `0.0`): it gates on
  *backtest* avg_r (in-sample) — the number we just proved decays OOS. A coarser, less
  trustworthy tool than the live-ledger-driven per-cell cuts above. Revisit only as a
  global backstop once a live-ledger-driven gate exists (the T2/T4 work).
- **Blunt removal from `strategies = [...]`**: forecloses the confluence-as-trigger option
  *and* wastes detection. Use per-(tf, direction) `strategy_timeframes_*` instead.

## Caveats

- The DOW split is now **done** (table above) — Group A/A′ reflect per-config signs. Group
  C whole-strategy retirements still need the combo test before deletion.
- Many cells are one realized ~10-week regime. n≥30 is the floor, not a guarantee.
- This is a prune of the **alert** surface, not a sizing change. Down-weighting weak cells
  (vs cutting) needs the position-sizing layer (Fork B step 3), which does not exist yet.
