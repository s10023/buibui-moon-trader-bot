# N2 — MFE/MAE diagnostic: exit-fixable vs entry-broken (exit spec §2)

**Date:** 2026-06-11 · **Status:** verdict (gates all exit-policy work) ·
**Spec:** `docs/redesign/2026-06-05-exit-improvement-spec.md` §2 ·
**Tool:** `PYTHONPATH=. poetry run python tools/exit_audit.py --min-n 30`
(read-only; lib `analytics/exits/mfe_mae.py`).

## TL;DR — GO: the expiry leak is overwhelmingly exit-fixable

- **Coverage 100%:** 2,489 / 2,489 resolved `signal_alert_outcomes` rows scored
  (982 expired / 1,350 loss / 157 win; ledger 2026-03-25 → 2026-06-11).
- **Pattern 1 fires globally.** The expired cohort (39.5% of resolved) reaches
  **≥ 1R in 63.7%** of trades (≥ 0.5R in 86.3%, median MFE **+1.35R**) while
  being asked to hit a median tp_r of **3.5R**. Every expired cell at n ≥ 30
  clears reach_05 ≥ 0.74. The TP is unreachably far; the edge is real but
  un-banked. → **partial at 1R / breakeven — exit fix, big win.**
- **Pattern 3 fires on short loss cells.** `bos 15m short` losers were **+1R
  before stopping out in 50% of cases** (median loss-MFE +1.03R, n=44);
  `pin_bar 1h short` and `trend_day 4h short` similar but milder. Breakeven /
  trail converts these to scratches. And this measurement is *conservative*
  (loss exit-bar favorable extreme excluded), so the true benefit is ≥ shown.
- **Pattern 4 (just wrong) = the long side.** Every n ≥ 30 long loss cell has
  median MFE ≤ 0.2R and reach_10 ≈ 0 (`bos`, `inside_bar`,
  `morning_evening_star`, `wick_fill` 15m longs). Exits can't help these —
  prune/down-weight (one more datapoint for the direction overlay).
- **No expired cell at n ≥ 30 is entry-broken** (Pattern 2). The only
  candidate is `inside_bar 15m long` (reach_05 0.44) at n=16 — below floor.

## Method

For each resolved row, walk the OHLCV bars actually held
(`candle_ts_ms < open_time ≤ outcome_filled_at_ms`) and record MFE_R / MAE_R
in R units (÷ |entry − sl|). Conservative intrabar conventions (spec §4
adverse-first, applied to measurement): loss exit bar's favorable extreme
does **not** count toward MFE; win MFE clamps to rr_ratio (no overshoot
credit) but the win exit bar's adverse extreme **does** count toward MAE;
expired counts both extremes of every bar; both floored at 0. Excursions are
**gross of costs** (net realized R lives in `outcome_r`).

## Cohort roll-up (all cells)

| outcome | n | mfe_mean | mfe_p50 | mae_mean | mae_p50 | reach_05 | reach_10 | tp_r_p50 | bars_p50 | outcome_r_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expired | 982 | +1.41 | +1.35 | +0.46 | +0.45 | 0.86 | 0.64 | 3.5 | 96 | +0.72 |
| loss | 1,350 | +0.52 | +0.32 | +1.25 | +1.14 | 0.36 | 0.16 | 3.5 | 22 | −1.00 |
| win | 157 | +2.72 | +2.50 | +0.28 | +0.22 | 1.00 | 1.00 | 2.5 | 28 | +2.72 |

Reading: expired trades die *in profit* (+0.72R mean mark-to-market) with a
median best excursion of +1.35R — they fail only the 3.5R ask. Winners' MAE
is tiny (p50 0.22R), so a breakeven stop armed near +1R would rarely kill a
true winner. Losses sit at MAE p50 ≈ 1.14 (slightly > 1 = gap-through-stop,
expected). Win MFE ≈ rr by construction (clamped) — not informative.

## Expired cohort × cell (n ≥ 30)

| strategy | tf | dir | n | mfe_p50 | mae_p50 | reach_05 | reach_10 | tp_r_p50 | r_mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bos | 15m | short | 61 | +1.85 | +0.37 | 0.98 | 0.82 | 3.0 | +1.08 |
| engulfing | 15m | short | 85 | +1.22 | +0.43 | 0.84 | 0.62 | 4.0 | +0.64 |
| hammer_hanging_man | 15m | short | 50 | +0.92 | +0.49 | 0.80 | 0.44 | 4.0 | +0.49 |
| inside_bar | 15m | short | 130 | +1.27 | +0.36 | 0.91 | 0.61 | 3.5 | +0.64 |
| morning_evening_star | 15m | long | 34 | +0.78 | +0.57 | 0.74 | 0.41 | 3.0 | +0.27 |
| morning_evening_star | 15m | short | 160 | +1.37 | +0.34 | 0.87 | 0.63 | 3.5 | +0.79 |
| morning_evening_star | 1h | short | 36 | +1.76 | +0.39 | 0.92 | 0.86 | 4.0 | +1.12 |
| pin_bar | 15m | short | 132 | +1.04 | +0.46 | 0.79 | 0.50 | 3.0 | +0.62 |

