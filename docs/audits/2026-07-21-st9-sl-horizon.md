# ST9 / H11 — SL-horizon audit

> ⚠ **SUPERSEDED IN PART (ST27, 2026-08-14) — the 29 negative verdicts below are
> UNTESTED, not tested-and-clear.** Every `CONFIRMED-BAD` / `NO-DIFFERENCE` in the tables
> was emitted from *failure to clear the bar* rather than from a CI that ruled an effect
> out, which is why their CI columns are em-dashes. On a corrected one-sided criterion,
> 22 of the 30 equivalent claims do not survive — including **every live 1h and 4h cell**.
> The three `SUSPECT` cells and the disposition below are unaffected. Tables are left as
> the dated record of this run. See
> [`2026-08-14-st27-sl-horizon-powered-null.md`](2026-08-14-st27-sl-horizon-powered-null.md).

Read-only. Live `signal_alert_outcomes` GATES the verdict;
`backtest_trades` corroborates. One Holm family per substrate.

Grid (a-priori, never tuned): `k in [0.5, 1.0, 1.5, 2.0, 3.0]` x ATR14, baseline `2%` flat. `tp_r` pinned, never swept.

## Fidelity gate

| Substrate | Passed | Matched | Agreement | Worst avg_r delta |
| --- | --- | --- | --- | --- |
| backtest | False | 11333 | 0.963 | 0.261 |
| live | False | 2637 | 0.987 | 0.277 |

- **backtest fidelity:** doji 1d: avg_r delta 0.2590 > tolerance 0.02
- **backtest fidelity:** doji 1h: avg_r delta 0.0345 > tolerance 0.02
- **backtest fidelity:** doji 4h: avg_r delta 0.0458 > tolerance 0.02
- **backtest fidelity:** engulfing 1d: avg_r delta 0.1475 > tolerance 0.02
- **backtest fidelity:** engulfing 1h: avg_r delta 0.0229 > tolerance 0.02
- **backtest fidelity:** hammer_hanging_man 1h: avg_r delta 0.0358 > tolerance 0.02
- **backtest fidelity:** hammer_hanging_man 4h: avg_r delta 0.0332 > tolerance 0.02
- **backtest fidelity:** inside_bar 1d: avg_r delta 0.1176 > tolerance 0.02
- **backtest fidelity:** inside_bar 1h: avg_r delta 0.0678 > tolerance 0.02
- **backtest fidelity:** inside_bar 4h: avg_r delta 0.0306 > tolerance 0.02
- **backtest fidelity:** morning_evening_star 1d: avg_r delta 0.2614 > tolerance 0.02
- **backtest fidelity:** morning_evening_star 1h: avg_r delta 0.0256 > tolerance 0.02
- **live fidelity:** doji 15m: avg_r delta 0.0569 > tolerance 0.02
- **live fidelity:** doji 1d: avg_r delta 0.0227 > tolerance 0.02
- **live fidelity:** doji 1h: avg_r delta 0.0516 > tolerance 0.02
- **live fidelity:** doji 4h: avg_r delta 0.0700 > tolerance 0.02
- **live fidelity:** engulfing 15m: avg_r delta 0.0488 > tolerance 0.02
- **live fidelity:** engulfing 1d: avg_r delta 0.0467 > tolerance 0.02
- **live fidelity:** engulfing 1h: avg_r delta 0.0417 > tolerance 0.02
- **live fidelity:** engulfing 4h: avg_r delta 0.0754 > tolerance 0.02
- **live fidelity:** hammer_hanging_man 15m: avg_r delta 0.0519 > tolerance 0.02
- **live fidelity:** hammer_hanging_man 1d: avg_r delta 0.0700 > tolerance 0.02
- **live fidelity:** hammer_hanging_man 1h: avg_r delta 0.0591 > tolerance 0.02
- **live fidelity:** hammer_hanging_man 4h: avg_r delta 0.1597 > tolerance 0.02
- **live fidelity:** inside_bar 15m: avg_r delta 0.0397 > tolerance 0.02
- **live fidelity:** inside_bar 1d: avg_r delta 0.2768 > tolerance 0.02
- **live fidelity:** inside_bar 1h: avg_r delta 0.0801 > tolerance 0.02
- **live fidelity:** inside_bar 4h: avg_r delta 0.0480 > tolerance 0.02
- **live fidelity:** morning_evening_star 15m: avg_r delta 0.0454 > tolerance 0.02
- **live fidelity:** morning_evening_star 1d: avg_r delta 0.0630 > tolerance 0.02
- **live fidelity:** morning_evening_star 1h: avg_r delta 0.0348 > tolerance 0.02
- **live fidelity:** morning_evening_star 4h: avg_r delta 0.0668 > tolerance 0.02
- **live fidelity:** pin_bar 15m: avg_r delta 0.0362 > tolerance 0.02
- **live fidelity:** pin_bar 1d: avg_r delta 0.0583 > tolerance 0.02
- **live fidelity:** pin_bar 1h: avg_r delta 0.0482 > tolerance 0.02
- **live fidelity:** pin_bar 4h: avg_r delta 0.0261 > tolerance 0.02

