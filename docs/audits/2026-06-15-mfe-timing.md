# MFE timing within the hold window — exit sub-project B, step 1

**Date:** 2026-06-15 · **Status:** measurement (read-only) · **Parent:**
`docs/redesign/2026-06-05-exit-improvement-spec.md` §2 · **Resolves:** the caveat
N2 left open (`docs/audits/2026-06-11-mfe-mae-diagnostic.md`) — *when* in the hold
window the favorable extreme is reached, needed before committing a time-stop value.

## Why this first

N2 proved the 40%-expiry leak is exit-fixable (magnitude of MFE) but left the
**timing** unmeasured. A time-stop set shorter than the time winners take to reach
their decisive favorable level would clip real winners. This step measures that
timing so the replay engine sweeps the time-stop within a safe range, and confirms
the breakeven / partial lock level (≈ 1R) is well-placed in time.

## Method

Read-only over `analytics.db` `signal_alert_outcomes` (2,561 resolved scoreable:
expired 1009 / loss 1393 / win 159). Per alert, walk the held window
(`candle_ts_ms` < open_time ≤ `outcome_filled_at_ms`), build the favorable-excursion
series in R (÷ |entry − sl|), and record the bar index of the favorable peak and the
first crossing of +1R / +0.5R. Same window logic as `analytics/exits/mfe_mae.py`;
timing uses the raw favorable path (gross of the exit-bar magnitude conventions).

## Results

Hold caps (expired bars_held p50 = `max_hold_bars`): 15m 96 · 1h 48 · 4h 30 · 1d 14.

**Winners — bars to first +1R** (clip-risk check):

| tf | n | held p50 | bars→1R p50 | bars→1R p90 |
| --- | --- | --- | --- | --- |
| 15m | 62 | 66 | 26 | 65 |
| 1h | 62 | 24 | 7 | 17 |
| 4h | 23 | 13 | 2 | 7 |
| 1d | 12 | 1.5 | 1 | 3 |

**Expired — favorable peak timing + reach:**

| tf | n | held p50 | bars→peak p50 | frac-of-hold p50 | reach-1R frac | (of those) bars→1R p50 / p90 |
| --- | --- | --- | --- | --- | --- | --- |
| 15m | 802 | 96 | 54 | 0.56 | 0.58 | 34 / 78 |
| 1h | 170 | 48 | 30 | 0.64 | 0.80 | 12 / 34 |
| 4h | 36 | 30 | 20 | 0.67 | 1.00 | 6 / 19 |

(1d expired n=1 — ignored.)

## Verdict (parameter oracle for the build)

1. **Breakeven + partial at +1R are timing-safe.** Eventual winners cross +1R
   early (p50 ≈ 7 bars on 1h, 2 on 4h, 26 on 15m), and most expired trades cross
   +1R *before* fading (58–100%). Locking at +1R converts faders to scratch/partial
   without cutting winners. Confirms the N2 lock level ≈ 1R.
2. **Time-stop has a per-tf floor — sweep, never hard-set short.** A time-stop must
   stay **≥ winner bars→1R p90** or it clips winners: floor ≈ **15m 65 · 1h 17 ·
   4h 7** bars. The replay engine sweeps the time-stop in `[floor → current
   max_hold]` (15m 65→96 · 1h 17→48 · 4h 7→30); below the floor is rejected a priori.
3. **Capital-freeing is real.** Expired trades peak mid-window (frac 0.56–0.67)
   then decay to the cap, so a time-stop in the upper range exits faders near/after
   their peak — frees concurrency capacity (a P1-caps lever) with little MFE loss.

Next: build the pluggable exit-replay (#1 time-stop + #2 BE + #6 partial-at-1R vs
policy #0), feed `(new_R, new_exit_ts)` into the P1 `PaperBook`, judge on
Sharpe/maxDD jointly with sizing (exit spec §4–§5).
