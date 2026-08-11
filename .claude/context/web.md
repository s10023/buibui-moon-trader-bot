# Web Layer Reference

Detailed reference for `web/`. Load this when working on the FastAPI backend or Svelte frontend.

## Backend — `web/api/`

- `main.py` — app + StaticFiles mount; reads `BUIBUI_CONFIG` env var (set by `buibui web --config <toml>`); stores `app.state.config_name` + `app.state.active_config`
- `deps.py` — `require_token`, `require_token_sse` (SSE query-param auth)
- `routers/` — config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones, live_outcomes, brief
- `models/` — Pydantic models per router; `active_config.py` → `ActiveConfigResponse` + `StrategyParamsModel`; `zones.py` → `ZoneBox`, `ZoneLine`, `SwingPoint`, `ZonesResponse`; `brief.py` → `BriefResponse` + section models (levels/zones/seasonality/indicators/sessions/pundit/health — M1 added `IndicatorStateModel` + 8 sub-models mirroring `analytics/brief/types.py`; M2 added `SessionClockModel` + `SessionStateModel`/`SessionRecapRowModel`/`SessionTendencyRowModel`; M3 added `ExternalStateModel`/`ExternalSnapshotModel`/`ExternalClusterRowModel` + additive `SymbolPanelModel.external`; M5 added `WeeklyStateModel` + `MonthlyContextModel` + additive `SymbolPanelModel.weekly`/`.monthly` — the mirror is load-bearing: Pydantic v2 `extra="ignore"` silently drops unmirrored keys at the API boundary); `live_outcomes.py` → `LiveOpenPositionModel` / `LiveOpenPositionsResponse` / `LiveOutcomeSymbolModel` (+ the roll-up/cell/strategy models)

### Key endpoints

- `GET /api/brief?symbols&days&as_of` → daily market-brief bundle (`BriefResponse`): per-symbol levels/zones/regime/seasonality/indicator-state panels (additive `indicators` key, null-safe) + M2 session layer (top-level `session_clock`; additive per-panel `sessions` = last-3-completed MYT-session recap + 180d tendency, null-safe) + M3 external layer (additive per-panel `external`, null-safe — operator-verified Coinglass/MMT heatmap/liq-map snapshots from `/ingest-charts`, latest fresh per (source, venue, scope, panel, window) with `venue: str | null` mirrored through Pydantic/TS, 48h staleness, ATR distances) + pundit board + health footer; read-only via `get_db`, deterministic (`as_of` ISO8601 → byte-identical; omitted → server clock). Brief.svelte renders a header clock chip + per-card Sessions, Month, Week and External sub-sections + legend entries mirroring the markdown formats. **Those four sub-sections used to share one `.sessions muted` class; they no longer do, and must not again** — four different kinds of information rendered identically is what made the tab read as undifferentiated text, and each now owns a `Block` with its own eyebrow label. Body copy uses `--text-soft`, not `--muted`: the latter measures 2.81:1 on `--bg-panel`, below the 4.5:1 AA floor, and is kept for chrome only. Month/Week order matches the CLI (frame widens, then narrows), and `weekHourLabel()` mirrors `render.py::_weekly_lines` so the tab and the CLI cannot disagree on which moment an elapsed-hour count names — UTC axis (the week is the Monday 00:00 UTC candle) with MYT in the readout, MYT day index computed independently rather than derived. The Week block honors `conditional_is_fallback` as a flag: when set it drops the cohort clause entirely and attributes the timing stat to "all weeks", never inferring the fallback from `path_direction`
- `GET /api/zones?symbol&timeframe&start_ms&end_ms` → `ZonesResponse(boxes, lines, swings)`; `ZoneBox`/`ZoneLine` carry `close_ms: int | None`
- `GET /api/backtest/runs` / `POST /api/backtest` — `BacktestRunSummary` has `stars/long_stars/short_stars: int | None`, `long/short_total_r/recovery_factor: float | None`; validators coerce pandas NaN → None
- `GET /api/strategies?config=<name>` — confidence values with per-config DB ratings override
- `GET /api/active-config` — `config_name`, `symbols`, `timeframes`, `strategies`, `day_filter`, `tp_r`, `sl_pct`, `fee_pct`, `adr_suppress_threshold`, `strategy_params`, `min_trades`, `min_trades_per_tf`; empty defaults when no `--config`
- `GET /api/stats/{symbol}?days=180` — cached daily in `stats_cache` table; `weekly_current_state`, `today_path`, `weekly_wick_percentile` always live (never cached), injected via `_inject_live_fields()`
- `GET /api/live-outcomes?days&min_n&symbol` — cross-symbol roll-up of the live `signal_alert_outcomes` ledger (all-time integrity roll-up + per-(strategy, tf, direction) + per-strategy win-rate/avg-R, both groupings additionally carrying `wins`/`losses`/`expired` counts; the optional `symbol` param scopes the roll-up + both tables to one symbol, response always carries a `symbols` chip list — global/all-time, unaffected by the filter); never cached, own router (not the per-symbol StatsBundle). Pure DuckDB read — no `get_client` dependency, so it works offline and on the keyless OKX data path
- `GET /api/live-outcomes/open?symbol` — unresolved alerts marked to the current price (`open_positions` + pure `mark_open_positions`); best-effort mark fetch degrades to `marks_ok=false` + null price columns rather than 5xx-ing. The ONLY live-outcomes route with a `get_client` dependency — that asymmetry is deliberate, everything else in this router stays a pure DB read
- `GET /api/backtest/analysis?use_config=true` — 12 digest query cards; `use_config=true` scopes via `DigestScope`