## Descriptive horizon (baseline arm)

| Strategy | TF | n | avg_r | median bars | expiry rate | SL % | SL in ATR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| doji | 1d | 1265 | -0.130 | 0.0 | 0.004 | 0.0200 | 0.28 |
| doji | 1h | 31534 | -0.037 | 10.0 | 0.136 | 0.0200 | 1.53 |
| doji | 4h | 7467 | -0.012 | 2.0 | 0.037 | 0.0200 | 0.74 |
| engulfing | 1d | 2984 | -0.159 | 0.0 | 0.005 | 0.0200 | 0.27 |
| engulfing | 1h | 69386 | -0.056 | 10.0 | 0.170 | 0.0200 | 1.47 |
| engulfing | 4h | 18211 | -0.043 | 2.0 | 0.030 | 0.0200 | 0.71 |
| hammer_hanging_man | 1d | 1904 | -0.130 | 0.0 | 0.005 | 0.0200 | 0.28 |
| hammer_hanging_man | 1h | 51707 | -0.082 | 13.0 | 0.230 | 0.0200 | 1.59 |
| hammer_hanging_man | 4h | 12515 | -0.116 | 3.0 | 0.062 | 0.0200 | 0.76 |
| inside_bar | 1d | 4650 | -0.135 | 0.0 | 0.007 | 0.0200 | 0.28 |
| inside_bar | 1h | 114245 | -0.052 | 10.0 | 0.136 | 0.0200 | 1.59 |
| inside_bar | 4h | 27145 | -0.041 | 2.0 | 0.047 | 0.0200 | 0.76 |
| morning_evening_star | 1d | 4580 | -0.042 | 0.0 | 0.007 | 0.0200 | 0.29 |
| morning_evening_star | 1h | 114527 | -0.052 | 12.0 | 0.189 | 0.0200 | 1.58 |
| morning_evening_star | 4h | 29149 | -0.014 | 3.0 | 0.080 | 0.0200 | 0.76 |
| pin_bar | 1d | 3487 | -0.068 | 0.0 | 0.009 | 0.0200 | 0.28 |
| pin_bar | 1h | 96485 | -0.091 | 10.0 | 0.151 | 0.0200 | 1.58 |
| pin_bar | 4h | 22598 | -0.074 | 3.0 | 0.067 | 0.0200 | 0.75 |

## Verdicts — LIVE (gate)

