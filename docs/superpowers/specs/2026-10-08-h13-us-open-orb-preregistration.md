# H13 — pre-registration for the US-open opening-range breakout

**Date:** 2026-10-08 · **Status:** pre-registered, unrun · **Issue:** H13 (#848), decided
in #919 under the #864 map · **Round:** 1, first of at most three candidates

This file fixes the construction, the gate and the kill-switches **before** any H13 number
exists. Every choice below was made in the #919 grilling session (2026-10-08) without looking
at H13 outcomes. A change to any of them after the first look is a new trial, not a
correction.

## 1. Hypothesis

A 15m opening-range breakout anchored at the **US cash-equity open** carries an edge on the
crypto majors, net of modelled cost. It is the cheapest form of H13(a) — entry-time
conditioning on proximity to a session open — because `orb_breakout.py` already ships and
only its anchor changes.

The prior is against it: conditioning axes are 6-for-6-plus-one-amended against (AGENTS.md
standing verdicts). That is why it is pre-registered rather than explored.

## 2. Trial accounting

- **Round 1's trial count is fixed at N = 3** (#833 ruling, applied in #864). H13 spends
  **one** of the three. Its DSR is computed at N = 3 whatever the other candidates turn out
  to be, so H13's bar does not move when they are picked.
- **Out of round 1, back to #848:** the open-proximity split over `backtest_trades` (a
  conditioning feature on a book at about −0.17R a trade) and the blackout-suppression leg
  H13(b). Either one would cost a trial of its own.
- **Prior looks, disclosed and not counted.** The 2026-06-04 conditional-edge test (register
  row A10) tested session *blocks* over the whole detector book, a different construction.
  The BTC 365-day hour-of-day extremes scan ranked 14 UTC first among 24 hours, but it
  measures where the daily extreme sits, a fade statistic, and it did not choose the anchor:
  the anchor comes from the NYSE calendar (§3), which is 13:30 or 14:30 UTC and never the
  scan's 14:00. If the anchor were the scan's hour, the scan's 24 looks would count.

## 3. Construction — frozen

| Item | Value |
| --- | --- |
| Anchor | NYSE cash open, 09:30 America/New_York, daylight-saving aware (13:30 UTC in EDT, 14:30 UTC in EST), from an exchange calendar |
| Days | NYSE trading days only. Full holidays are skipped (no cash open, so no anchor). Early-close days are kept, with the session close at 13:00 ET. No weekend trades |
| Timeframe | 15m |
| Universe | BTCUSDT, ETHUSDT, SOLUSDT — the only symbols with real 15m bars. Pooled into one construction |
| Opening range | the first 2 bars from the anchor (09:30–10:00 ET), the detector's shipped `range_candles` |
| Signal | the first 15m close above the range high (long) or below the range low (short) after the range, at most one fire per direction per symbol per day, as the detector ships. Both directions pooled |
| Signal window | from the end of the range until the session close (16:00 ET, or 13:00 ET on an early close) |
| Entry | the engine's standard fill on the bar after the signal close |
| Stop | the opposite side of the range, widened to `min_sl_pct` |
| Target | fixed `tp_r` = 1.5 × risk (the engine's derived path; the detector's structural 1.5 × range-width target is not used) |
| Time exit | any open position is closed at the session close |
| Gates | all off: regime, HTF, ADR, `volume_suppress`, `day_filter`, F9 ATR floor |
| Costs | production values read from `config/strategy_params.toml` at run time (today: `fee_pct` 5 bps, `slippage_bps` 2 per taker leg, `min_sl_pct` 0.5%), never restated in the driver |
| Sample | every bar from each symbol's first 15m bar up to and including 2026-10-08 (the commit date of this file). Later data is a forward record and is never run through this gate |

The 5m/1m "mark the open candle" setup H13 was re-raised with is **not** this construction
and is not testable: nothing below 15m exists in `analytics.db`. This is a different setup,
not a coarse version of that one.

## 4. Observation unit and the gate

- **Book-day series**: one row per NYSE trading day with at least one resolved trade, the
  mean net R across that day's trades. The three majors fire at the same open with
  ρ ≈ 0.5, so book-day rows absorb the cross-symbol correlation once; per AGENTS.md they are
  not deflated again.
