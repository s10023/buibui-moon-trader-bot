# The audit bootstrap priced correlated trades as independent draws

**Date:** 2026-08-25

**Subject:** `analytics/audit_guard.py::evaluate_audit_cells` and its eleven consumers

**Status:** measured, then fixed in the same branch. No config changed, no rating moved, no
filed verdict moves.

## Verdict

**FOUND, and it is worse than the filed framing.** Both legs of every audit verdict were priced
on the trade count. The significance leg formed `t = sr·√n_trades` on a trade-weighted design
effect of **4.991**, and the CI came from a block bootstrap that could not reach the dependence
at all — not because the block length was too short, but because **no trade query feeding an
`AuditCell` carries an `ORDER BY`**, so the array it resampled runs of was in DuckDB storage
order. A guard whose docstring claimed serial-correlation awareness was ordering on nothing.

Undeflated, this repo's own panel reads **70 of 125 cells significant where 52 survive**. The
fix makes the dependence unit a **required** per-observation `cluster_key` that fails closed,
and prices both legs on it.

**One clean negative makes this safe to land: `powered_null` holds on 0 of 125 cells before the
correction and 0 of 125 after, so no filed powered-null verdict moves.** The live damage was
always on the `ENABLE`/`DISABLE` branch. The equities fork reached the same negative
independently on its own panel (0 of 64), which is worth one line: two panels, two cell shapes,
same answer.

## What was wrong

`evaluate_audit_cells` had two legs and both assumed independent draws:

| leg | before | after |
| --- | --- | --- |
| effect size | `block_bootstrap_ci` over trades | `cluster_bootstrap_ci` over clusters |
| significance | `t = sr·√n_trades` | `t = sr·√n_eff`, `n_eff = n / DEFF` |

A block bootstrap absorbs **serial** dependence — it resamples runs of observations adjacent
*in the array it is handed*. Two separate things defeated it here:

1. **Cross-sectional dependence is not adjacency.** Three symbols firing on the same UTC day are
   three rows scattered through the array, and no block length reaches them.
2. **The adjacency was meaningless anyway.** Every `ORDER BY` in the eleven consumers is on an
   OHLCV or run-selection query. Not one trade query has one. `warning_audit.tag_trades` builds
   its index symbol-by-symbol, so same-day cross-symbol rows are maximally far apart by
   construction.

`AuditCell` carried `supp_r` and `kept_r` as bare lists, so there was no channel through which a
caller *could* have declared the unit even if it had wanted to.

## Measurement

