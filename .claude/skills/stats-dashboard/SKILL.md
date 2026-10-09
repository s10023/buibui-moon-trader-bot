---
name: stats-dashboard
description: >
  Stats page architecture: card inventory, adding new stat cards, timezone /
  caching constraints, and the `analytics/stats/` ↔ router ↔ Svelte UI flow.
  Invoke when the user says "/stats-dashboard", touches `analytics/stats/`,
  `analytics/stats_lib.py`, `web/api/routers/stats.py`,
  `web/api/routers/live_outcomes.py`, `Stats.svelte`, or any P1/P2, ADR, DOW,
  session, path-cone, live-outcome or weekly-timing data — even for small fixes.
allowed-tools: "*"
---

# Stats Dashboard Skill

Use when working on the Stats page or its backend — adding new stat cards, fixing data, changing layout, or debugging the API.

---

## Architecture

```text
analytics/stats/                 ← pure computation, ONE module per stat (returns StatsBundle)
analytics/stats_lib.py           ← 8-line legacy import shim (`from analytics.stats import *`)
web/api/routers/stats.py         ← GET /api/stats/{symbol}?days=180 (cached in stats_cache table)
web/api/routers/live_outcomes.py ← GET /api/live-outcomes — its OWN endpoint, never in StatsBundle
web/api/models/                  ← Pydantic response models
web/ui/src/pages/Stats.svelte    ← 11-card grid UI
web/ui/src/components/           ← PathCone · WeeklyCone · LiveOutcomes
web/ui/src/api.ts                ← getStats(symbol, days) / getLiveOutcomes(days, minN, symbol)
```

**`stats_lib.py` is a shim, not the implementation.** Edit the per-stat module under
`analytics/stats/` and export it from `analytics/stats/__init__.py`; the shim needs no
change. Callers importing `analytics.stats_lib` keep working either way.

## The 11 Cards

### Cached in StatsBundle (`compute_all` → `stats_cache` table) — 9 cards

| Card | `analytics.stats` fn | Data key | Notes |
| ------ | ------------- | ---------- | ------- |
| P1/P2 Daily | `compute_p1p2_daily` | `p1p2` | overall + per-DOW bars; `p1_strong_pct` = fraction where P1-direction wick < 20% range; Low First = green, High First = red |
| Average Daily Range | `compute_adr` | `adr` | ADR(14), ADR(30), today_range_pct, today_consumed_pct (÷ ADR14), today_move_up |
| Hourly Extreme Distribution | `compute_hourly_extremes` | `hourly_extremes` | 24 bars, MYT; `peak_high/low_hour_by_dow` per-DOW MODE |
| Day-of-Week Patterns | `compute_dow_patterns` | `dow_patterns` | avg_range_pct, bull_pct, avg_return_pct, `strong_high_pct`/`strong_low_pct` (Str H/L = rejection wick < 20% range) per DOW |
| Session Breakdown | `compute_session_breakdown` | `sessions` | Asia (08–13 MYT)/London (14–21)/NY (20–03); 04–07 dead zone; London/NY overlap double-counted |
| Weekly P1/P2 | `compute_weekly_p1p2` | `weekly_p1p2` | raw DOW distribution (not cumulative); use P2 Timing for "is extreme in yet?" |
| Weekly P2 Timing | `compute_weekly_p2_timing` + `compute_weekly_flip_risk_conditioned` | `weekly_p2_timing` + `weekly_flip_risk_conditioned` | All: unconditional still-ahead % + flip risk; Bullish/Bearish P1 toggle: P(P2 still ahead \| p1_direction, DOW); live "This week" banner |
| Daily Path Cone | `compute_path_cone(conn, symbol)` | `path_cone` | all-history conditional cone: ADR-normalized hourly paths × (direction × weekday) percentile bands + timing/excursion/pivot percentiles; ignores `days` |
| Weekly Path Cone | `compute_weekly_cone(conn, symbol)` | `weekly_cone` | weekly analogue: Mon 00:00 → Sun 23:00 UTC hourly paths normalized by that week's OWN trailing AWR14, × 3 directions (all/bull/bear); weekday is the x-axis, NOT a conditioning cell. ⚠ **Conditional on OUTCOME, not a forecast** — a "bull week" is defined by its close, so the bull cone sits above the unconditional one by construction and that separation carries no predictive information. Ignores `days`; never raises on thin data (empty combos) |

### Live — never cached (injected after cache hit via `_inject_live_fields()`)

Four live fields, and **only one of them is a card of its own** — the other three ride
inside a cached card. Every one is wrapped in its own `try/except: pass`, so a live field
that raises silently disappears rather than failing the stats response.

