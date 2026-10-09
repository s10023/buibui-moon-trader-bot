# H13 — 15m opening-range breakout at the NYSE cash open: fails the gate, positive and small

Runs round 1's first trial (#848) exactly as pre-registered in
`docs/superpowers/specs/2026-10-08-h13-us-open-orb-preregistration.md` (decided in #919, merged in
PR #935). The driver is `tools/h13_us_open_orb.py`, which builds §3–§6 as written and nothing
else. It reads `analytics.db` read-only on 2026-10-09 (~03:30 UTC), when every symbol already held
15m bars past 2026-10-08, so every session in the sample is closed.

## Verdict

H13 fails the gate under both same-bar tie-breaks, so the round-1 verdict is FAIL and the slot
does not carry a candidate forward. It fails on one leg only: DSR at N = 3 reads 0.205
(adverse-first) and 0.286 (target-first) against 0.95, while PBO (0.000) and the bootstrap lower
bound (+0.42 annualised Sharpe) both pass. This is not "no effect": the book-day mean is
+0.052R with a block-bootstrap CI of [+0.019, +0.085]R, which excludes zero, and the powered-null
check is licensed at the DSR bar of ±0.095R. Read together, H13 carries a real positive effect
that is too small for the gate at three trials, and the analysis rules out an effect as large as
the bar. The verdict does not depend on the two choices this driver made: tie-breaks move 13 of
6,250 trades, and at the 0.005 variance floor instead of the measured 0.0116 the DSR is still
0.69 and 0.79. More data does not rescue it: at the floor the observed Sharpe (0.072 per
book-day) clears the bar only near 20,000 book-days, about 79 years of NYSE sessions, and at the
measured variance the bar stays above it at 100,000. Round 1 therefore ends with all three slots
empty (E841 killed at K1, C836 emptied by #961, H13 failed), which is #864's to act on.

## What ran, in the spec's order

**K3 fidelity.** At its default 00:00-UTC anchor the changed detector reproduces the shipped
detector's 522 signals on `tests/fixtures/btc_15m_200d.parquet` exactly; the reference was frozen
from the pre-change code before any edit (`tests/fixtures/orb_utc_anchor_k3_signals.json`). The
NYSE anchor passes the truncation harness from `tests/test_lookahead.py` on the same fixture
(`tests/test_h13_us_open_orb.py::TestNyseAnchorIsCausal`, at least one point tested).

**The build** (no mean read): BTCUSDT from 2019-09-08, ETHUSDT from 2019-11-27, SOLUSDT from
2020-09-14, no 15m gaps, cut at 2026-10-08. 6,250 H13 trades on 1,769 NYSE sessions; exits 2,724
at the session close, 2,011 stops, 1,515 targets. The control column, the shipped 00:00-UTC orb on
the same panel and settings, holds 12,290 resolved trades on 2,584 UTC days. Costs from
`config/strategy_params.toml` at run time: `fee_pct` 0.0005, slippage 0.0002 per leg, `min_sl_pct`
0.005; `tp_r` 1.5; every gate off.

**K1 power** (printed before any mean of the H13 series):

| Input | Value |
| --- | --- |
| H13 book-days n | 1,769 |
| control book-day sd | 0.7841R |
| `--sr-variance` | 0.005 (floor; one column exists before H13 runs) |
| required Sharpe (`tools/distil_power.py`, N = 3, no `--n-eff`) | 0.0995 per book-day = 1.580 annualised at 252 |
| required effect | +0.0780R per book-day against the +0.15R ceiling: **PASS** |

**K2 same-bar ties.** 13 of 6,250 H13 trades and 20 of 12,290 control trades change outcome
between the two resolutions. Both resolutions give the same verdict, so K2 does not fire.

**The gate:**

| | adverse-first | target-first |
| --- | --- | --- |
| mean R / book-day | +0.0522 | +0.0574 |
| sd | 0.7240 | 0.7243 |
| Sharpe per book-day (annualised at 252) | +0.0722 (+1.145) | +0.0793 (+1.259) |
| control Sharpe per book-day | −0.0800 | −0.0745 |
| measured `sr_variance` (ddof 1, two columns) | 0.01158 | 0.01183 |
| DSR (N = 3) | **0.2050** | **0.2863** |
| PBO (2,586 UTC calendar days, 14 splits) | 0.0000 | 0.0000 |
| boot, annualised Sharpe (block 5, seed 7) | [+0.424, +1.842] | [+0.545, +1.947] |
| mean CI | [+0.0192, +0.0849]R | [+0.0246, +0.0896]R |
| DSR bar in R | ±0.0949 | ±0.0956 |
| `powered_null` | licensed | licensed |
| gate | FAIL | FAIL |

## Readings fixed before any H13 outcome was read

- **The control column** is the shipped 00:00-UTC detector with §3's target, stop floor, costs and
  gates, under the shipped detector's own lifetime (no session-close exit). A control trade still
  open at the sample end is dropped, and its book-day is the UTC date of its signal bar.
- **A signal bar must close strictly before the session close.** The engine fills on the next
  bar's open, so a signal on the bar closing at 16:00 ET would enter at the close and be flattened
  in the same instant, paying cost for no exposure.
- **The time exit** fills at the close of the last bar opening before the session close, with no
  slippage beyond the per-leg cost every exit pays.
- **K1's variance** is the floor, because only the control column exists before H13 runs. **The
  gate's** is the sample variance of the two columns' per-book-day Sharpes, each on its own
  book-day series, floored at 0.005.
- **The bootstrap block** is 5 book-days (one trading week), seed 7, the sleeves' seed.
- **The calendar** is `exchange_calendars`' XNYS (4.13.2, the wifey fork's pin): DST-aware opens,
  14 early closes in the sample, and special closures such as 2025-01-09 absent.

## What this does not say

- **PBO 0.000 carries no information here.** The only other column loses money, so H13 ranks
  first in every split. The leg passes, and it passes for a reason unrelated to overfitting.
- **No direction, symbol, year or anchor split was computed**, because each would be a further
  trial. In particular, nothing here licenses "longs at the US open work" or a 14:00-UTC variant.
- **This is not the reel setup** (5m mark, 1m expansion). Nothing below 15m exists in the database,
  so that setup remains untested.
- **Costs are modelled, not realised.** Every figure is an optimistic bound whose error runs one
  way.
- **The open-proximity split and the blackout leg** of #848 were left out of round 1 and remain
  open there. Each one costs a trial of its own.
