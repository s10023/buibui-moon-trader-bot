# ST104 — `detect_eqh_eql` retune, three pre-registered arms: result

**Date:** 2026-08-29 · **SoT:** ST104 · **Pre-registration:**
`docs/superpowers/specs/2026-08-29-st104-eqh-eql-retune-prereg.md`
**Tools:** `tools/st104_sweep.py` (run + merge) · `tools/st104_score.py` (scoring)

**Verdict: all three arms FAIL. Net of modelled cost the `eqh_eql` book is
CONFIRMED-BAD at 15m and 1h on both directions — the control included — and the
tightening the operator's framework implies makes it WORSE, not better. Do not
adopt any of the three settings.**

## Headline verdict

Every one of the 32 (arm × timeframe × direction) cells returns a named verdict
and **none passes the three-leg gate**: 18 CONFIRMED-BAD, 9 NO-EDGE, 4
UNREACHABLE, 1 INSUFFICIENT. In the pre-registered primary cell — 15m short —
all four arms have bootstrap CIs lying wholly below zero, and the ranking is
`T2_look −0.1478 > T3_swing −0.2017 > baseline −0.2051 > T1_tol −0.2436`. The
arm carrying the operator's headline observation, T1's four-times-tighter
tolerance, finishes **last**, 0.039R below the control it was meant to improve.

This is not an underpowered null. The primary cell holds 8,255–106,151 trades at
effective n of 2,520–6,264 after day-clustering, and every CI half-width is a
fraction of the pre-registered bar. The study answers the question it asked.

**The premise was sound and the detector is not the problem.** The spec's D4
check passes: run over 20–28 Aug 2026 on BTCUSDT 15m, every arm reproduces the
journal's BTC EQL cluster at ≈77,800 — baseline and T1 emit a level at 77,906.4,
T2 and T3 additionally find 77,600 / 77,808 / 77,850 / 78,100. The detector sees
the level the operator traded. What fails is what happens after it fires.

## The primary cell, net of production cost

15m short, full history (2019-09 → 2026-08), 3 symbols, `min_sl_pct` 0.005,
5bps fee + 2bps slippage per leg:

| arm | n | days | DEFF | n_eff | mean stop | drag | avg_r | 95% CI | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 21,968 | 2,255 | 4.22 | 5,206 | 0.74% | 0.215R | −0.2051 | [−0.2531, −0.1602] | CONFIRMED-BAD |
| T1_tol | 8,255 | 1,539 | 3.28 | 2,520 | 0.72% | 0.218R | **−0.2436** | [−0.3057, −0.1797] | CONFIRMED-BAD |
| T2_look | 106,151 | 2,492 | 16.95 | 6,264 | 2.14% | 0.128R | −0.1478 | [−0.1886, −0.1077] | CONFIRMED-BAD |
| T3_swing | 84,976 | 2,544 | 9.58 | 8,867 | 0.79% | 0.211R | −0.2017 | [−0.2342, −0.1682] | CONFIRMED-BAD |

DSR is 0.000 for all four (a negative Sharpe cannot clear a positive
expected-maximum benchmark) and PBO is 0.115. `min_trl` is `inf` everywhere at
15m and 1h, which is the correct answer for a non-positive Sharpe rather than a
missing number.

## Per arm

**T1 — `tolerance_pct` 0.003 → 0.00075. FAIL, and it is the worst arm.** Tightening
to the precision the operator's framework actually trades at cuts fires to 37% of
baseline (8,255 from 21,968) and moves avg_r the wrong way, −0.2051 → −0.2436.
The 4h cells are the only place T1 is not negative (4h short +0.0747), and both
are NO-EDGE on CIs spanning zero at n≈400–630. ⚠ **This is the arm the SoT row
led with, and it is the one the data rejects hardest.**

**T2 — `lookback` 50 → 400. FAIL, but it is the least-bad arm, and the reason is
geometry rather than signal.** It fires 4.8× baseline and reads −0.1478, the best
of the four. It is also the only arm whose stops are structurally wide: a mean
2.14% against 0.72–0.79% for the other three. Since drag is
`2(fee+slip) × entry/risk`, T2 pays **0.128R per trade against ~0.215R** for the
others. Roughly the whole of its advantage over baseline (0.057R) is that cost
difference (0.087R). ⛔ **Do not read T2 as "a longer lookback works."** It is a
different stop geometry wearing the same detector's name.

**T3 — `swing_n` 5 → 2. FAIL.** Fires 3.9× baseline at −0.2017, statistically
indistinguishable from the control it was meant to beat. The spec required this
result to name which of two things it evidences: it is evidence about **the
scalar `swing_n`**, and **not** about a true shelf detector that folds adjacent
touches into one level. That construction remains untested.

## The dilution the spec asked us to name