## Frontend — `web/ui/` (Svelte 5 + Vite)

Build: `make web-build` → `web/ui/dist/` served by FastAPI StaticFiles.

### Key files

- `src/api.ts` — typed client; `getStrategies(configName?)`, `getActiveConfig()`
- `src/stores/` — config, strategies, prices SSE, positions SSE, `activeConfig.ts` (exposes `activeConfigStore`, `configName`, `configDefaultSymbol`)
- `src/pages/` — Brief, Chart, Backtest, SignalFeed, Positions, Prices, Stats
- `src/components/` — Nav, CandleChart, BacktestResult, LiveOutcomes, PathCone, WeeklyCone, …
- `src/components/brief/` — the Brief tab's cards. `Card.svelte` (shared frame) and
  `Block.svelte` (labelled sub-section) are the two primitives; `SymbolPanel.svelte`
  composes one symbol from `PriceLadder` / `ZonesRow` / `IndicatorBlock` /
  `SessionBlock` / `PeriodBlock` / `ExternalBlock` / `SeasonalityStrip`, with
  `PunditBoard`, `DataHealth`, `BriefLegend` and `ScopeBar` alongside. `format.ts`
  holds the shared formatters (they must not be duplicated per component, or the tab
  and the CLI drift on the same number); `scope.svelte.ts` holds the scope-selector
  state. **Scope is applied at render time, never by narrowing `compute_brief`** —
  `card/state.py` builds the F2 card's MarketState from that same bundle, so a
  backend scope would silently narrow what the AI card sees.

### Backtest page

- DB-backed sortable/filterable table; collapsible run form
- **"◈ \<config\>" button** — pre-fills all chips + fee_pct/tp_r/sl_pct from active TOML
- Stars per row: `stars` (combined), `long_stars` (↑★), `short_stars` (↓★) — JOINed by `(strategy, tf, day_filter, direction)`
- Columns: long/short win rate, avg R, total R (↑/↓), Max DD, RF (≥3 green / 2–3 yellow / <2 red) — all sortable
- ADR Gate column shows `adr_suppress_threshold` per row (2dp, `—` for NULL)
- Filter sections: CATEGORY (symbol/TF/strategy/day filter/ADR gate/stars), PERF (win%/trades/avg R/total R/max DD/RF), DIR (directional long+short)
- **Analysis sub-tab** — 12 lazy-loaded cards; `min_trades` input + "◈ Scope to config" toggle

### Chart page

- Watchlist sidebar; timeframe/days selectors
- **Strategies row** — 6 collapsible group toggles: Structure (bos/liquidity_sweep/eqh_eql/order_block/fvg), Fibonacci (fib_golden_zone/ote_entry), Price Action (wick_fill/marubozu/inside_bar/trend_day), Candlestick (engulfing/pin_bar/hammer_hanging_man/doji/morning_evening_star), Flow (smt_divergence/cvd_divergence/funding_reversion), Session (orb/seasonality); taxonomy in `STRATEGY_GROUPS` in `Chart.svelte`; groups absent from active TOML hidden
- **Indicators row** — EMA 20/50/200, RSI 14, **Zones** (7 toggles: FVG, OB, EQH·EQL, BOS, Fib Zone, OTE, Swings)
  - FVG/OB/Fib/OTE — HTML overlay divs; EQH/EQL/BOS — line series
  - Active zones extend to right edge; inactive end at `close_ms` (dimmed)
  - Colors: bull=`#56d364`, bear=`#f85149`, fib=`#e3b341`, ote=`#f0883e`
