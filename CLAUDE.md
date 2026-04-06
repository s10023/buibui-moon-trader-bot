# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Project Overview

Buibui Moon Trader Bot — a crypto trading bot for Binance Futures with live price monitoring and position tracking. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** code changes, always run these checks:

```bash
# Format Python code
make lint-py

# Type check
make typecheck

# Run tests
make test
```

For Markdown changes:

```bash
make lint-md
```

## Project Structure

- `buibui.py` — CLI entry point (argparse)
- `monitor/` — monitor modules split into thin wrappers and pure logic libs:
  - `price_monitor.py` / `position_monitor.py` — thin wrappers (create client, load config, call lib)
  - `price_lib.py` / `position_lib.py` — pure business logic with dependency injection (no module-level side effects)
  - `live_price.py` — WebSocket + Rich live mode for price monitor
  - `live_position.py` — WebSocket + Rich live mode for position monitor
- `analytics/` — analytics data layer (DuckDB-backed):
  - `data_store.py` — pure DB lib: schema init, upsert (ohlcv/funding/OI/signals/backtest), range queries; `upsert_signals(conn, df)` persists fired signals; `get_signals_history(conn, symbol, tf, start_ms, end_ms)` reads them back; `list_backtest_runs(conn)` returns all saved runs newest-first with `stars`, `long_stars`, `short_stars` JOINed from `confidence_ratings` by `(strategy, tf, day_filter, direction)` — PARTITION BY includes `adr_suppress_threshold` so runs with/without ADR bias gate appear as separate rows for Backtest tab comparison; `upsert_backtest_run` / `upsert_backtest_trades` persist backtest results; `upsert_confidence_ratings(conn, config_name, ratings, win_rates, day_filter=None, direction="combined")` stores per-config star ratings keyed by `(config_name, strategy, tf, direction)`; `get_confidence_ratings(conn, config_name, direction="combined")` loads combined stars; `get_directional_confidence_ratings(conn, config_name)` → `{strategy: {tf: {"long": stars, "short": stars}}}` for directional override; `confidence_ratings` PK is `(config_name, strategy, tf, direction)` — direction is `'combined'`, `'long'`, or `'short'`; `backtest_runs.adr_suppress_threshold REAL NULL` — NULL=no filter; `backtest_runs.recovery_factor DOUBLE NULL` — total_r / max_drawdown_r, populated on upsert; `_backtest_run_id` appends `|adr:X` only when threshold is set so existing run_ids are unchanged
  - `data_fetcher.py` — pure fetch lib: Binance Futures API → DataFrames (no DB concerns)
  - `data_sync.py` — pure orchestration: paginated backfill + incremental sync
  - `analytics_runner.py` — thin wrapper: creates client, opens DB, calls sync lib
  - `indicators_lib.py` — pure strategy signal detection (21 active strategies: seasonality, wick_fill, marubozu, orb, liquidity_sweep, fvg, bos, funding_reversion, smt_divergence, eqh_eql, order_block, cvd_divergence, trend_day, engulfing, pin_bar, inside_bar, hammer_hanging_man, doji, morning_evening_star, fib_golden_zone, ote_entry); `fibonacci_retracement` is legacy/commented-out (superseded by `fib_golden_zone`); also exports `ParamSpec`, `StrategySpec`, `STRATEGY_REGISTRY`, `DETECTOR_REGISTRY`, `KNOWN_STRATEGIES`; `StrategySpec.confidence` is `dict[str, int] | int` — use `get_confidence(tf)` to resolve per-TF rating (falls back to `"default"` key then `3`); `SIGNAL_COLUMNS` includes `tp_price` — `fib_golden_zone` and `ote_entry` populate it with the structural 1.618 extension TP; other strategies leave it `0.0` (formatter falls back to `sl_dist × tp_r`)
  - `backtest_lib.py` — pure backtest engine: Trade, BacktestResult, run_backtest, format helpers; fee drag uses actual trade risk (`2 * fee_pct * entry / risk`); `min_sl_pct` widens structural SLs that land too close to entry (prevents fee-drag explosion); `run_backtest(volume_suppress=bool)` skips low-volume signal candles when True (volume < 1.5× 20-candle rolling mean); detectors called once on full OHLCV history — rolling detectors (fib_golden_zone, ote_entry, order_block, eqh_eql, cvd_divergence) generate signals at every historical candle; last-candle-only detectors (candlestick patterns) fire at most once per backtest run; `BacktestResult` exposes per-direction split: `long_closed_trades`, `long_win_count`, `long_win_rate`, `long_avg_r`, `long_total_r` + short equivalents; `max_drawdown_r` (peak-to-trough cumulative R) + `recovery_factor` (total_r / max_drawdown_r, 0.0 when no drawdown) properties
  - `backtest_runner.py` — thin wrapper: opens DB, loads OHLCV/funding, calls indicator + backtest libs
  - `signal_lib.py` — pure scan lib: `scan_symbol()` (runs strategies on one symbol/tf), `run_scan_cycle()` (fans out, deduplicates, sends Telegram, persists passing signals to DB via `upsert_signals`); both accept `confidence_override: dict[str, dict[str, int]] | None` (combined, per-config) and `directional_confidence_override: dict[str, dict[str, dict[str, int]]] | None` ({strategy: {tf: {direction: stars}}}) — directional takes precedence over combined which takes precedence over registry default; `_compute_backtest()` respects `fee_pct`, `day_filter`, `min_sl_pct` from `BacktestFilterConfig` and `adr_suppress_threshold` from `BiasConfig` — applies `_filter_signals_by_adr()` after day_filter so stored avg_r reflects only ADR-gate-passing trades; `_filter_signals_by_adr(ohlcv_df, signals_df, threshold)` is directional: suppresses only the chasing direction (LONGs when close > range midpoint, SHORTs when close < midpoint) so reversal signals at extremes pass through; `_is_adr_exempt(strategy_params, strategy)` returns True for strategies with `adr_exempt=True` — bypasses both the live gate and `_compute_backtest` filter; exempt strategies store `NULL` `adr_suppress_threshold` in `backtest_runs` so they appear as no-filter runs in the Backtest tab; `_resolve_volume_suppress(strategy_params, strategy, global_suppress)` — per-strategy volume gate lookup (per-strategy override → global fallback); volume gate in live daemon loop applies per-event (each signal's strategy resolved independently); `_compute_stats_context()` computes per-symbol `StatsContext` once per cycle and passes it into alert formatting; `run_scan_cycle` accepts `bias_cfg: BiasConfig | None`
  - `stats_lib.py` — pure stats lib: `compute_p1p2_daily` (→ `P1P2Result` with `p1_strong_pct` — fraction of P1 candles where P1-direction wick < 20% of range), `compute_hourly_extremes` (incl. `peak_high/low_hour_by_dow` per-DOW MODE), `compute_adr` (→ `ADRResult` with `adr_14`, `adr_30`, `today_range_pct`, `today_consumed_pct`, `today_move_up: bool | None`), `compute_dow_patterns` (incl. `avg_return_pct`, `strong_high_pct`, `strong_low_pct` — fraction of days each wick < 20% of range), `compute_session_breakdown`, `compute_weekly_p1p2`, `compute_weekly_p2_timing` (→ `WeeklyP2Timing` with `low/high_still_ahead_by_dow` + `low/high_flip_risk_by_dow`), `compute_weekly_flip_risk_conditioned` (→ `WeeklyFlipRiskConditioned` with `rows: list[WeeklyFlipRiskConditionedRow]`; for each historical week identifies P1 direction — which extreme was set first — then computes P(P2 still ahead | p1_direction, DOW); p1_direction="low" = bullish weeks, p1_direction="high" = bearish weeks), `compute_all` → `StatsBundle`; live (never-cached) functions injected via `_inject_live_fields()`: `compute_weekly_current_state(conn, symbol, adr_14, days)` → `WeeklyCurrentState | None`, `compute_daily_distance(conn, symbol, adr_14, days)` → `DailyDistanceResult | None` (P(historical move > today's) + gap to p80), `compute_weekly_wick_percentile(conn, symbol, adr_14, days)` → `WeeklyWickPercentile` (current week P1 wick exceedance vs history; None fields if P1 not yet set); all times in MYT (UTC+8) using `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP`; raises `ValueError` on empty data
  - `signal_runner.py` — thin wrapper: creates client, opens DB, syncs candles, polls `run_scan_cycle` in a loop; all TOML params (`sl_pct`, `cooldown_seconds`, `fee_pct`, `day_filter`, `bias_cfg`) are wired through; loads both `confidence_override` (combined) and `directional_confidence_override` (long/short) from DB at startup and passes both to `run_scan_cycle`
  - `signal_config.py` — `BacktestFilterConfig` (includes `fee_pct`, `min_sl_pct`, and `min_avg_r`; each loaded from `[backtest].*` falling back to top-level); hard mode gates on `min_avg_r` (directional avg_r ≥ threshold) — replaces old win-rate `filter_threshold` + `SignalWatchConfig` + `load_signal_config()`; `SymbolOverride` dataclass holds per-symbol tp_r/sl_pct/atr_sl overrides within a strategy block; `StrategyOverride.per_symbol` maps symbol → `SymbolOverride`; `StrategyOverride.adr_exempt: bool` — when True the ADR bias gate is skipped for this strategy (use for breakout/continuation strategies); `StrategyOverride.volume_suppress: bool | None` — when True/False overrides the global `[backtest].volume_suppress` for this strategy (None = inherit global); `SignalWatchConfig.effective_volume_suppress(strategy)` resolves per-strategy → global fallback; resolution order: `symbol+TF → symbol → TF → strategy → global`; `BiasConfig` holds F8 bias layer params (`adr_suppress_threshold`, `dow_soft_suppress`, `dow_suppress_min_abs_return`) loaded from `[bias]` TOML section; `SignalWatchConfig.bias` carries it through to `run_scan_cycle`
  - `backtest_config.py` — `BacktestSweepConfig` (includes `min_sl_pct`, `liq_sweep_use_fib`, `volume_suppress: bool`) + `load_backtest_config()` — TOML config loading for sweep mode; supports `--day-filter` CLI flag; `liq_sweep_use_fib` toggles `liquidity_sweep` between fib-extension mode (default) and pivot-sweep mode for backtest comparison; `is_adr_exempt(strategy)` returns True for strategies with `adr_exempt = true` in their TOML block — used by `backtest_runner.py` to skip ADR filter; `effective_volume_suppress(strategy)` mirrors signal_config resolution (per-strategy → global); also exports `SymbolOverride` (same per-symbol override dataclass as `signal_config.py`)
  - `recalibrate_lib.py` — pure recalibration lib: `get_backtest_win_rates(conn)` → DataFrame with combined + directional columns (`long_avg_r`, `short_avg_r`, `long_total_trades`, `short_total_trades`); `win_rate_to_stars(avg_r, total_trades)`; `compute_recalibrated_ratings(conn, min_trades)` → `dict[str, dict[str, int]]` (combined per TF); `compute_directional_ratings(conn, min_trades=5)` → `{strategy: {tf: {"long": stars, "short": stars}}}` — uses lower default min_trades since directional splits have fewer trades; `format_recalibration_report(old, new, win_rates, directional_ratings=None)` — shows L★/S★ columns when directional_ratings provided; `write_confidence_to_db(conn, config_name, ratings, win_rates, day_filter=None, directional_ratings=None)` — writes combined + long + short directions; `write_confidence_to_source` patches `indicators_lib.py` directly (legacy fallback)
  - `recalibrate_runner.py` — thin wrapper: opens DB, calls lib, prints diff report; `--config <toml>` derives `day_filter`, `config_name`, and `adr_suppress_threshold` from TOML; `--apply` with `--config` writes to `confidence_ratings` DB table keyed by config name; `--apply` without `--config` falls back to legacy source patching of `indicators_lib.py`; wired as `buibui.py recalibrate` subcommand and `make buibui-recalibrate`
- `signals/` — signal detection daemon package:
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (20 actionable strategies; seasonality + legacy fibonacci_retracement excluded); `confidence` field removed — resolved per-TF at dispatch time via `STRATEGY_REGISTRY[name].get_confidence(tf)`
  - `cooldown_store.py` — two-layer dedup: candle watermark per `(symbol, tf, strategy)` + cooldown timer per `(symbol, strategy, direction)`; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent` + `StatsContext` dataclasses; `SignalEvent.tp_price: float` — structural TP from detector (e.g. 1.618 fib ext); `0.0` means use `tp_r` fallback; `StatsContext.adr_move_up: bool | None` indicates whether today's move was upward (used by the directional ADR gate); `StatsContext.wk_low/high_still_ahead_conditioned_pct: float | None` + `wk_move_bucket: str | None` — M4 conditioned weekly timing (prefer over unconditional when populated); `format_signal_alert()` → Markdown Telegram message with SL/TP levels + optional 2-line stats footer separated by blank line (line 1: `📐 bull%, avg DOW return, "still ahead" P1 framing, ADR progress bar`; line 2: `🎯 TP window: per-DOW peak hour, weekly P2 timing`); `_adr_bar(consumed_pct)` renders a 10-char ASCII bar with `▓` overflow; `_format_stats_line(ctx, direction)` is direction-aware (LONG vs SHORT); prefers conditioned pct over unconditional when `wk_move_bucket` is set
  - `DEFAULT_DB_PATH` lives in `data_store.py` — import from there, do not redefine in runners
  - `_upsert` uses explicit `conn.register` / `conn.unregister` in a try/finally — do NOT switch to the implicit `conn.execute("... FROM df")` replacement scan; it holds a raw C pointer without Py_INCREF and causes malloc heap corruption at `conn.close()` after multiple batches. Do NOT drop the try/finally; unregister must always run or the stale registration causes the same crash.
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
- `web/` — web layer (Phase 4 + 5):
  - `api/` — FastAPI backend: `main.py` (app + StaticFiles mount), `deps.py` (`require_token`, `require_token_sse` for SSE query-param auth), `routers/` (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats), `models/` (Pydantic models for each router); backtest router exposes `GET /api/backtest/runs` (all saved runs) and `POST /api/backtest` (run + auto-save); `BacktestRunSummary` has `stars`, `long_stars`, `short_stars`: `int | None` (resolved per-row via `confidence_ratings` JOIN by direction), `long_total_r`/`short_total_r`/`recovery_factor`: `float | None`, and validators to coerce pandas NaN → None for nullable columns; config router exposes `GET /api/strategies?config=<name>` to return confidence values overridden with per-config DB ratings; stats router exposes `GET /api/stats/{symbol}?days=180` (cached daily per symbol in `stats_cache` table); `weekly_current_state`, `daily_distance`, and `weekly_wick_percentile` fields on `StatsResponse` are computed fresh on every request (live, never cached) and injected via `_inject_live_fields()` after cache hit/miss
  - `ui/` — Svelte 5 + Vite frontend: `src/api.ts` (typed client), `src/stores/` (config, strategies, prices SSE, positions SSE), `src/pages/` (Chart, Backtest, SignalFeed, Positions, Prices, Stats), `src/components/` (Nav, CandleChart, BacktestResult, …). Build: `make web-build` → `web/ui/dist/` served by FastAPI StaticFiles. Backtest page: DB-backed sortable/filterable table loads on mount; collapsible run form; stars per row from `BacktestRunSummary.stars` (combined), `long_stars` (↑★), `short_stars` (↓★) — all JOINed by `(strategy, tf, day_filter, direction)` at query time; columns: long/short win rate, avg R, total R (↑/↓), **Max DD**, **RF** (recovery factor, color-coded ≥3 green/2–3 yellow/<2 red) — all sortable; **ADR Gate** column + chip filter shows `adr_suppress_threshold` per row (2dp, `—` for NULL) so bias-on and bias-off runs can be compared side by side; filters organised into three labeled sections: CATEGORY (symbol/TF/strategy/day filter/ADR gate/stars), PERF (win%/trades/avg R/total R/max DD/RF), DIR (directional long+short win%/avg R/total R). Stats page: 10-card grid (P1/P2 — incl. P1 strong%, ADR, hourly distribution, DOW patterns — incl. Str H/Str L columns, session breakdown, weekly P1/P2, avg return by day, weekly P2 timing with flip risk, Daily Distance, P1 Wick Rank). Default lookback: 365d. "Daily Distance" card shows P(historical daily move > today's) + gap to 80th-percentile — live, never cached. "P1 Wick Rank" card shows current week's P1 wick exceedance vs historical P1 wicks — live, never cached; shows "P1 not yet set" when only one weekly extreme has formed. Weekly P2 Timing card has All/Bullish P1/Bearish P1 toggle — "All" = unconditional, conditioned modes show P(P2 still ahead | P1 direction, DOW) from `weekly_flip_risk_conditioned`. Weekly P2 Timing card also shows a live "This week" banner with current DOW, move% from weekly open, distance bucket, and conditioned low/high-still-ahead probabilities.
- `tests/` — pytest suite; tests import from lib modules and pass mock dependencies directly
- `config/coins.json` — per-symbol leverage and stop-loss config

## Code Style

- **Linter + Formatter**: ruff (replaces black; handles linting, import sorting, and formatting)
- **Type checker**: mypy (strict — `disallow_untyped_defs = true`)
- **All functions must have type annotations** including return types (`-> None` for test methods)
- **Markdown linter**: markdownlint-cli2
- Use `from typing import Any` for mock parameters in tests

## Testing

- Framework: pytest + unittest.mock
- Tests must not make real network calls — lib functions accept a `client` parameter; tests pass a `MagicMock` directly
- Analytics tests use `duckdb.connect(":memory:")` for full DB isolation — never touch the real `analytics.db`
- Run: `make test` or `poetry run pytest tests/ -v`

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames)
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Session Memory Protocol

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Do not wait to be asked.

Fields to keep current:

- Last session summary (one line: what changed)
- Open questions / pending decisions (or "none")

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (project-specific, committed to repo) and are invoked with `/skill-name`. Each encapsulates a recurring workflow so you don't need to re-explain it. Use them proactively.

| Skill | Invoke | When to use | Cadence |
| ----- | ------ | ----------- | ------- |
| `sanity-check` | `/sanity-check` | Full project health check: git hygiene, docs sync, wiring audit, architecture review, skills freshness | Weekly or after any large refactor |
| `atr-sweep` | `/atr-sweep` | Find optimal ATR SL multiplier per strategy × TF; translates to `atr_sl_multiplier` TOML overrides | After any SL-related change or when backtests show high fee drag |
| `wfo-sweep` | `/wfo-sweep` | **Full automated WFO chain**: param-audit → param-sweep → apply → backtest → recalibrate → commit. One command to refresh all tp_r for a config. | When a config feels stale or after any major strategy/detector change |
| `config-refresh` | `/config-refresh` | Full TOML refresh: fix strategy_timeframes gaps, run TP sweep, update tp_r per strategy × TF, commit | When a signal_watch config feels stale, after detector rewrites, or when weekdays config drifts behind signal_watch.toml |
| `backtest-findings` | `/backtest-findings` | Interpret any sweep table (ATR/TP/volume/duration) and commit winners to TOML | After every sweep run |
| `param-sweep-apply` | `/param-sweep-apply` | Auto-apply WFO param-sweep/param-audit results: parse pasted tables, pick best tp_r per strategy × TF, edit TOML, run backtest + recalibrate | Paste results and invoke — use when running sweeps manually outside `/wfo-sweep` |
| `recalibrate` | `/recalibrate` | Update strategy star ratings in `indicators_lib.py` from DB backtest runs | After any `make buibui-backtest SAVE=1` adds new runs |
| `volume-sweep` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (indicators_lib, DETECTOR_REGISTRY, signals/registry, tests) | Every time a new strategy is added |
| `backtest-run` | `/backtest-run` | Quick reference for all `buibui backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `signal-watch` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch` | `/post-branch` | Check CLAUDE.md, README.md, MEMORY.md, Makefile, docker-compose.yml for needed updates | After every branch — run automatically without being asked |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
