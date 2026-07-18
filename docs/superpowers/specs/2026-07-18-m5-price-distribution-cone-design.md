# M5 Stats Rework — Daily Price-Distribution Cone (design)

**Date:** 2026-07-18 · **Status:** user-approved design (brainstorm session) ·
**Milestone:** Brief-v2 M5 (last open milestone) · **Supersedes:** the parked
`price-distribution-feature` memory (2026-06-30 brainstorm, paused)

## 1. Context and goal

The Stats tab's daily analytics are scalar aggregates; the operator called it
"a beginner version… not entirely correct / not what I expect to see" against
the DieguitoCharts/BrighterData candle-outcome tool. The only distribution-ish
card, Daily Distance, is a 1-D CDF of today's range *magnitude* — it answers
"how big is today" but not "what does a typical day's *path* look like from
here".

**Goal:** replace that gap with a conditional daily path-distribution cone —
historical intraday paths normalized and pooled into percentile bands, sliced
by direction × weekday, with live today-vs-template overlay, timing
distributions, excursion stats, and price-mapped projection pivots.

**Framing:** descriptive/discretionary operator tool (like the rest of the
Stats tab), NOT a traded edge. Does not compete with the XS-solo deploy core.

## 2. Scope decisions (brainstorm ledger, all user-approved 2026-07-18)

| Decision | Choice |
| -------- | ------ |
| M5 decomposition | **Cone-first.** This spec = the cone unit only. Live Alert Outcomes UX and the External-block presentation (M4 follow-on) become separate small specs later. |
| v1 components | **All four:** path cone · low/high-timing + excursion distributions · projection pivots · live today-overlay. One shared substrate makes the last three cheap views over the first. |
| Conditioning | **Direction × weekday**, user-selectable chips: All/Bull/Bear × All/Mon…Sun (24 combos), sample count shown, thin-sample warning below n=30. |
| Normalization | **ADR-relative.** Each historical day's path in ×ADR units of its *own* trailing ADR14; display re-scaled by today's ADR14 (% + price equivalents). |
| Day boundary | **UTC day, MYT axis labels.** Period = the Binance UTC daily candle (matches chart, DOW stats, XS book); x-axis hour labels rendered in MYT (UTC+8, no DST). |
| Suspect-card fate | **Daily Distance removed** in the same PR (strictly superseded). **P1 Wick Rank kept** — weekly-scoped; redesign deferred to the weekly cone re-parametrization. |
| Placement | **Hero section** at the top of the Stats page, above the existing card grid. No new page or nav. |
| Architecture | **Server-side percentiles, all 24 combos precomputed** into the cached `StatsBundle` (~20 KB); chip switches are pure client-side. Cone rendered as **plain SVG** in Svelte (no lightweight-charts band hack). |

## 3. Component architecture

```text
analytics/stats/path_cone.py     ← NEW pure compute (all correctness lives here)
analytics/stats/bundle.py        ← PathConeBundle joins StatsBundle.compute_all
analytics/stats/daily_distance.py← DELETED (superseded)
web/api/routers/stats.py         ← cached block + live overlay in _inject_live_fields()
web/api/models/stats.py          ← PathConeResponse added; DailyDistanceResponse removed
web/ui/src/api.ts                ← TS types updated to match
web/ui/src/pages/Stats.svelte    ← hero section (SVG cone + chips + pivots + timing)
```

- `path_cone.py` takes a DuckDB conn + symbol, returns one typed
  `PathConeBundle` dataclass. No I/O beyond the conn, no module-level side
  effects — same contract as every other `analytics/stats/` module.
- The cached bundle carries everything deterministic per (symbol, date). The
  live today-overlay is computed fresh per request in `_inject_live_fields()`,
  never cached — the exact pattern of the Daily Distance card it replaces.

## 4. Compute methodology

### 4.1 Substrate

Input: **all available 1h history** for the symbol (the page's `days`
selector does NOT scope the cone — conditioning needs the depth; the UI
shows the population size). Bars group into UTC days.

Per historical day `d`:

- `adr14_d` = mean of `(high − low) / open` over the 14 completed UTC days
  strictly before `d`. If fewer than 14 complete prior days exist, or
  `adr14_d <= 0`, day `d` is excluded. A day never normalizes by a window
  containing itself (look-ahead-safe; enforced by a perturbation test).
- Path point at elapsed hour `h` (1…24):
  `norm_h = ((close_h − open_d) / open_d) / adr14_d` — signed, ×ADR units.
  Step 0 ≡ 0 at the open by construction.
- Direction label: sign of `close_d − open_d` (last hourly close vs first
  hourly open). Exact-doji days (`close == open`) count only in "All".
- Weekday label: UTC weekday of the day's open (displayed Mon…Sun).
- Population membership requires all 24 hourly bars present (gap days
  dropped). The current forming UTC day is always excluded.

### 4.2 Cone

For each of the 24 combos (direction ∈ {all, bull, bear} × weekday ∈
{all, mon…sun}): per-step percentiles **p10/p25/p50/p75/p90** of `norm_h`
(linear interpolation, numpy default), plus the population size `n`.

### 4.3 Timing distributions

Per combo, indexed `h = 1…24`:

- `low_in_by[h]` = fraction of days whose eventual daily low was set at or
  before hour `h` (the hour whose bar low equals the day low; ties → the
  earliest such hour). `high_in_by[h]` symmetric.
- Live read: at elapsed hour `h_now`, render
  "low in {low_in_by[h_now]} · high in {high_in_by[h_now]}".

### 4.4 Excursion stats

Per combo: percentiles p10/p50/p90 of each day's **max adverse excursion**
(`min_h norm_h`) and **max favorable excursion** (`max_h norm_h`), in ×ADR.
Read: "the median bull day dips −0.35 ADR before closing up".

