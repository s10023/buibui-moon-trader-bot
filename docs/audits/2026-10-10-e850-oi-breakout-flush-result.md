# E850 — short after a breakout on rising open interest: fails the gate, and the short loses

Runs round 2's first trial (#850) exactly as pre-registered in
`docs/superpowers/specs/2026-10-09-e850-oi-breakout-flush-preregistration.md` (merged in PR #991).
The driver is `tools/e850_oi_breakout_flush.py`, which builds §3–§6 as written and nothing else.
It read a snapshot of `analytics.db` taken 2026-10-10 14:19 UTC, when every panel symbol already
held a 1d bar dated after 2026-10-08, so every bar in the sample is closed.

## Verdict

E850 fails the gate, and it fails in the opposite direction from the thesis. Shorting a 20-day
breakout made on unusually fast-rising open interest lost −0.115R per book-day over 1,024
book-days, with a block-bootstrap CI on the mean of [−0.193, −0.040]R that excludes zero below.
The annualised Sharpe is −1.79 on a bootstrap interval of [−2.91, −0.64]. DSR at N = 3 is 0.000,
PBO is 0.913 and boot_lo is negative, so all three legs fail. This is not "no effect found" and
it is not a powered null. The CI reaches past the −0.138R DSR bar, so `powered_null` is not
licensed. What the evidence supports is narrower and stronger: as constructed, the short is
confirmed-bad. The loss belongs to breakouts, not to open interest. The ungated control short
loses almost as much (−1.62 annualised; −0.685R per event against the treatment's −0.715R), and
the treatment-minus-control difference of −0.029R per event has a deflated t of −0.04. Rising
OI therefore does not turn a breakout into a flush on this panel. The beta guard does not
rescue the reading either: after regressing on the equal-weight universe, the intercept is
still −0.065R per book-day (t −2.48). Breakout names keep outperforming the market over the
next five days, which matches the `xsrev` prior the spec named. Funding was positive for the
short (+0.0047R per held position-day), so leaving it out made the gate conservative, and adding
it back cannot change the sign. So the OI-flush hypothesis gets a confirmed-bad short, not a
powered-null NO. This run licenses nothing about the long side: that would be a new trial
chosen after seeing this result, and the attribution says OI is not what drives it. Round 2's
slot 1 is empty; slots 2–3 stay with #983.

## What ran, in the spec's order

**Panel and costs.** The 24 USDT-M perps of `config/universe.toml` minus PAXGUSDT, read through the
`ohlcv` view on a connection pinned to `TimeZone='UTC'`. OI is `open_interest_archive.oi_contracts`
(`vision_um_metrics`), and the row stamped 23:00 UTC of D is the 5-minute snapshot taken at about
22:55, per the resample rule in `analytics/oi_archive.py`. It precedes D's close, so §10's
"stamp marks the end of the interval" reversal does not apply. Costs are read from
`config/strategy_params.toml` at run time: `fee_pct` 0.0005 and slippage 0.0002 per leg, so
2(fee + slip) = 0.0014 × N per round trip.

**K2.3, jumps (listed before any decision was built).** One non-zero hourly |Δ ln contracts| > ln 3
exists in the panel: ENAUSDT at 14:00 UTC on 2024-04-02, ×3.2 in its first archive hour after
listing, with `oi_usd` moving the same way. It is a listing ramp, not a redenomination, and is
named in the driver's `EXPLAINED_JUMPS`. Every other ln-3 crossing comes from a placeholder row
with 0 contracts and 0 USD. There are 330 such rows on 77 hours. Most are exchange-wide archive
blanks, for example all 14 symbols then listed from 2022-03-07 16:00 to 2022-03-08 01:00, and
every listed symbol on 2023-11-23, 2023-11-26, 2025-04-11, 2025-04-15 and 2025-07-21. The other
50 are single-symbol hours that resume the next hour. A zero row is read as a missing row, never
filled, so no decision is made on 2022-03-07 for those 14 symbols or on three BTC days in July
2024. No decision was removed for an unexplained jump.

**K2.1, truncation.** On 3,049 real decision days (every control event plus every 25th decidable
day), the control and treatment flags re-derived from bars and OI cut at D's close match the
full-history run with 0 mismatches. The fixture half
(`tests/test_e850_oi_breakout_flush.py::TestTruncation`) also proves the harness has teeth: a
frame that reads D+1's close is caught.

**K2.2, counts.** Before re-trigger skips, treatment ⊆ control holds event by event. Neither column
has a 1d calendar gap.

| Year | Treatment / control events |
| --- | --- |
| 2020 | 1 / 18 |
| 2021 | 12 / 24 |
| 2022 | 97 / 155 |
| 2023 | 199 / 364 |
| 2024 | 298 / 494 |
| 2025 | 220 / 320 |
| 2026 | 211 / 336 |

2020–2021 is BTC alone, as §8 disclosed. The treatment's percentile window needs 90 values, so
BTC's first treatment-eligible day falls in December 2020.

**The build (no mean read).** The treatment had 1,038 triggers. 416 were skipped while a
position was held and one exits after the sample end, which leaves 621 positions on 1,024
book-days. The control had 1,711 triggers, 808 skipped and one past the sample end, which leaves
902 positions on 1,184 book-days. No held window spans a calendar gap.

**K1, power (printed before any mean of either column):**

| Input | Value |
| --- | --- |
| E850 book-days n | 1,024 |
| E850 book-day sd | 1.2327R |
| `--sr-variance` | 0.005: the floor binds over a measured 0.00004 across E850 and the control |
| n_eff over the 24 symbols' daily returns (disclosure) | 1.852, deflator 3.600 |
| required Sharpe (`tools/distil_power.py`, N = 3, no `--n-eff`) | 0.1119 per book-day = 2.138 annualised, against the 2.5 ceiling: **PASS** |

**The gate:**

| | E850 |
| --- | --- |
| mean R per book-day | −0.1152 |
| Sharpe per book-day (annualised at 365) | −0.0935 (−1.786) |
| control Sharpe, annualised | −1.615 |
| DSR (N = 3, sr_variance 0.005) | 0.0000 |
| PBO (E850 + control, 2,189 UTC calendar days, 0R idle) | 0.9132 |
| bootstrap, annualised Sharpe (block 5, seed 7) | [−2.905, −0.641] |
| mean CI | [−0.1928, −0.0401]R |
| `powered_null` at ±0.1379R | NOT LICENSED |
| gate | **FAIL** |

**§6 readings** (printed for completeness; each one only changes a PASS):

| Reading | Value |
| --- | --- |
| beta guard | intercept −0.0647R (t −2.48), slope −24.0R per unit of equal-weight return, n 1,024 |
| XS overlap | ρ +0.154 on 1,024 open days, +0.119 on 2,122 calendar days |
| attribution | treatment −0.7146R per event (621) minus control −0.6854R (902) = −0.0292R; t raw −0.152, deflated −0.042 |
| funding received by the short | +0.00471R per held position-day (+1.55 bps of notional, n 3,105) |

## Readings fixed before the run, and one added after

The driver's docstring states every reading of the frozen spec, chosen before any E850 return
was read:

- A zero OI row is missing.
- "Nothing is decided on D" binds both columns.
- The percentile is the linear rolling quantile, which equals `numpy.percentile`.
- A trigger on D+5 is skipped, which is E841's reading.
- K1's variance spans E850 and the control, the only round-2 columns that exist.
- An unexplained jump blocks the 185 days of OI feeding a decision.
- XS is `replay_xs` on the full universe.
- Attribution uses a Welch t, which is conservative because treatment ⊂ control.

One reporting branch was added after the first run. The first output read "no effect found; NOT
LICENSED", which is the right gate result and the wrong sentence for a CI wholly below zero. The
driver now prints the confirmed-bad wording, mirroring the positive-CI branch H13 already had.
The construction, the gate, the kill-switches and every number are unchanged; the two runs'
pre-gate output is byte-identical.

## Independent check

A plain-loop re-implementation of BTC's trigger, percentile, sizing, P&L and cost, written
without the driver's frame code, reproduces the driver's 48 treatment and 77 control positions
with the same trigger dates and event R to 1e-9.

## What this does not say

- **Nothing about a long-breakout trade.** The control's loss as a short is a long's gross gain
  only before costs and before the funding a long pays. A 20-day Donchian long is a time-series
  trend construction, and the EWMAC trend sleeve is shelved. Testing it would be a new
  pre-registration chosen with this result in hand.
- **Nothing about OI as a conviction signal to add**, which the mechanics-backlog row of
  2026-08-23 proposes. The spec's decision log names it as a separate trial.
- **The two §8 biases run in opposite directions.** Modelled costs flatter the short, so the real
  loss is at least this large on that count. Survivorship works against it: today's top-25 are
  names whose rallies lasted, which may overstate continuation, and a panel including delisted
  names could shrink the loss. That is untested.