T2 and T3 do not tighten the detector — they **loosen** it, 4.8× and 3.9×
respectively. The pre-registration framed all three as expressions of one idea
(match the framework's precision), but only T1 is a tightening. So the family is
not three measurements of one object: it is one tightening, and two different,
more permissive detectors. Their larger n buys narrower CIs on a *different*
population, which is why more trades do not translate into a better verdict.

## Tie-break exposure (ST56 / ST57)

Ambiguous bars — where the exit bar spans both stop and target, so OHLC alone
cannot order them — run **0.07%–0.22%** of trades at 15m and 0.13%–0.58% at 1h.
Adverse-first resolution therefore penalises the arms with tighter stops slightly
harder: baseline 0.22% and T1 0.21% against T2's 0.07%. **The direction of that
bias favours T2 and disfavours T1**, which is the same direction as the measured
result — so T1's last place is *marginally* flattered by removing the bias, not
explained by it: 0.15 percentage points of ambiguous bars cannot account for a
0.039R gap. The 1d cells are the exception at 3.2–10.0% ambiguity, and all four
are UNREACHABLE or NO-EDGE anyway.

## Preconditions and gates

- **P1 run_id namespacing** — satisfied by #718. Every study row carries an
  explicit `detector_params` blob, **including the baseline**, which is why the
  control could not overwrite production's own `eqh_eql` rows. Production held 0
  rows with non-null `detector_params` before this study.
- **P2 selection by run_id** — scoring reads only rows whose stored
  `detector_params` matches an arm, so the ~5.29× `backtest_trades` duplication
  factor is sidestepped by construction rather than deduped after the fact.
- **P3 causality** — all four arms pass `_first_lookahead_violation` on the 4h
  fixture (`tests/test_st104_sweep.py::TestArmCausality`). ⚠ **1d is UNTESTED,
  not clean:** T2's 400-bar lookback needs more history than the 200-day fixture
  holds, and T1/T2 emit no 1d signals on it at all. This matters because
  `swing_n` sets the width of a **centred** window — the exact shape that made
  `bos` non-causal on 100% of its signals — so the arms could not be assumed
  causal from the stock-parameter suite.
- **Day-clustering applied once**, via `cluster_stats` + a cluster bootstrap on
  `utc_day_keys`. `effective_independent_series` is deliberately NOT also
  applied: per ST80 the two are one correction with two estimators. The day key
  is a documented lower bound on a 24/7 tape, so every DEFF here is a floor and
  every verdict conservative.
- **No Sharpe folded to `abs()`.** Folding is required only when a
  negative-direction verdict is gated on DSR or MinTRL; here every negative claim
  goes through CI containment instead, so folding would only have made the gate
  more permissive.

## The correction that changed the study

A first pass ran at `BacktestSweepConfig`'s defaults, which are `fee_pct = 0.0`,
`slippage_pct = 0.0` **and `min_sl_pct = 0.0`**. It was discarded, and the reason
is worth recording because the failure was silent and looked like a result:

- **The CONTROL cleared the three-leg gate.** Baseline 15m short read +0.0429R,
  DSR 0.992, PBO 0.000, boot_lo +0.0043 → PASS, on the cell this repo files as a
  loser. Nothing errored; the table simply said the default detector works.
- **Without the stop floor, structural stops under 0.1% got through**, each
  paying over 1R of drag, and they dominated the mean: baseline's mean drag was
  0.427R unfloored against 0.215R floored.
- **Cost reverses the ranking.** Gross, baseline is the best arm (+0.0429) and T2
  the worst (−0.0116). Net, that order inverts. A gross reading of this detector
  is not a weaker measurement — it is the opposite one.

⇒ **A sweep config built programmatically inherits ZERO costs and ZERO stop
floor, and neither default is neutral for a detector whose drag scales as
`entry/risk`.** The runner now reads `fee_pct`, `slippage_bps` and `min_sl_pct`
from `config/strategy_params.toml` rather than restating them.

⚠ Costs are MODELLED, not realised, so every figure here remains an optimistic
bound whose error runs one way.

## Decision Log reconciliation

- **D1** (knobs cannot join the run_id → STOP): not triggered; #718 landed first.
- **D2** (all three arms n < 500 in the primary cell → UNREACHABLE): not
  triggered. All three clear 500 at 15m by a wide margin; the UNREACHABLE
  verdicts are all on 1d, where T1 emits 46 trades in total.
- **D3** (a positive verdict in an arm with wider stops at 1h/4h lands in the
  tie-break bias's favoured direction and is unproven): **not triggered, because
  there is no positive verdict to discount.** T2 is the wide-stop arm and it is
  CONFIRMED-BAD at 1h and 4h short.
- **D4** (the retuned detector must reproduce the journal's 25–26 Aug levels):
  **PASSES**, as measured above.

## What this does and does not license

**Closes:** the three pre-registered knobs, individually. Each has a named
verdict and none is worth adopting. Per the pre-registration a joint arm was
permitted only if ≥2 single arms cleared their screens; **none did, so the joint
retune is closed too** rather than merely unscheduled.

**Does not close:** a shelf detector that folds adjacent touches into a single
level. T3 was explicitly the closest available scalar to that idea, not the idea
itself, and it remains a new construction owing its own pre-registration and the
full three-leg gate.

**Reinforces** the standing verdict that tuning existing detectors is dead, and
adds a mechanism to it: for this detector family the binding term is not the
signal but `entry/risk`, so any future work here should move the stop geometry
first and the detection thresholds second — the same lesson the card path already
learned when its `min_rr` floor had to be taken net of drag.