### 4.5 Projection pivots

Per combo: percentiles **p50/p80** of the day's excursion *magnitudes*
(both defined ≥ 0 so p80 always means "more extreme than p50"):

- high magnitude: `(day_high − open_d) / open_d / adr14_d`
- low magnitude: `(open_d − day_low) / open_d / adr14_d`

Mapped to price at render time:

```text
high_pivot(q) = today_open × (1 + q × adr14_today)
low_pivot(q)  = today_open × (1 − q × adr14_today)
```

where `adr14_today` is the same trailing-14 computation ending yesterday and
`today_open` is today's UTC-day open. Pivots are the actionable target
levels; the close-cone's right edge stays purely visual.

### 4.6 Live overlay (never cached)

Today's hourly closes so far — including the forming bar at its latest
close — normalized by `adr14_today`. `elapsed_h` = the count of *completed*
hourly bars today (0–24); completed bars plot at steps `1…elapsed_h` and the
forming bar's latest close plots at step `elapsed_h + 1`. The timing line
reads `low_in_by[elapsed_h]` / `high_in_by[elapsed_h]` and is hidden when
`elapsed_h = 0` (day just opened).
Payload: `today_path` (list of ×ADR points), `elapsed_h`, `adr14_today`,
`today_open`. The client draws it as a dotted path on whichever combo cone
is selected. If `adr14_today` is unavailable (new listing) the overlay and
pivots degrade to "insufficient"; the historical cone still renders.

## 5. API and caching

- `StatsResponse` gains a `path_cone` block: 24 combo entries (each:
  24×5 percentile matrix, `n`, `low_in_by`, `high_in_by`, excursion trios,
  pivot percentiles). Deterministic per (symbol, date) → rides the existing
  `stats_cache` keyed on (symbol, days, date) unchanged. The block is
  identical across `days` keys (cone ignores `days`); the redundant storage
  (~20 KB per key) is accepted for cache-layer simplicity.
- `today_path` is injected in `_inject_live_fields()` alongside the existing
  live cards.
- `daily_distance` is removed from `StatsResponse` and the TS types in the
  same PR.
- Error contract: symbol with no OHLCV at all → 404/422 exactly as today.
  Short history → HTTP 200 with per-combo `n` low or zero; combos at `n=0`
  are marked insufficient. No 500s on empty data (house rule).

## 6. UI

Hero section at the top of `Stats.svelte`, full-width above the card grid:

- **SVG cone** (viewBox-responsive, no chart lib): p10–p90 outer band fill,
  p25–p75 inner band fill, p50 median line, dotted today-path with a dot at
  the current hour. Y-axis in ×ADR with a % equivalence caption
  (`1 ADR ≈ x.x%` from `adr14_today`); x-axis labels in MYT.
- **Chips:** direction [All | Bull | Bear] and weekday [All | Mon…Sun].
  Switching is pure client-side (all combos already loaded). `n` badge next
  to the chips; amber thin-sample treatment below n=30.
- **Pivots panel:** the price-mapped high/low p50/p80 levels for the selected
  combo, as price + %.
- **Timing line** below the cone: the `low_in_by`/`high_in_by` read at the
  current elapsed hour.
- Reuses the existing card-help ⓘ pattern (What / Value / e.g.). Dark
  minimal house style; `/frontend-design` loaded before implementation.

## 7. Retirement and migration

- `compute_daily_distance`, `DailyDistanceResult`, `DailyDistanceResponse`,
  and the Daily Distance card: deleted. No data migration (never persisted).
- P1 Wick Rank: untouched. Recorded here: **redesign at the weekly cone
  re-parametrization** (weekly period = same substrate re-parametrized;
  out of scope for v1).

## 8. Error handling summary

| Condition | Behavior |
| --------- | -------- |
| Day missing any of its 24 hourly bars | Day dropped from population |
| `adr14_d` unavailable or ≤ 0 | Day dropped |
| Combo `n = 0` | Combo marked insufficient; UI renders "insufficient data" |
| `adr14_today` unavailable | Overlay + pivots insufficient; historical cone still renders |
| Symbol has no OHLCV | 404/422 as today (no 500) |

## 9. Testing

In-memory DuckDB (`duckdb.connect(":memory:")`) with synthetic 1h OHLCV:

- Hand-computed percentile assertions on a tiny known population.
- **Causality perturbation test:** inflating day `d`'s range must not change
  day `d`'s own normalized path — only later days' (via their ADR windows).
- Tie convention (earliest extreme hour), doji exclusion from bull/bear,
  incomplete-day exclusion, forming-day exclusion.
- Pivot price mapping round-trip against hand-computed values.
- API: cache round-trip (cached block byte-stable across requests same day);
  live overlay changes with a new forming bar; removed `daily_distance`
  field absent.
- No regression-golden impact (stats are not in the backtest goldens).
- Gate: `make lint-py` · `make typecheck` · `make test` all green.

## 10. Out of scope / follow-ups

1. **Live Alert Outcomes UX** (sortable columns, per-ticker drill-down,
   open-positions hover/click) — separate small spec.
2. **External-block presentation** (price-ladder view / annotated archived
   screenshot; M4 follow-on) — separate small spec, UI-only.
3. **Weekly re-parametrization** of the cone (+ P1 Wick Rank redesign).
4. Cone-derived research state-tags (e.g. "today outside p90 by hour 6") —
   route through the hypothesis inbox if raised; not part of this operator
   tool.

## 11. Effort estimate

~4–5 working days via sonnet SDD: compute ~1.5–2 d · API ~0.5 d ·
UI ~1.5–2 d — consistent with the original 4–7 d estimate, minus the
methodology risk this spec retires.