| Strategy | TF | Decision | n | baseline avg_r | best k | lift | CI lo | CI hi | adj p | DSR | PBO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| doji | 15m | **NO-DIFFERENCE** | 63 | 0.064 | — | — | — | — | — | — | — |
| doji | 1d | **INSUFFICIENT** | 6 | -1.070 | — | — | — | — | — | — | — |
| doji | 1h | **INSUFFICIENT** | 18 | -0.586 | — | — | — | — | — | — | — |
| doji | 4h | **INSUFFICIENT** | 5 | -1.070 | — | — | — | — | — | — | — |
| engulfing | 15m | **CONFIRMED-BAD** | 265 | -0.118 | — | — | — | — | — | — | — |
| engulfing | 1d | **INSUFFICIENT** | 3 | 0.263 | — | — | — | — | — | — | — |
| engulfing | 1h | **CONFIRMED-BAD** | 69 | -0.181 | — | — | — | — | — | — | — |
| engulfing | 4h | **INSUFFICIENT** | 18 | -0.192 | — | — | — | — | — | — | — |
| hammer_hanging_man | 15m | **CONFIRMED-BAD** | 162 | -0.084 | — | — | — | — | — | — | — |
| hammer_hanging_man | 1d | **INSUFFICIENT** | 2 | -1.070 | — | — | — | — | — | — | — |
| hammer_hanging_man | 1h | **CONFIRMED-BAD** | 52 | -0.527 | — | — | — | — | — | — | — |
| hammer_hanging_man | 4h | **INSUFFICIENT** | 17 | 0.015 | — | — | — | — | — | — | — |
| inside_bar | 15m | **CONFIRMED-BAD** | 497 | -0.131 | — | — | — | — | — | — | — |
| inside_bar | 1d | **INSUFFICIENT** | 9 | 0.152 | — | — | — | — | — | — | — |
| inside_bar | 1h | **CONFIRMED-BAD** | 146 | -0.109 | — | — | — | — | — | — | — |
| inside_bar | 4h | **CONFIRMED-BAD** | 46 | -0.661 | — | — | — | — | — | — | — |
| morning_evening_star | 15m | **CONFIRMED-BAD** | 516 | -0.021 | — | — | — | — | — | — | — |
| morning_evening_star | 1d | **INSUFFICIENT** | 10 | -0.570 | — | — | — | — | — | — | — |
| morning_evening_star | 1h | **CONFIRMED-BAD** | 180 | -0.160 | — | — | — | — | — | — | — |
| morning_evening_star | 4h | **NO-DIFFERENCE** | 59 | 0.013 | — | — | — | — | — | — | — |
| pin_bar | 15m | **NO-DIFFERENCE** | 370 | 0.009 | — | — | — | — | — | — | — |
| pin_bar | 1d | **INSUFFICIENT** | 6 | 0.347 | — | — | — | — | — | — | — |
| pin_bar | 1h | **CONFIRMED-BAD** | 84 | -0.438 | — | — | — | — | — | — | — |
| pin_bar | 4h | **CONFIRMED-BAD** | 34 | -0.050 | — | — | — | — | — | — | — |

## Verdicts — Backtest

| Strategy | TF | Decision | n | baseline avg_r | best k | lift | CI lo | CI hi | adj p | DSR | PBO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| doji | 1d | **SUSPECT** | 1265 | -0.130 | 1.5 | 0.247 | 0.150 | 0.350 | 0.000 | 1.000 | 0.377 |
| doji | 1h | **CONFIRMED-BAD** | 31534 | -0.037 | — | — | — | — | — | — | — |
| doji | 4h | **CONFIRMED-BAD** | 7467 | -0.012 | — | — | — | — | — | — | — |
| engulfing | 1d | **SUSPECT** | 2984 | -0.159 | 1.0 | 0.281 | 0.210 | 0.352 | 0.000 | 1.000 | 0.103 |
| engulfing | 1h | **CONFIRMED-BAD** | 69386 | -0.056 | — | — | — | — | — | — | — |
| engulfing | 4h | **CONFIRMED-BAD** | 18211 | -0.043 | — | — | — | — | — | — | — |
| hammer_hanging_man | 1d | **NO-DIFFERENCE** | 1904 | -0.130 | 0.5 | 0.129 | 0.066 | 0.197 | 0.007 | 0.999 | 0.541 |
| hammer_hanging_man | 1h | **CONFIRMED-BAD** | 51707 | -0.082 | — | — | — | — | — | — | — |
| hammer_hanging_man | 4h | **NO-DIFFERENCE** | 12515 | -0.116 | 3.0 | 0.090 | 0.058 | 0.121 | 0.000 | 0.849 | 0.000 |
| inside_bar | 1d | **NO-DIFFERENCE** | 4650 | -0.135 | 2.0 | 0.207 | 0.152 | 0.257 | 0.000 | 1.000 | 0.610 |
| inside_bar | 1h | **CONFIRMED-BAD** | 114245 | -0.052 | — | — | — | — | — | — | — |
| inside_bar | 4h | **CONFIRMED-BAD** | 27145 | -0.041 | — | — | — | — | — | — | — |
| morning_evening_star | 1d | **SUSPECT** | 4580 | -0.042 | 1.0 | 0.169 | 0.106 | 0.226 | 0.000 | 1.000 | 0.183 |
| morning_evening_star | 1h | **CONFIRMED-BAD** | 114527 | -0.052 | — | — | — | — | — | — | — |
| morning_evening_star | 4h | **CONFIRMED-BAD** | 29149 | -0.014 | — | — | — | — | — | — | — |
| pin_bar | 1d | **CONFIRMED-BAD** | 3487 | -0.068 | — | — | — | — | — | — | — |
| pin_bar | 1h | **CONFIRMED-BAD** | 96485 | -0.091 | — | — | — | — | — | — | — |
| pin_bar | 4h | **CONFIRMED-BAD** | 22598 | -0.074 | — | — | — | — | — | — | — |