- **Range Levels** — MO, DO, PDH/PDL, WO, PWH/PWL, Mon H/L; solid lines from origin to right edge; HTML labels
- **CME Gap** — semi-transparent box for most recent Fri 21:00–Sun 22:00 UTC window; **15m and 1h only** (pill hidden on 4h/1d — `timeToCoordinate` returns null for inter-candle timestamps on coarser TFs)
- Time axis + crosshair: **MYT (UTC+8)** via `localization.timeFormatter`
- Signal markers; funding/OI sub-panels; Fib overlay; live candle via SSE + 30s seed refresh

### Stats page

- **Daily Path Cone** card (hero, above the grid) — `path_cone` cached direction × weekday percentile bands + `today_path` live overlay via `_inject_live_fields()`; hovering the chart draws a dashed vertical guide line and a readout strip below with that hour's p90/p75/p50/p25/p10 (rendered as prices when the symbol has a live overlay, else ADR multiples) plus today's own value at that hour — hover-only, no keyboard equivalent (deliberate non-goal)
- 9-card grid: P1/P2 (incl. P1 strong%), ADR, hourly distribution, DOW patterns (incl. Str H/Str L), session breakdown, weekly P1/P2, avg return by day, weekly P2 timing with flip risk, P1 Wick Rank
- **Live Alert Outcomes** card (full-width, below the grid) — cross-symbol, fetched independently of the symbol picker via `getLiveOutcomes(days, minN, symbol)`; all-time roll-up chips (incl. a no-TP integrity badge) + period/min-n/symbol pill toggles (symbol chips scope the whole card, each chip showing its own count, `ALL` always summing the global chip list) + by-strategy and by-cell tables with diverging avg-R bars, click-to-sort headers, and an `exp` (expired) column between `n` and `win`; each table's title carries the threshold that applies to it (`(n≥N total)` on by-strategy, `(n≥N per cell)` on by-cell), and an empty cell table (while by-strategy still has rows) shows an inline "no cell clears n≥N — lower min n" hint; footnote clarifies win% = wins/(wins+losses) with expired excluded, and avg R is net of costs and includes expired. Expandable **open-positions panel** (toggled off the roll-up's "open" stat) marks unresolved alerts to the live price via `getLiveOutcomesOpen`, polling every 30s while expanded; columns age/entry/mark/unrealized-R (gross, footnoted against the net avg R in the tables below)/distance-to-SL/distance-to-TP; degrades to a "prices unavailable" note rather than erroring when the mark fetch fails
- Default lookback: 365d
- "Today Path" overlay + "P1 Wick Rank" — live, never cached (Daily Path Cone bands themselves are cached); "Live Alert Outcomes" — cross-symbol, never cached
- Weekly P2 Timing: All/Bullish P1/Bearish P1 toggle; live "This week" banner with DOW, move%, distance bucket, conditioned probabilities

### Nav

- Shows active config name chip when server has a config loaded
- Chart + Stats default symbol: first config symbol → coins.json fallback

---

## Package map (moved from CLAUDE.md 2026-08-04)

- `web/` — web layer (Phase 4 + 5). See `.claude/context/web.md` for full API + UI reference.
  - `api/` — FastAPI: routers (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones, live_outcomes, brief); `GET /api/active-config`, `GET /api/zones`, `GET /api/backtest/analysis`, `GET /api/live-outcomes` (cross-symbol signal_alert_outcomes roll-up, optional `symbol` scope, never cached) + `GET /api/live-outcomes/open` (unresolved alerts marked to live price, the only live-outcomes route with a `get_client` dependency), `GET /api/brief` (read-only deterministic daily market-brief bundle); stats live fields via `_inject_live_fields()`
  - `ui/` — Svelte 5 + Vite; pages: Chart, Backtest, SignalFeed, Positions, Prices, Stats, Brief; build: `make web-build` (**not** a type gate — always pair with `make web-check`). `src/lib/cone.ts` holds the pure cone math (`coneX`/`coneY`/`bandPath`/`linePath`/`overlayPath`/`toPrice`/`fmtNorm`/`fmtPrice`) shared by `PathCone.svelte` (daily, 24 steps) and `WeeklyCone.svelte` (weekly, 168 steps) — period length and the open/normalizer are parameters, so the two cones cannot disagree on the price a band represents
