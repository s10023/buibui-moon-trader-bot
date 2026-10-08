# A15 re-run — composite exit at 1h/4h, net of cost, ties resolved from 15m

Re-runs the 2026-06-15 exit-policy A/B (fixed vs composite: time-stop + breakeven at 1R +
50% partial at 1R) under the two caveats its register row carried: the adverse-first
same-bar tie-break (C2) and the gross cost basis (C4). Asked by #924, which blocks #921.
Read-only: no detector, config, golden or rating was touched, and no trial is spent from the
round budget.

## Verdict

At 1h the filed NO stands: net of cost, the composite's portfolio Sharpe is −3.31 against
fixed's −2.94. At 4h it flips on the point estimate: composite −2.66 against fixed −2.90.
Neither reading is the tie-break's doing. 15m resolution touched 0 fixed-arm bars at either
timeframe, and 3 (1h) and 4 (4h) composite-arm bars, which moved the composite's mean R by
+0.0008 and +0.0024 and left the 1h verdict unchanged; the 4h flip is already there under
adverse-first (−2.71 vs −2.90). The cost basis is what decides. Gross, the composite leads at
both timeframes, because its shorter holds let the paper book admit about half as many trades
again, and netting charges each of those its drag. Do not read either Sharpe gap as a
measurement. Both books lose at every reading. The ledger spans 0.54 years, so an annualised
Sharpe near −3 carries a standard error of roughly 3. The paired per-trade delta (composite −
fixed: +0.089R at 1h, +0.101R at 4h) has UTC-day-clustered CIs that include zero at both
timeframes. The re-run therefore gives the "exit is the gap" premise no support in either
direction: at 1h and 4h the composite neither rescues a losing book nor can be told apart from
the fixed exit. 15m, 69% of the ledger, stays unresolved because nothing below 15m is held.

## What changed from the filed run

| | filed (2026-06-15) | this re-run |
| --- | --- | --- |
| ledger | 2,561 alerts, all timeframes pooled | 2,047 (1h) and 639 (4h), each its own book |
| costs | gross | net: `round_trip_drag_r` per trade (fee 0.05% + slippage 0.02% per leg) |
| same-bar ties | adverse-first | 15m bars walked with the same state machine; a 15m tie stays adverse-first |
| everything else | per-tf time-stop floor, BE and partial at 1R, runner to the alert's `rr_ratio`, P1 `PaperBook` with default `SizingConfig` | unchanged |

A tie is a bar that touches the stop in force and an unconsumed favourable level (take-profit,
the partial, or the breakeven arm). Inside a 15m walk breakeven arms from the next 15m bar,
the same no-look-ahead rule the native walk applies per bar. A bar whose 15m set is
incomplete stays adverse-first; none was.

## Results

Snapshot of `analytics.db` taken 2026-10-08 09:59 UTC; resolved alerts 2026-03-25 →
2026-10-07, 3 symbols, every 1h/4h stop at 2.0% (median). Reproduce with
`PYTHONPATH=. poetry run python tools/exit_audit.py --replay --tfs 1h,4h --fine-tf 15m --net`
(drop `--net` for the gross table).

| tf | basis | ties | fixed Sharpe | composite Sharpe | sized (fixed / comp) | ambiguous bars (fixed / comp) | resolved |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1h | net | adverse-first | −2.940 | −3.311 | 459 / 704 | 0 / 3 | 0 |
| 1h | net | 15m | −2.940 | −3.311 | 459 / 704 | 0 / 3 | 3 |
| 1h | gross | 15m | −1.222 | −0.072 | 458 / 688 | 0 / 3 | 3 |
| 4h | net | adverse-first | −2.901 | −2.710 | 229 / 361 | 0 / 4 | 0 |
| 4h | net | 15m | −2.901 | −2.663 | 229 / 361 | 0 / 4 | 4 |
| 4h | gross | 15m | −2.184 | −1.280 | 229 / 360 | 0 / 4 | 4 |

No 15m bar inside a resolved walk was itself a tie. Per-trade paired delta, composite −
fixed, 15m-resolved, 95% CI clustered by UTC entry day: 1h **+0.0893R** [−0.0076, +0.1745]
(n=2,047); 4h **+0.1010R** [−0.0704, +0.2624] (n=639). The delta is identical gross and net,
since both arms share entry and stop and so pay the same drag per trade. That is why the C4
effect reaches the portfolio only through admission: the composite frees cap headroom
sooner, sizes 53% (1h) and 58% (4h) more trades, and each one pays the drag.

### Why ties are this rare

Every 1h/4h stop in the ledger is the flat 2%. A composite tie needs one bar spanning −1R and
+1R, a 4% range; a fixed-arm tie needs −1R and the alert's take-profit, several R wider
still. The 1h/4h tape rarely prints either. The C2 concern was sound in principle, because
adverse-first does fall harder on the tighter post-arming arm, but on this ledger it has
almost nothing to act on.

## Readings outside #924's scope

- **The filed NO does not reproduce on today's ledger under its own construction.** Pooled,
  gross and adverse-first (exactly the 2026-06-15 run), the current 9,310-alert ledger reads
  composite −0.41 against fixed −0.77, the reverse of the filed +0.22 vs −0.19. Netted, the
  pooled NO returns (composite −3.00 vs fixed −2.21). The filed direction was a 2,561-alert
  reading that the cost basis, not the data, keeps alive.
- **15m** (net, adverse-first): composite −3.26 vs fixed −1.79, per-trade delta +0.0052R
  [−0.0470, +0.0570]. One tie in 6,467 alerts, so C2 is immaterial there too, although this
  cannot be checked from held data.
- **1d** (net, adverse-first): composite −0.99 vs fixed −2.09, per-trade delta +0.2366R
  [+0.0609, +0.4035], n=157, with 11 unresolved ties that 1h/4h bars could resolve. This is
  the only slice whose CI excludes zero. It is one of four slices read, on 157 alerts, so it is a
  lead for a pre-registered test rather than a result.

## Caveats

- Costs are modelled, not realised, so every net figure is an optimistic bound.
- The headline remains the filed portfolio Sharpe on the cap-admitted subset, which mixes the
  exit effect with which trades get admitted; the per-trade delta is the cleaner comparison.
- One time-stop value per timeframe, and one global composite, as filed. Per-edge assignment
  and a time-stop sweep remain untested here.
