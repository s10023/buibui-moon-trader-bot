# H8 and A31 re-run on a causal tagger — the continuation effect was look-ahead

Re-runs A31 (`docs/audits/2026-10-08-a31-h8-vwap-cluster-rerun.md`) leg for leg, and
re-reads the H8 amended headline (`docs/audits/2026-07-24-h8-m1-indicator-conditioning.md`),
on the tagger fixed in #952. Until that fix `analytics/indicator_condition.py::_axes_as_of`
kept every bar with `open_time <= t`. On history the last 1d bar in that set is the day in
progress at entry, so its close lay up to 24h in the future. That close became the reference
price for every axis, and the same bar fed ATR14 and the regime label. The 1h bar opening at
`t` also entered the VWAP. Live rows were tagged as of `candle_ts_ms`, the signal candle's
open, which has the same leak. The fixed tagger sees only bars closed by `t`
(`open_time + bar_len <= t`), takes the reference price from the last closed 1h close, and
tags live rows as of `candle_ts_ms + tf`, the alert's own candle close. Read-only against a
scratch copy of the 2026-10-08 backup snapshot.

## Verdict

C836's nominating cell no longer reads BUILD. On the causal tagger, 4h shorts below the
weekly VWAP hold +0.160R on n=2,337. That is down from +0.467R on the same snapshot. The
with-vs-without lift falls from +0.903R to +0.123R on [+0.005, +0.241], and `audit_guard`
no longer clears the cell on the UTC-day cluster, so it reads INSUFFICIENT. Both
sensitivity legs agree. Cut at 2026-08-13 it reads +0.162R INSUFFICIENT. Without the
pre-causality-fix `bos` and `liquidity_sweep` rows it reads +0.082R, a lift of −0.004R,
INSUFFICIENT. Under #921's withdrawal rule C836's round-1 slot therefore stays empty.

The fix also withdraws H8's amended headline, "price location gates BOTH directions". On
one snapshot, run once with each tagger, every price-location cell on the leaky tagger has
the continuation shape: shorts below the reference win, and longs below it and shorts above
it lose. On the causal tagger no short-side location cell is gate-grade at any backtest
tier, and the long-side cells change state. Longs entered *above* the reference (weekly and
monthly VWAP, value area, Monday range, upper Bollinger band, bullish EMA stack, grinding-up
tape) underperform other longs at 1h and 4h.

What survives is 19 AVOID cells, all long-side and dominated by one collinear "extended up"
reading. They are in-sample on the same ten months as every rating, and they suppress
entries rather than add an edge. The two remaining BUILD cells (4h `ema_slope/rising/short`
and `ema_stack/mixed/short`) disappear when the pre-fix `bos` and `liquidity_sweep` rows
are removed, so neither stands on its own.

The continuation effect was the leak. A trade entered on a day that later closed down was
tagged "below" by that very close, which is why the filed `below/short` cell was strongest
for 00h entries (+0.952R), when the leak saw a whole day ahead.

## A31's legs, before and after

Same snapshot, same `tools/indicator_condition_audit.py` settings (`bar` 0.05, `alpha`
0.05, `min_n` 30, `n_boot` 2000, seed 12345, UTC-day cluster, one tier at a time). The
"leaky" column is the pre-fix tool run on this snapshot. It reproduces A31's 4h numbers to
the digit, so the two columns differ only by the tagger.

| leg | n tagged | `below/short` leaky | `below/short` causal | lift causal [CI] | verdict causal |
| --- | --- | --- | --- | --- | --- |
| backtest 4h | 7,925 | +0.467 BUILD | +0.160 (n 2,337) | +0.123 [+0.005, +0.241] | INSUFFICIENT |
| backtest 4h, entries before 2026-08-13 | 7,919 | +0.470 BUILD | +0.162 | +0.125 [+0.007, +0.247] | INSUFFICIENT |
| backtest 4h, no `bos` / `liquidity_sweep` | 6,649 | +0.429 BUILD | +0.082 | −0.004 [−0.133, +0.126] | INSUFFICIENT |
| live (all tf) | 9,319 | +0.142 INSUFFICIENT | −0.196 | −0.120 [−0.172, −0.063] | INSUFFICIENT |
| live (all tf), before 2026-08-13 | 4,959 | +0.224 INSUFFICIENT | −0.095 | −0.099 [−0.172, −0.022] | INSUFFICIENT |
| live 4h (diagnostic) | 639 | −0.417 NO-EDGE | −0.556 (n 222) | −0.386 [−0.669, −0.109] | AVOID |

The mirror cells move with it. At backtest 4h, `above/short` goes from −0.435R AVOID to
+0.037R INSUFFICIENT, and `below/long` from −0.436R AVOID to −0.045R INSUFFICIENT.
`above/long` goes from +0.103R to −0.313R, and it is now the AVOID cell.

The live 4h AVOID is post-hoc and thin (n=222), and live is not independent of the
backtest. Read it as agreeing with the backtest's direction, not as a finding.

Live n is 9,319 here against A31's 9,318: A31's snapshot held one row fewer, and the pre-fix
tool reads 9,319 on this one too. The live leg cut at 2026-08-13 holds 4,959 rows, not
A31's 4,961. Live entries now sit at candle close, so two candles opening just before the cut
fall after it.

## H8's headline cells, before and after

Gate-grade counts per tier, both taggers on this snapshot. The filed 2026-08-04 counts (30
BUILD, 25 AVOID, 17 long) were trade-clustered and on older data. The leaky column below is
the like-for-like baseline.