At min_n=15 the picture holds (`bos 15m long` 0.93/0.82 reach, `doji 15m
short` 1.00/0.70, `bos 1h short` 0.94/0.65, `inside_bar 1h short` 0.88/0.82);
the lone weak cell is `inside_bar 15m long` (reach_05 0.44, n=16).

## Loss cohort × cell (n ≥ 30)

| strategy | tf | dir | n | mfe_p50 | mae_p50 | reach_05 | reach_10 | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bos | 15m | long | 47 | +0.15 | +1.11 | 0.15 | 0.00 | just wrong |
| bos | 15m | short | 44 | +1.03 | +1.09 | 0.66 | 0.50 | **BE/trail fix** |
| engulfing | 15m | short | 71 | +0.35 | +1.13 | 0.35 | 0.07 | just wrong |
| hammer_hanging_man | 15m | short | 38 | +0.21 | +1.09 | 0.32 | 0.00 | just wrong |
| inside_bar | 15m | long | 41 | +0.19 | +1.10 | 0.12 | 0.00 | just wrong |
| inside_bar | 15m | short | 113 | +0.24 | +1.12 | 0.23 | 0.07 | just wrong |
| inside_bar | 1h | short | 52 | +0.38 | +1.17 | 0.40 | 0.19 | marginal |
| morning_evening_star | 15m | long | 52 | +0.16 | +1.08 | 0.15 | 0.02 | just wrong |
| morning_evening_star | 15m | short | 82 | +0.37 | +1.12 | 0.32 | 0.07 | just wrong |
| morning_evening_star | 1h | short | 52 | +0.37 | +1.13 | 0.35 | 0.21 | marginal |
| pin_bar | 15m | short | 75 | +0.38 | +1.09 | 0.41 | 0.11 | just wrong |
| pin_bar | 1h | short | 31 | +0.61 | +1.16 | 0.58 | 0.26 | **BE/trail fix** |
| wick_fill | 15m | long | 34 | +0.17 | +1.25 | 0.09 | 0.00 | just wrong |

At min_n=15: `trend_day 4h short` (n=26, mfe_p50 +0.84, reach_05 0.73,
reach_10 0.42) is a third strong BE/trail candidate — fits its momentum-book
profile.

## Verdict against the spec's 4-pattern grid

1. **"Expired often reaches ≥ 1R but tp_r higher" → CONFIRMED, dominant
   pattern.** All 8 expired cells at n ≥ 30 (and 13 of 14 at n ≥ 15). Action:
   **partial at ~1R + breakeven** (spec §3 policies #6 + #2); a per-cell
   time-stop sweep (#1) is secondary — expired trades already end +0.72R mean,
   the leak is un-banked MFE, not capital lock-up.
2. **"Expired rarely reaches +0.5R" → NOT FOUND at the n-floor.** Only
   `inside_bar 15m long` (n=16) qualifies; route it to the direction-overlay
   prune list, not to exit tuning.
3. **"Loss with high MFE before SL" → CONFIRMED for 2–3 short cells.**
   `bos 15m short` (half its losers saw +1R), `pin_bar 1h short`,
   `trend_day 4h short`. Action: breakeven (arm ≈ +0.75R…+1R) / trail —
   exactly the cells where it converts −1R into scratch.
4. **"Loss low MFE, fast MAE" → CONFIRMED for all long loss cells + most 15m
   short candlestick cells.** Exits cannot help these; they are entry/direction
   problems. Do **not** spend exit-DOF on them (spec §9 anti-goal).

**Go/no-go: GO** for the exit-policy phase, scoped to spec §3 v1 = **#2
breakeven + #6 partial-at-1R + #1 time-stop**, evaluated per §4/§5 (exit
replay on the same ledger entries, conservative intrabar rules, OOS split,
n ≥ 30, DSR/PBO guards, judged jointly with P1 sizing — not raw avg_r). The
diagnostic also pre-answers the parameter oracle: the reachable lock level is
≈ 1R (median expired MFE +1.35R, reach_10 0.64), not 1.5R+.

This is **not** a tp_r re-sweep of the TA book (stop-doing list holds): no
detector or TOML changes; the next step is the §4 exit-replay engine over the
live ledger, with policy #0 (status quo) as the A/B baseline.

## Caveats

- **Cost basis mixed:** `outcome_r` mixes pre/post-PR-3 rows (raw vs net of
  costs). Excursions themselves are gross and unaffected; only the
  `outcome_r_mean` column carries the mix.
- **n is not independent:** strategies co-fire on the same candle/move, so
  per-cell rows share underlying price paths. Treat tables as descriptive;
  the policy sweep applies the P0 multiple-testing guards.
- **15m shorts dominate** (the live config's fire mix); long cells and 4h/1d
  are thin. 19 open rows excluded.
- `bars_held` for expired = max_hold by construction (96 on 15m, 48 on 1h);
  MFE *timing* within the window is not measured here — needed before
  committing a time-stop value.
- Win-cohort MFE is clamped to rr_ratio by design; win rows inform MAE
  (breakeven safety) only.
