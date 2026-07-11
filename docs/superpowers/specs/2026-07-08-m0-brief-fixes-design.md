# M0 Brief Fixes — sweep-flag correctness, fresh as-of price, legend

Date: 2026-07-08 · Status: user-approved 2026-07-08 (window-table
wording clarified during review); implementation plan:
`docs/superpowers/plans/2026-07-08-m0-brief-fixes.md`
Scope: milestone M0 of the Brief-v2 roadmap agreed 2026-07-08
(M0 fixes → M1 indicator-state layer → M2 session layer → M3 external
context → M4 Brief-v2 integration/F2 inputs → M5 Stats rework).
This spec covers M0 only.

## Problems

1. **False "swept✓" on reference levels.** `analytics/brief/levels.py`
   passes the last 3 *completed daily* bars to
   `reference_levels.sweep_flag` with the last completed bar as entry.
   PDH *is* yesterday's high, so yesterday's close is below it almost by
   definition, and any 3-day lower-high sequence flags PDH "swept"
   without today's price ever touching it (mirror for PDL in
   higher-low sequences). Root cause: the window overlaps the bars that
   *define* the level, and today's action — the only window where a
   sweep of today's level is meaningful — is never consulted. The
   borrowed semantics are correct in the proximity audit (levels are
   computed per-entry-bar there) and remain untouched at the source.
2. **Stale reference price.** `bundle.py` uses the last *completed* 1d
   close, which on a perp equals today's daily open — every ATR
   distance on the card can be up to 24 h stale.
3. **Unexplained metrics.** Regime 1d/4h, ATR14, level abbreviations,
   zone notation, and seasonality fields render with no explanation.

## Goals / Non-goals

Goals: correct sweep flags, a fresh as-of price with visible basis, a
legend explaining every card element.

Non-goals (parked in later milestones): session recap (M2), indicator
states / range / Monday-range / candle patterns (M1), chart-image and
X-timeline ingestion (M3 — X timelines deliberately deferred
2026-07-08, manual URL pasting stays), layout redesign and copy
humanize (M4), Stats tab (M5).

## Design

### 1. Sweep semantics — forming-bar windows

New brief-local helper in `analytics/brief/levels.py` (name e.g.
`_swept_current_period`); `analytics/reference_levels.py::sweep_flag`
is not modified. A level is swept when *current-period* bars pierced it
and the as-of price is back on the original side.

Two distinct time ranges matter, and the fix hinges on keeping them
apart. A level is **defined by** a past period (PDH = *yesterday's*
high — definitions unchanged, from `reference_levels.py`) but is
**active as a reference** during the *current* period (today's chart
is where traders watch PDH). Only bars from the active period can
sweep a level; the bar(s) that define it can only ever *touch* it
(yesterday's high equals PDH by construction — touching, never
piercing). The shipped bug came from scanning defining-era completed
bars instead of the active period.

Windows are sliced from `daily_df` (already fetched, includes the
forming bar) using `as_of_ms`:

| Level | Defined by (unchanged) | Sweep window (daily bars incl. forming) |
| --- | --- | --- |
| PDH / PDL | yesterday's high/low | today's bar only |
| PWH / PWL | last week's high/low | this week's bars (Mon 00:00 UTC onward) |
| MonH / MonL | this week's Monday high/low | this week's bars strictly after Monday (Tue onward) |

Monday is excluded from the MonH/MonL window for the same reason
yesterday is excluded from PDH's: a bar cannot sweep the level it
defines (its own high *is* MonH). Keeping the exclusion explicit makes
the rule uniform across all six levels rather than leaning on the
strict `>` to reject the touch-equality case.

Swept condition (strict inequalities):

- high-type level (PDH/PWH/MonH): `max(window.high) > level` **and**
  `as_of_price < level`
- low-type level (PDL/PWL/MonL): `min(window.low) < level` **and**
  `as_of_price > level`

`as_of_price` is the section-2 reference price. Empty window →
`False`. Price currently beyond the level (breakout in progress) →
`False`.

### 2. As-of reference price

`bundle.py` additionally fetches 1h OHLCV (existing `get_ohlcv` +
`completed_bars`). Reference price fallback chain:

1. last completed 1h close, if ≤ 2 h behind `as_of_ms`;
2. else the forming 1d bar's close (latest synced price);
3. else the last completed 1d close (current behavior).

Fallbacks 2 and 3 append a health note. `SymbolPanel` gains an additive
`ref_price_source: str` field (`"1h"` / `"1d_forming"` / `"1d_close"`),
flowing through `bundle_to_dict` → API → UI. `ref_close` /
`ref_close_ts_ms` keep their names; their value becomes the chosen
reference bar's close/timestamp.

All `dist_atr` values (levels and zones) are measured from the new
price. The ATR14(1d) yardstick is unchanged. Renderer label changes
from `Close 67,412` to `Last 67,412 (1h · 09:00 UTC)` in both the CLI
markdown and the Svelte panel, so the price basis is visible instead of
silent.

### 3. Legend card (UI only)

`Brief.svelte` gets an ⓘ toggle expanding a static legend card:

- Regime values — `trend` / `range` / `high_vol` / `unknown` from
  `analytics/regime.py`, the same classifier that soft-gates live
  signals;
- ATR14(1d) — the yardstick: every ± number on the card is a distance
  in daily ATRs from the as-of price;
- level abbreviations — PDH/PDL, PWH/PWL, MonH/MonL, DO/WO/MO;
- swept — pierced this period and price back on the original side;
- zone notation — TF + FVG/OB/BOS/EQH-EQL + direction, distance in
  ATRs or `inside`;
- seasonality strip and pundit-board columns.

No API change for the legend; content is UI-side static.

## Testing

TDD (superpowers:test-driven-development). New unit tests:

- regression case — 3-day lower-high downtrend must NOT flag PDH swept
  (reproduces the shipped bug);
- genuine pierce-and-reclaim today → swept; price still beyond level →
  not swept;
- MonH excludes Monday's own bar; PWH window excludes last week;
- ref-price fallback chain (fresh 1h / stale 1h → forming 1d / no 1h
  and no forming bar → completed 1d) + health notes;
- existing brief tests updated for the new label and distances
  (intentional behavioral change, confined to brief surfaces).

Definition of Done: `make lint-py` · `make typecheck` · `make test` ·
`make test-regression` (goldens must not move — brief is not in the
regression pipeline).

## Rollout

Single branch/PR (`docs/m0-brief-fixes` — spec, plan, and code ride
together). Read-only analytics change + UI addition; no DB, schema,
config, or golden change.