| Field | `analytics.stats` fn | Renders as |
| ------ | ------------- | ------- |
| P1 Wick Rank | `compute_weekly_wick_percentile(conn, symbol, adr_14, days)` | **its own card** — current week's P1 wick exceedance vs historical P1 wicks; "P1 not yet set" when only one weekly extreme has formed |
| Today Path overlay | `compute_today_path(conn, symbol)` | overlay on the **Daily Path Cone** card (×ADR14) |
| Current Week Path overlay | `compute_current_week_path(conn, symbol)` | overlay on the **Weekly Path Cone** card (×AWR14) |
| Weekly Current State | `compute_weekly_current_state(conn, symbol, adr_14, days)` | banner row inside the **Weekly P2 Timing** card: current DOW, move% from weekly open, distance bucket, conditioned low/high-still-ahead probabilities |

### Live Alert Outcomes — its own endpoint, not the stats bundle at all

| Card | Path | Notes |
| ------ | ------------- | ------- |
| Live Alert Outcomes | `GET /api/live-outcomes` → `compute_live_outcomes(conn, days, min_n, symbol)` | Reads the live `signal_alert_outcomes` ledger: roll-up (total/resolved/open, win/loss/expired), per-(strategy, tf, direction) win-rate + avg-R cells, per-strategy roll-up. **Aggregates across ALL symbols by default**, which is why it is neither cached nor in `StatsBundle`. Rendered by `components/LiveOutcomes.svelte`, which fetches for itself — `Stats.svelte` calls only `getStats`. A companion `GET /api/live-outcomes/open` serves open positions. Empty ledger is a valid state (zero roll-up, never raises) |

⚠ **Era rule.** Live Alert Outcomes pools every alert in its `days` window across
whatever signal-path rule changes fell inside it, and the card prints no era line.
Before quoting one of its win rates or avg-R cells as a measurement of the current
book, run `make buibui-portfolio-replay`, which prints the ledger-scope era check
(`analytics.eras`, keyed on fire time), or name the window. The cached bundle cards
describe price, not system performance, so the rule does not apply to them.

**Why split three ways?** The cached bundle is safe to serve stale for a day. The live
fields must reflect the current candle's position, so they bypass the cache. Live Alert
Outcomes is cross-symbol, so it does not fit a bundle keyed by `(symbol, days, date)` at
all. ⚠ **Do not "tidy" it into `compute_all`** — that would key a cross-symbol ledger
read to one symbol's cache row.

## Key constraints

- **Timezone**: DuckDB on this host requires `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP` for MYT — NOT `AT TIME ZONE`.
- **Empty data**: the cached `compute_*` fns raise `ValueError` on empty data and the router maps that to 404, not 500. The cone fns are the exception — they return empty combos rather than raising.
- **Caching**: cached in `stats_cache` table keyed by `(symbol, days, date)`. Live fields never enter the cache.
- **Sessions**: London (14–21 MYT) and NY (20–03 MYT) overlap — one candle can count in both.
- **DOW order**: `["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]` — enforce this in Svelte, not API.
- **Str H/L definition**: Str H = large upper wick (rejection), Str L = large lower wick (rejection). NOT continuation (closed near extreme). `strong_high_pct` = fraction of days where upper wick < 20% of range.

## Adding a new stat card

Decide: is the card **cacheable** (uses only historical OHLCV, same answer all day) or **live** (depends on today's candle position)?

**Cacheable card:**

1. Add `analytics/stats/<name>.py` with `compute_<name>(conn, symbol, days)` → typed dataclass
2. Export both from `analytics/stats/__init__.py` (the `stats_lib.py` shim re-exports `*`)
3. Add to `StatsBundle` + call from `compute_all` in `analytics/stats/bundle.py`
4. Add field to `StatsResponse` in the API model
5. Add TypeScript type to `StatsResponse` in `web/ui/src/api.ts`
6. Add `<div class="card">` block in `Stats.svelte`
7. Add test in `tests/` using `duckdb.connect(":memory:")`

**Live field:**

1. Steps 1–2 above, with the live signature `compute_<name>(conn, symbol, adr_14, days)`
2. Add to `_inject_live_fields()` in `web/api/routers/stats.py` — called after cache hit AND
   after a miss-recompute, each field in its own `try/except: pass`
3. Everything else same as above (steps 4–7). It only needs its own card if it does not ride
   inside a cached one — three of the four current live fields do not.

**Cross-symbol card** (like Live Alert Outcomes): give it its own router + models module and
have the Svelte component fetch for itself. It does not belong in `StatsBundle`, whose cache
key is `(symbol, days, date)`.

## Checks after any change

```bash
make lint-py
make typecheck
make test
# For UI changes: make web-build
```

## UI style rules

Load `/frontend-design` before any CSS/layout changes.

- Card title: `font-size: 11px; text-transform: uppercase; color: var(--accent)`
- Accent values: `color: var(--accent)`. Green/red: `var(--green)` / `var(--red)`
- Help button: small `?` circle, toggles inline `help-panel` below card title
- Wide cards (full row): add class `card-wide` (`grid-column: 1 / -1`)