- **Edge** = `analytics.research_guards.passes_gate(dsr, pbo, boot_lo)` on that series:
  DSR ≥ 0.95 at N = 3, PBO ≤ 0.5, block-bootstrap lower bound > 0.
- **PBO's second column.** `cscv_pbo` needs at least two trials. PBO runs across round 1's
  candidates on a shared book-day axis. If round 1 ends with H13 alone, the second column
  is the shipped 00:00-UTC `orb_breakout` on the same 15m panel under §3's settings, an
  existing construction rather than a new search, so it adds no trial.

## 5. Kill-switches — checked before the gate is read

- **K1 Power.** After the build, measure the book-day sd on the control column (the
  00:00-UTC orb, same panel) and run `tools/distil_power.py --units per_book_day
  --sr-footing per_obs --n-trials 3 --sd <measured>` with `--sr-variance` = max(measured
  across available columns, 0.005) and no `--n-eff` (book-day rows). **Do not run H13** if
  the required effect exceeds **+0.15R per book-day**. Indicative price on 2026-10-08, from
  assumed inputs (1,700 book-days, sd 1.0R): +0.059R at variance 0.0005, +0.100R at 0.005.
- **K2 Same-bar ties.** Resolve ambiguous bars (stop and target both inside one 15m bar)
  both ways: adverse-first, as the engine does, and target-first. If the gate verdict
  differs between the two, H13 is **INDETERMINATE**, never a pass. Report the ambiguous
  count in both resolutions (the ST56 lesson; `analytics/giveback.py` is the model).
- **K3 Fidelity.** The anchor change must reproduce the shipped detector's signals exactly
  when the anchor is set to 00:00 UTC on the 15m fixture, and pass `tests/test_lookahead.py`.
  Otherwise the build is wrong and nothing runs.

## 6. Reading the verdict

- **PASS**: the gate clears under both tie-break resolutions. H13 is an edge.
- **FAIL**: read as "no effect found". A filed NO only where
  `analytics.audit_guard.powered_null` licenses one (CI containment against the bar); a
  failure to clear the gate is not power.
- **INDETERMINATE** (K2) and **killed** (K1, K3): a failure to clear, never a null, and
  never quoted as "no edge".

## 7. Tradeable test — only after a PASS

1. **Minimum lot.** At 1% risk on the equity held when the test runs, drop every trade
   below the exchange minimum lot (skipped, never sized up), then re-run the gate on what
   remains. It must still pass. The equity figure stays local and never enters this repo.
2. **Survival.** #915's f = min(1/k at 5% over 12 months, half-Kelly net), at the measured
   win rate and trade frequency, must be at least the 1% measurement size.
3. **XS benchmark.** Report the correlation of H13's book-day returns to the XS sleeve's,
   and whether H13 adds to it. Reported, not a leg.

## 8. What the build owes (the hand-off)

- `orb_breakout.py` honours an anchor (today it ignores `session_hour_utc`), as a
  calendar-driven, DST-aware cash open rather than a fixed UTC hour, with the signal window
  ending at the session close.
- A session-close time exit in the engine, which has none today.
- Both tie-break resolutions for K2.
- The run itself, with K1–K3 reported before the gate.

## 9. Decision log

Each line names the observable that would reverse it.

- One trial, ORB leg — reversed if round 1 is re-scoped to fewer than two other
  candidates *and* the operator re-opens the trial budget (#833).
- DST-aware cash open, not the fixed 14:00 UTC `session_windows.py` boundary — reversed only
  by evidence that crypto flow keys on a fixed UTC hour, which would need its own trial.
- Prior looks not counted — reversed if the anchor is ever changed to 14:00 UTC.
- 15m majors — reversed if a 15m backfill of the universe lands before the run (that would
  be a new construction, so a new pre-registration).
- `tp_r` 1.5 — ties the target to the break-even on file in #912 (51.2% all-taker at a
  0.5% stop); no other value is admissible without a new trial.
- Book-day footing with the round's candidates as PBO columns — reversed if the other
  candidates cannot produce a book-day series on a shared axis, in which case the
  00:00-UTC control column applies.