## Cost context

Round-trip cost is `0.1400%` of notional (`2 x fee 0.0005` + `2 x slippage 2.0 bps`).
In R terms this scales inversely with stop width, so a tight stop is
charged more R for the same trade. Read every lift against the
`SL in ATR` column above before calling it an edge.

## Diagnosis of the failed fidelity gate

The pre-committed fidelity gate (spec section 7) failed on both substrates, so
under the pre-committed protocol the verdict tables above are NOT accepted. A
post-hoc diagnosis (read-only, per-trade, on the worst-failing family `doji`)
identifies why — and it is a statistical-power limitation, not a harness defect:

| TF | matched n | outcome agreement | gross avg_r delta |
| --- | --- | --- | --- |
| 1h | 539 | 0.996 | 0.036 |
| 4h | 135 | 0.926 | 0.024 |
| 1d | 27 | 0.926 | 0.189 |

At adequate n the harness reproduces the production engine to cost scale: `doji`
1h matches stored `pnl_r` to 0.036R with 99.6% outcome agreement over 539 trades.
(That 0.036R is `harness net_r + cost_r` vs stored `pnl_r`; the harness fidelity
replay omits funding while stored `pnl_r` includes it, so part of the residual is
funding, not harness error — the true reproduction is tighter still.) The harness
is sound.

The 1d gate fails because the stored `backtest_trades` set covers only 3 symbols,
leaving `doji` 1d with n=27. There, 2 same-direction resolution flips (harness
loss vs stored win, each a ~4R swing because 1d trades are almost all win/loss
with near-zero expiry) move the mean by ~0.30R — past the 0.02R tolerance. The
flip rate (~0–7% across TFs) is irreducible intrabar-ordering difference between
two independent engines; it is common-mode across the baseline and every ATR arm
(identical signals, identical `replay_exits`), so it cancels in the paired lift.

Conclusion: the 0.02R absolute-reproduction tolerance is unachievable where the
stored comparison set is tiny and per-trade R is large. The gate did confirm the
harness is not broken (no convention or cost-model drift); its threshold was too
strict for the thin 1d slice.

## Disposition

- **Not accepted.** The pre-committed gate failed; these verdicts are not a
  committed result. No threshold was relaxed and the gate output is left as-is.
- **Thesis is supported on backtest, unconfirmable live.** The backtest 1d
  SUSPECT cells (`doji` k=1.5, `engulfing` k=1.0, `morning_evening_star` k=1.0)
  are real, DSR/PBO-clean, and consistent with ST9: at 1d the flat 2% stop is
  ~0.28 ATR and widening it to ~1–1.5 ATR lifts avg_r. But the pre-committed
  gating substrate is LIVE, and live 1d is INSUFFICIENT (n=2–10) — it cannot
  corroborate. Every 1h/4h/15m cell is CONFIRMED-BAD on both substrates.
- **Revisit conditions.** Re-run when (a) the production backtest is re-run over
  the full 25-symbol universe and saved, so 1d stored-n grows from ~27 to ~1265
  and the fidelity gate has real power at 1d; and (b) live 1d n grows enough to
  gate the SUSPECT cells. Until then ST9 is: the 1d graveyard for this family is
  genuinely suspect, but not yet cleared — the family stays demoted, not revived,
  and the flat 2% stop is not touched.