125 cells cut `strategy × timeframe × direction` at `n ≥ 30`, over **166,384 distinct trades**
(855,146 raw rows deduped on `(symbol, timeframe, strategy, direction, signal_time)` — a **5.13×**
duplication factor, against `AGENTS.md`'s filed ~5.29×). Shipped defaults: `n_boot=2000`,
`alpha=0.05`, `seed=12345`.

### The design effect is heavily skewed, and the median is the wrong summary

| statistic | value |
| --- | --- |
| median DEFF (UTC day) | 1.670 |
| **trade-weighted DEFF** | **4.991** |
| max DEFF | 9.89 (`bos × 15m × short`, n=7,585) |
| median cluster-CI ÷ block-CI width | 1.251 (p25 1.130, p75 1.417, max 2.675) |
| analytic route, median `√DEFF` | 1.292 |

The two routes are independent — one is an analytic design effect, the other resamples whole
days — and they agree at the median (1.251 vs 1.292).

⚠ **Quoting the median would understate this by 3×.** The deflation concentrates in the
high-volume 15m cells, which `AGENTS.md` records as 64.4% of the live ledger. The cells that
currently carry significance are exactly the cells the correction bites. The eight largest:

| cell | n | symbols | DEFF (day) | DEFF (day × symbol) | cross-symbol share |
| --- | --- | --- | --- | --- | --- |
| `bos × 15m × short` | 7,585 | 3 | 9.89 | 4.15 | 65% |
| `inside_bar × 15m × short` | 5,397 | 3 | 9.10 | 3.82 | 65% |
| `eqh_eql × 15m × short` | 6,693 | 3 | 9.00 | 4.72 | 54% |
| `bos × 15m × long` | 7,246 | 3 | 8.65 | 3.82 | 63% |
| `eqh_eql × 15m × long` | 6,158 | 3 | 8.33 | 4.76 | 49% |
| `morning_evening_star × 15m × short` | 4,729 | 3 | 8.16 | 3.51 | 65% |
| `inside_bar × 15m × long` | 5,495 | 3 | 8.08 | 4.04 | 57% |
| `pin_bar × 15m × short` | 4,250 | 3 | 7.35 | 3.23 | 65% |

**Both channels are live and roughly evenly split** — 49–65% of the excess design effect is
same-day cross-symbol, the remainder is intraday-serial within one symbol. That matters for the
framing: the fork's finding was cross-sectional clustering alone. Here the block bootstrap was
missing the serial half too, and only because the query is unordered.

### The significance leg is the larger channel

| | significant at 0.05 (raw, pre-Holm) |
| --- | --- |
| on `n_trades` | **70 of 125** |
| on `n_eff` | **52 of 125** |

18 cells lose raw significance, several by two orders of magnitude:

| cell | n | n_eff | p (trades) | p (n_eff) |
| --- | --- | --- | --- | --- |
| `bos × 15m × short` | 7,585 | 767 | 0.00010 | 0.215 |
| `inside_bar × 15m × short` | 5,397 | 593 | 0.00162 | 0.296 |
| `morning_evening_star × 15m × short` | 4,729 | 580 | 0.00336 | 0.304 |
| `engulfing × 15m × long` | 1,994 | 534 | 0.00063 | 0.077 |
| `eqh_eql × 1h × short` | 1,071 | 293 | 0.00197 | 0.106 |
| `pin_bar × 15m × short` | 4,250 | 578 | 0.02792 | 0.418 |

### The unit does not plateau at the UTC day

The fork's `session_day_keys` docstring declares itself non-portable here — *"Do not port this to
a 24h tape. On crypto the UTC day is an arbitrary cut through a continuous session."* That is a
claim, so it was measured:

| cluster unit | median clusters | median size | median ICC | median DEFF |
| --- | --- | --- | --- | --- |
| 12h | 202 | 1.96 | 0.507 | 1.508 |
| **UTC day** | **164** | **2.61** | **0.445** | **1.670** |
| 2 days | 112 | 3.97 | 0.376 | 1.873 |
| 1 week | 37 | 9.26 | 0.213 | 2.366 |
| day × symbol | 291 | 1.49 | 0.412 | 1.185 |

**DEFF rises monotonically as the window widens and never plateaus**, so the warning is correct:
dependence keeps being found past the day, and every figure the day key produces under-corrects.

The day was chosen anyway, and the reason is stated rather than assumed: it is the only unit on
this tape with an independent anchor — the `1d` bar, `day_filter`, and the regime / ADR / DOW
context are all keyed to the UTC day. A wider window would be a free parameter picked to move a
verdict. `cluster_key` is opaque, so a consumer with a better unit can pass one.

## This is the same correction as the 2.92× deflator, not a second one

The SoT row files ST80 as *"distinct from the 2.92× cross-sectional deflator"*. That is right
about the conclusion — nothing inside `audit_guard` corrected either — and **wrong about the
mechanism**, which is the half that says where double-counting would bite.

`analytics.forecast.effective_independent_series` is `n_eff = k / (1 + (k−1)·ρ)`. A design effect
on a fully-populated cross-section is `DEFF = 1 + (k−1)·ρ`. So `n_eff = k / DEFF` is the same line
read twice. At `AGENTS.md`'s filed `ρ = 0.315`, `k = 25`:

```text
DEFF = 1 + 24 × 0.315 = 8.56        25 / 8.56 = 2.921
```

— the filed 2.92 to three digits. **Two estimators of one phenomenon, applied in different
modules. The rule is "never both in one place", which is stronger than "do not conflate them".**

The overlap buys a free safety property. `AGENTS.md`'s rule that *book-day rows are already
aggregated and must NOT be deflated again* **enforces itself** under a day key: one row per day
is a singleton cluster, so ICC is 0, DEFF is 1, and the deflation is a no-op.

**Four of the eleven consumers are that shape, so their verdicts are structurally unmovable by
this change** — a stronger statement than the 0-of-125 powered-null negative, and independent of
it. `state_audit` collapses to one row per `(day, direction)` and `weekly_path` to one per
calendar week; `premium_state_audit` (H14) and `carry_unwind_audit` (H15) both build their cells
through `state_audit.build_state_cells`, and both panels carry a `day` column
(`build_forward_panel` emits it directly, `build_ledger_panel` via `collapse_to_daily`). So H14's
and H15's filed NO-EDGE / INSUFFICIENT results cannot move under any cluster key at all — they
were already priced on the right unit, by hand, before this existed.

All four now pass their real day or week rather than a placeholder, so the property is
checkable rather than asserted, and a future change that stops collapsing would start deflating
automatically. `weekly_path` clusters on the **week** — the one consumer that correctly does not
call `utc_day_keys`, which is what shows the design generalises rather than special-casing the
day.

## The fix

`AuditCell` gains a **required** `cluster_key`, positioned before the defaulted `kept_r` so mypy
refuses a call site that omits it. **It refused ten, across nine files** — that is the mechanism
working, and all ten had a real timestamp available, so nothing had to be invented.

A mismatched key length **fails closed** to `INSUFFICIENT` and leaves the Holm family, rather
than falling back to per-trade resampling. An unmeasurable panel and an uncorrelated one must not
both read as a deflator of 1.0 — the fail-open shape this repo already closed in
`tools/distil_power.py`. A caller with genuinely ungrouped observations declares that by passing
distinct keys, which costs nothing and leaves a greppable decision where a silent default would
leave none.

`CellVerdict` now reports `n_clusters` and `design_effect`, because a deflator applied silently
is indistinguishable from one that was forgotten.

`evaluate_audit_cells` loses `boot_method`: it chose between block bootstraps and the cluster
bootstrap has no such choice. ⚠ **One caller passed it** (`weekly_path.py`) — the fork's note
that no caller did does not port.

## Bounds on the claim

Every bound runs the same way, which is the safe way for a guard: **the true design effect is
larger than measured, so a surviving verdict is conservative.**

- **The day key under-corrects**, per the unit sweep above.
- **Clusters key on ENTRY**, so a multi-day hold overlaps days it is not clustered with.
- **The pool mixes engine settings.** ST86 established that retunes overwrote rows measured under
  the previous value, and the axis was never stored, so a deduped `backtest_trades` pool spans
  settings. Mixed settings within a day add noise to the within-day correlation, which *depresses*
  measured ICC.
- **`powered_null` is 0 of 125 both ways**, so this bounds nothing about the powered-null family —
  those verdicts stand on their own evidence, unchanged.
- **Nothing here touches a sleeve gate.** Those run on a portfolio-level series with no
  cross-section to cluster.

## Reproduction

`docs/plans/scripts/st80_cluster_deff.py` (gitignored, covered by `make buibui-backup`), run as
`BUIBUI_DB=<path> PYTHONPATH=. python docs/plans/scripts/st80_cluster_deff.py`. It computes the
cluster statistics **alongside** the shipped block-bootstrap CI, never in place of it, so the
before/after pair comes from one run over one pool.