| tier | leaky BUILD | leaky AVOID (long) | causal BUILD | causal AVOID (long) |
| --- | --- | --- | --- | --- |
| 1d | 6 | 0 (0) | 0 | 2 (2) |
| 4h | 8 | 11 (8) | 2 | 8 (8) |
| 1h | 6 | 11 (8) | 0 | 9 (9) |
| **backtest** | **20** | **22 (16)** | **2** | **19 (19)** |
| live | 0 | 7 (2) | 0 | 1 (0) |

On the leaky tagger every short-side BUILD at 1d is a "below" or "low" state, and every
short-side AVOID at 4h and 1h is an "above" or "high" state. The long-side AVOIDs at 4h
and 1h include `below/long` on `vwap_weekly`, `vwap_monthly` and `monday_range`. That is
the filed two-sided continuation picture.

On the causal tagger the backtest AVOID cells are:

- **1d:** `vwap_monthly/above/long` −0.438R and `monday_range/above/long` −0.557R.
- **4h:** `vwap_weekly`, `vwap_monthly` and `vp_value_area` above/long; `bb_pctb/high`,
  `ema_stack/bullish` and `ema_stack/mixed`; `pa_char/grind_up`; `bb_squeeze/no_squeeze`.
  All long, −0.208R to −0.449R.
- **1h:** the same eight plus `monday_range/above/long`. All long, −0.200R to −0.426R.

Every one is long-side. Every price-location cell among them is an "above" state, and the
rest are its correlates (upper band, bullish stack, grinding up) or base-rate splits
(`ema_stack/mixed`, `bb_squeeze/no_squeeze`). With the 4h sensitivity leg removing
`bos` and `liquidity_sweep`, only `vwap_weekly/above/long` survives (−0.241R, lift −0.237R
on [−0.353, −0.125]).

H8's restraint points apply unchanged and bind harder. The axes are collinear, so this is
about one effective finding, not 19. It is in-sample on 2025-09-12 to 2026-08-17. And it
reads the same way as the standing "counter-trend book" result (HTF agreement inverted), so
it is not new information. On the causal tagger these are no longer mirror pairs: the
`below/long` twin of each AVOID is INSUFFICIENT, because longs below the reference are
merely flat (−0.045R at 4h), not reliably positive.

## How the fix was checked

- `tests/test_indicator_condition.py::test_in_progress_bars_are_invisible_and_closed_bars_count`
  runs at a day-boundary entry and a mid-day entry.
  - Wild values on the forming 1d bar and on the 1h bar opening at `t` must move no axis.
  - The same kind of value on the last closed 1h bar must flip `vwap_weekly`, and on the
    last closed 1d bar must flip `ema_stack`.
  - It fails on the old `open_time <= t` filter, on a 1h-only leak, and on a reference
    price taken from the last closed 1d close. The old test passed all three.
- `tests/test_indicator_condition_audit.py::test_live_entry_is_the_signal_candle_close` pins
  the live as-of at `candle_ts_ms + tf`. The scanner stores `e.open_time` as `candle_ts_ms`,
  and the outcome resolver walks bars after it, so the candle close is the alert's entry,
  the same instant the backtest's `entry_time` (the bar after the signal bar) denotes.
- Tag agreement, leaky vs causal, at backtest 4h: `vwap_weekly` 79.8% where both tag, and
  243 rows that now have no tag, because a Monday 00:00 entry has no closed 1h bar in the
  week yet. Other axes: `pa_char` 78.4%, `monday_range` 82.6%, `bb_pctb` 83.8%, `regime`
  88.7%, `ema_stack` 97.5%. Live 4h is similar (`vwap_weekly` 83.6%).
- The filed `below/short` cell fell with entry hour, from +0.952R at 00h to +0.123R at 20h,
  which is the shape a leak that shrinks with less of the day remaining would leave. On the
  causal tagger 00h is still the highest bucket (+0.529R, n 212), but the rest run −0.013R
  to +0.268R with no monotone fall.
- An ad-hoc causal re-tag made in another session before the fix read `below/short` +0.160R
  on n 2,337 and `above/short` +0.037R on n 1,339 at backtest 4h, with 77.4% agreement. The
  tracked re-run reproduces those counts and means exactly. 77.4% is the same agreement with
  the 243 untagged rows counted as disagreements. There is no material difference.

## What this does not re-run

- **ST63's occurrence dump** (`docs/audits/2026-08-24-st63-null-of-the-maximum.md` and its
  power-pricing sibling) tagged fires with the same leaky tagger. It closed as no actionable
  cell, and a leak can only add spurious separation, so the fix pushes in the direction of
  that verdict. It is not re-run here.
- **The 15m tier** stays deferred, as in H8.
- **Pre-fix `bos` / `liquidity_sweep` rows** remain in the pool (#949). They carry both 4h
  BUILD cells on the causal tagger, and removing them leaves one AVOID cell at 4h.

## Reproduce

Against a snapshot, one tier at a time:

```bash
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 4h
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 4h --until 2026-08-13
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 4h --exclude-strategies bos liquidity_sweep
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source live
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source live --until 2026-08-13
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source live --timeframes 4h
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 1d
PYTHONPATH=. poetry run python tools/indicator_condition_audit.py --db <snapshot> --source backtest --timeframes 1h
```

For the leaky baseline, run the same commands from a `git archive` of the commit before the
fix for #952.
