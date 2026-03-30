# Stats Dashboard Skill

Use when working on the Stats page or its backend — adding new stat cards, fixing data, changing layout, or debugging the API.

---

## Architecture

```
analytics/stats_lib.py          ← pure computation (DuckDB queries, returns StatsBundle)
web/api/routers/stats.py        ← GET /api/stats/{symbol}?days=180 (cached in stats_cache table)
web/api/models/                 ← Pydantic response models (if any)
web/ui/src/pages/Stats.svelte   ← 6-card grid UI
web/ui/src/api.ts               ← getStats(symbol, days) typed client
```

## The 6 Cards

| Card | stats_lib fn | Data key | Notes |
|------|-------------|----------|-------|
| P1/P2 Daily | `compute_p1p2_daily` | `p1p2` | overall + per-DOW bars |
| Average Daily Range | `compute_adr` | `adr` | ADR(14), ADR(30), today_range_pct, today_consumed_pct |
| Hourly Extreme Distribution | `compute_hourly_extremes` | `hourly_extremes` | 24 bars, MYT, green=high, red=low |
| Day-of-Week Patterns | `compute_dow_patterns` | `dow_patterns` | avg_range_pct, bull_pct, sample_days |
| Session Breakdown | `compute_session_breakdown` | `sessions` | Asia/London/NY high_pct + low_pct; MYT windows |
| Weekly P1/P2 | `compute_weekly_p1p2` | `weekly_p1p2` | overall + high_day + low_day |

All wrapped by `compute_all(conn, symbol, days)` → `StatsBundle`.

## Key constraints

- **Timezone**: DuckDB on this host requires `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP` for MYT — NOT `AT TIME ZONE`.
- **Empty data**: `stats_lib.py` raises `ValueError` on empty data; API must return 422/404 not 500.
- **Caching**: results cached in `stats_cache` table keyed by `(symbol, days, date)`.
- **Sessions**: London (14–21 MYT) and NY (20–03 MYT) overlap — one candle can count in both.
- **DOW order**: `["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]` — enforce this in Svelte, not API.

## Adding a new stat card

1. **Backend**: Add a `compute_<name>(conn, df)` function in `analytics/stats_lib.py`. Return a typed dataclass. Add to `StatsBundle` and call from `compute_all`.
2. **API**: Add the new field to `StatsResponse` (or create a sub-model). Router picks it up automatically via `compute_all`.
3. **Types**: Add the TypeScript type in `web/ui/src/api.ts` under `StatsResponse`.
4. **UI**: Add a `<div class="card">` (or `card-wide`) block in `Stats.svelte`. Follow the existing help-panel pattern (`CARD_HELP` dict + `toggleHelp`).
5. **Tests**: Add a test in `tests/` using `duckdb.connect(":memory:")`.

## Checks after any change

```bash
make lint-py
make typecheck
make test
# For UI changes: make web-build
```

## UI style rules

- Dark, minimal — no clutter. Load `/frontend-design` skill before any CSS/layout changes.
- Card title: `font-size: 11px; text-transform: uppercase; color: var(--accent)`.
- Accent values: `color: var(--accent)`. Green/red: `var(--green)` / `var(--red)`.
- Help button: small `?` circle, toggles inline `help-panel` below card title.
- Wide cards (full row): add class `card-wide` (`grid-column: 1 / -1`).
