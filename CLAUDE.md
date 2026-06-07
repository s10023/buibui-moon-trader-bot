# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Working Agreement (persona · quality gate · anti-drift)

**Persona.** You are a senior quant-systems engineer. Bias to de-biased, out-of-sample evidence (DSR / PBO / MinTRL / avg_r across regime × session × combo) over in-sample optimism. Never commit an overfit parameter. Report negative-EV findings honestly — a strategy that loses is a result, not a failure to hide.

**Definition of Done (a gate, not a habit).** A Python change is not "done" until, and you must state each result plainly (if a step was skipped or failed, say so — do not claim green without running it):

- `make lint-py` ✓ (ruff format + lint)
- `make typecheck` ✓ (mypy strict)
- `make test` green
- `make test-regression` goldens unmoved — unless the change is *intentionally* behavioural, in which case regenerate and note it.

**Anti-drift.** Before any multi-step task, restate the goal + its success metric in one line. If a step stops serving that metric, stop and ask rather than drift. Require avg_r × (regime × session × combo) evidence before killing a strategy — demote, don't delete.

**Token efficiency.** Skills are dormant until invoked — don't load what you don't need. Use the context-mode `ctx_*` tools for any command/output over ~20 lines. `/compact` proactively at logical boundaries (don't wait for autocompaction). Delegate heavy reads/long analysis to a subagent only when the saved main-context clutter outweighs the startup cost.

**Guardrail.** A PreToolUse hook (`.claude/hooks/guard-destructive.py`) blocks catastrophic Bash (rm -rf, git reset --hard, force-push, DB wipes). If blocked, do not work around it silently — surface it.

## Project Overview

Buibui Moon Trader Bot — a crypto trading bot for Binance Futures. Live price + position monitoring, an analytics/backtest stack (DuckDB), a 20-strategy signal engine with Telegram alerts, and a FastAPI + Svelte web UI. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** Python code change:

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite
```

For Markdown changes: `make lint-md`.

For UI / API changes: `make web-build` (production bundle) or `make web-dev` (Vite dev server).

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update`).

## CLI

`buibui.py` is the single CLI entry point with subcommands:

- `buibui monitor price | position` — live price / position monitor
- `buibui signal watch | test` — live signal daemon / historical replay. `watch` with no `--config` auto-picks today's config by **UTC weekday** (Mon/Fri→`signal_watch_weekdays.toml`, Tue–Thu→`signal_watch.toml`, Sat/Sun→`signal_watch_all.toml`); the three configs partition the calendar without overlap. UTC (not local) so the picker matches the `day_filter` scope on each candle's UTC `open_time`. `watch --once` runs a single scan cycle and exits (cron / GitHub Actions entry). `DATA_SOURCE=okx` env selects the keyless OKX adapter (`utils/okx_client.py`) instead of Binance — used by `.github/workflows/signal-watch.yaml`, which seeds an ephemeral `analytics.db` from the committed slim `live_signal.duckdb` (`make export-live-db`); local default `DATA_SOURCE=binance` is unaffected
- `buibui analytics backfill | sync` — OHLCV ingestion
- `buibui backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `buibui digest` — pre-canned analytics queries
- `buibui param-audit | param-sweep` — WFO parameter tools
- `buibui recalibrate` — refresh star ratings
- `buibui web` — start FastAPI backend

Each Makefile `buibui-*` target wraps the equivalent CLI invocation.

## Project Structure

- `buibui.py` — thin CLI entry shim (delegates to `cli.main:main`)
- `cli/` — argparse subcommand package: `main.py` builds the top-level parser and dispatches to per-subcommand modules (`monitor.py`, `signal.py`, `analytics.py`, `backtest.py`, `digest.py`, `param.py`, `recalibrate.py`, `web.py`); `_common.py` shared helpers
- `monitor/` — monitor modules split into thin wrappers and pure logic libs:
  - `price_monitor.py` / `position_monitor.py` — thin wrappers (create client, load config, call lib)
  - `price_lib.py` / `position_lib.py` — pure business logic with dependency injection (no module-level side effects)
  - `live_price.py` — WebSocket + Rich live mode for price monitor
  - `live_position.py` — WebSocket + Rich live mode for position monitor
- `analytics/` — analytics data layer (DuckDB-backed). See `.claude/context/analytics.md` for full module API reference.
  - `store/` — DB layer split into 8 modules: `schema.py` (`init_schema`, `DEFAULT_DB_PATH`), `market_data.py` (OHLCV / funding / OI upsert), `signals.py` (`upsert_signals`, `get_signals_history`, `upsert_signal_outcome`), `backtest_runs.py` (`upsert_backtest_run`, `upsert_backtest_trades`, `list_backtest_runs`, `get_win_rate_by_strategy`), `backtest_cache.py` (`BacktestSnapshot`, `get/put/prune_backtest_cache`), `confidence.py` (`upsert_confidence_ratings`, combined + directional getters), `combos.py` (combo + cross-TF combo upsert/list/lookup), `stats_cache.py`. `_common.py` holds the sealed `_upsert` register/unregister helper. `data_store.py` is a thin re-export shim for the 30+ external import sites.
  - **CRITICAL**: `_upsert` (in `store/_common.py`) uses explicit `conn.register`/`conn.unregister` in try/finally — never switch to implicit replacement scan (causes malloc heap corruption). Never drop the try/finally.
  - `data_fetcher.py` / `data_sync.py` / `analytics_runner.py` — fetch, sync orchestration, thin runner
  - `strategies/` — strategy signal detection package (one file per `detect_*` function): 22 detector modules (`wick_fills.py`, `marubozu_retest.py`, `orb_breakout.py`, `liquidity_sweep.py`, `fvg.py`, `market_structure.py` = `bos`, `funding_extreme.py`, `smt_divergence.py`, `eqh_eql.py`, `order_block.py`, `cvd_divergence.py`, `trend_day.py`, `engulfing.py`, `pin_bar.py`, `inside_bar.py`, `hammer_hanging_man.py`, `doji.py`, `morning_evening_star.py`, `fibonacci_retracement.py` legacy, `fib_golden_zone.py`, `ote_entry.py`, `ema.py`); `_base.py` (`ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`); `_shared.py` (`_find_bos_swing`, `volume_confirm`, `compute_ema`, `ema_cross_count`, `is_trending`, `_empty_signals`, `_signals_to_df`, `_fmt_time`); `_seasonality.py` (`seasonality_stats`, `SEASONALITY_COLUMNS`); `_registry.py` (explicit-tuple-driven assembler holding `STRATEGY_REGISTRY` (21 entries), `DETECTOR_REGISTRY` (19; `seasonality` / `smt_divergence` / legacy `fibonacci_retracement` excluded), `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES` (now includes `"trend"` for `ema`), `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS`, `patch_confidence_scores`); `__init__.py` eager re-exports. The package is the public entry — `from analytics.strategies import ...` (the prior `indicators_lib.py` shim was removed in strat-3).
  - `backtest/` — backtest engine split into 6 modules: `engine.py` (`Trade`, `BacktestResult`, `run_backtest` accepts keyword-only `live_parity: LiveParityConfig | None` + `bias_cfg: BiasConfig | None` + `regime_series: pd.Series | None` + `strategy_params: dict[str, StrategyOverride] | None` + `htf_slope_series_by_anchor: Mapping[tuple[str,int,int], pd.Series] | None`, `_compute_atr14`, `_df_to_events`/`_events_to_df` adapters into the live `SignalEvent` shape, `_resolve_regime_at`/`_apply_regime_gate_to_signals` per-signal regime helpers (PR-2), `_resolve_series_at`/`_apply_direction_filter_gate_to_signals`/`_apply_htf_ema_gate_to_signals` PR-3 helpers — direction_filter is a pure per-event flag check, F8 HTF EMA reuses the per-signal last-fully-closed lookup pattern over a pre-computed slope series, `_apply_adr_bias_gate_to_signals` PR-4 adapter — splits signals by per-(tf, direction) `_is_adr_exempt`, runs live's `_filter_signals_by_adr` on the non-exempt slice, concats back ordered (per-direction `adr_exempt_long`/`adr_exempt_short` from PR #380 + per-tf-direction `adr_exempt_long_per_tf`/`adr_exempt_short_per_tf` from PR #401 propagate into backtest replay; the runner skips its legacy pre-filter when `live_parity.adr_bias=True` to avoid double-filtering), `_apply_cooldown_gate_to_signals` PR-5 N-bar gate + `_CooldownState` in-memory ledger keyed by `(symbol, tf, strategy, direction)` (state instantiated inside `run_backtest()` so each call has a fresh ledger per T6 plan Q1; baked-in defaults 15m=4 / 1h=3 / 4h=2 / 1d=1 bars resolved by `_resolve_cooldown_bars`, overridable via `live_parity.cooldown_bars_per_tf`)), `gates.py` (`_is_low_volume`, `_is_volume_spike`, `filter_signals_by_day`), `combo.py` (`ComboBacktestResult`, `run_combo_backtest`), `cross_tf.py` (`CrossTfComboBacktestResult`, `run_cross_tf_combo_backtest`), `formatters.py` (10× `format_*` helpers + `_tf_sort_key`), `live_parity_config.py` (`LiveParityConfig` frozen dataclass — T6 live-parity gate toggles; PR-1 plumbing + PR-2 regime + PR-3 direction_filter + F8 HTF EMA + PR-4 ADR bias + PR-4b conflict resolver + PR-5 cooldown all live-ported. PR-4b applies the conflict resolver at the runner via cross-strategy pooling: `analytics/backtest_runner.py::_resolve_conflicts_for_signals_map` groups detected signals by (symbol, tf) candle, calls the lifted `_apply_conflict_resolver` with a per-strategy avg_r resolver from `confidence_ratings` (`_build_confidence_ratings_map`), then redistributes survivors back into per-strategy DataFrames. Default-off byte-identical because the pooling step is a no-op when `ratings_map is None`). `backtest_lib.py` is a thin re-export shim.
  - `backtest_runner.py` / `backtest_config.py` — thin runner + TOML config loader for sweep mode. `BacktestSweepConfig` mirrors the live-side `[strategy_timeframes]` / `[strategy_timeframes_long]` / `[strategy_timeframes_short]` blocks via `effective_strategy_timeframes(strategy, direction)` (Bucket C — Q-BC-2 additive narrowing). Sweep paths (`_collect_sweep_results` + `_collect_signals_map`) hard-skip cells excluded by the base allowlist before detection, then call `_apply_strategy_timeframes_directional_filter` to drop signal rows by direction × tf — keeps `backtest_runs` + recalibrate + Stats UI aligned with what the live daemon actually fires. Combo + cross-TF workers iterate all strategies for confluence and are deferred to a follow-up.
  - `param_sweep.py` — WFO sweep lib; `run_param_sweep` (returns `ParamSweepReport(rows, gate, n_grid)` — gate computed over the full grid pre-truncation) / `run_strategy_audit`; `format_sweep_results(..., gate=...)` renders the commit-gate stamp; parallelized via `ProcessPoolExecutor`
  - `sweep_guard.py` — P0a-2 commit gate (sub-PR 1): `evaluate_commit_gate(chosen, all_trials, n_grid)` → `CommitGateVerdict` (`COMMIT` / `DO_NOT_COMMIT` / `INSUFFICIENT`). Refuses a swept `tp_r` unless `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n ≥ MinTRL(0.95)`; pure (consumes `research_guards`, no DB/IO). Enforced by `/wfo-sweep` + `/param-sweep-apply` (hard pre-write rule). See `docs/redesign/2026-06-06-p0a-2-sweep-commit-gate-plan.md`
  - `digest_lib.py` — 12 pre-canned SQL queries; `run_digest`; `DigestScope`; powers `buibui digest` + analysis API
  - `cme_gap_lib.py` — CME gap detection + alert warning helper
  - `zones_lib.py` — structural zone extraction (geometry only): FVG, OB, EQH/EQL, BOS, Fib, OTE, swing points
  - `signal/` — signal scanner split into 10 modules: `scanner.py` (`scan_symbol` + `run_scan_cycle` 3-phase fan-out; `_resolve_outcome_sl_tp` — per-event SL/TP resolver for the outcome-ledger writer: structural SL when valid, else the same `entry*(1±eff_sl_pct)` pct fallback the alert formatter uses, so **every** fired event persists a non-NULL `sl_price`/`tp_price`/`rr_ratio` and is scoreable — no NULL hole), `types.py` (`SignalEvent`, `StatsContext`, `ConfluenceData`), `gates.py` (`_filter_signals_by_adr`, `_is_adr_exempt`, `_apply_direction_filter_gate`, `_apply_htf_ema_gate` (F8; honors per-strategy `suppress_directions` — precedence: per-strategy override → global `[bias.htf_ema].suppress_directions` → built-in `("long","short")`; `[]` = exempt, omitted key = symmetric/back-compat), `_apply_regime_gate`), `resolvers.py` (10× `_resolve_*` helpers, incl. `_resolve_atr_sl_floor`), `bt_cache.py` (`_compute_backtest`, `_backtest_summary`), `atr_floor.py` (`_apply_atr_floor` — F9 ATR-as-min-SL widener for the live path; mirrors backtest engine), `outcome_backfill.py` (`backfill_outcomes` — forward-walks OHLCV to resolve outstanding `signal_alert_outcomes` rows; called once per cycle from `signal_runner`), `stats_context.py` (`_compute_stats_context`), `cofire.py` (live + cross-TF co-fire detection), `_common.py` (`_bt_mem_cache`, `_reset_bt_cache`, timeframe parsing). `signal_lib.py` is a 4-line re-export shim.
  - `signal_config.py` — `SignalWatchConfig`, `BacktestFilterConfig`, `BiasConfig`, `ComboConfig`; TOML `extends` support
  - `stats/` — stats package split per dimension: `bundle.py` (top-level `compute_all` orchestrator), `p1p2.py`, `adr.py`, `dow.py`, `hourly.py`, `session.py`, `daily_distance.py`, `weekly_state.py`, `weekly_p1p2.py`, `weekly_p2_timing.py`, `weekly_flip_risk.py`, `weekly_wick.py`, `live_outcomes.py` (cross-symbol live-alert outcome roll-up over `signal_alert_outcomes` — roll-up + per-(strategy, tf, direction) + per-strategy win-rate/avg-R; NOT part of the per-symbol `StatsBundle`/cache, served by its own router). `_common.py` shared helpers; live fields injected by `bundle._inject_live_fields()`. `stats_lib.py` is a re-export shim.
  - `signal_runner.py` — daemon thin wrapper; OHLCV cache; combo lookup refresh every 10 cycles
  - `signal_test_runner.py` — historical replay: no DB writes, no cooldown; `--at` / `--lookback`
  - `recalibrate_lib.py` / `recalibrate_runner.py` — compute + write star ratings to DB or source
  - `perf_timer.py` — `timed(label)` context manager
  - `regime.py` — §6 regime classifier (`trend`/`range`/`high_vol`/`unknown`); pure function over OHLCV; wired as Phase 2 live gate (soft mode shipped 2026-05-10) per `docs/redesign/buibui-redesign.md`
  - `research_guards/` — P0a-1 overfitting / multiple-testing controls (pure math, no DB/IO/deps beyond numpy + stdlib `statistics.NormalDist`): `psr.py` (Probabilistic Sharpe), `dsr.py` (Deflated Sharpe + `expected_max_sharpe`), `pbo.py` (`cscv_pbo` / `PBOResult` — CSCV overfit probability), `haircut.py` (`haircut_sharpe` Bonferroni/Holm/BHY), `mintrl.py` (Minimum Track Record Length), `bootstrap.py` (`block_bootstrap_ci` stationary/circular). **P0a-2 wiring (sub-PR 1, PR #422): now consumed by `analytics/sweep_guard.py` for the param-sweep commit gate**; audit-tool CIs + recalibrate DSR-annotation still pending. See `docs/redesign/2026-06-05-p0a-1-research-guards-pr.md`
- `signals/` — signal detection daemon package (alerting + dedup only — detection lives in `analytics/`). See `.claude/context/signals.md` for full reference.
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (20 actionable strategies; `seasonality` / `fibonacci_retracement` excluded)
  - `cooldown_store.py` — two-layer dedup: candle watermark + cooldown timer; JSON-persisted to `signal_state.json`
  - `alert_formatter.py` — `SignalEvent`, `StatsContext`, `ConfluenceData`; 6-section alert layout; W1–W8 candle warnings
  - `DEFAULT_DB_PATH` lives in `analytics/store/schema.py` (re-exported via `analytics.data_store`) — import from either, do not redefine in runners
- `utils/` — shared utilities:
  - `binance_client.py` — Binance client creation, time sync, config loading; `create_data_client()` dispatches on `DATA_SOURCE` (`okx`→`OKXClient`, else Binance)
  - `okx_client.py` — keyless OKX V5 public market-data adapter; `OKXClient.futures_klines()` returns Binance-shaped OHLCV (backward pagination via `after`, drops unconfirmed candle, `1d`→`1Dutc`, neutral `taker_buy_volume=volume/2`). `analytics.data_fetcher.fetch_klines` branches on `isinstance(client, OKXClient)`; `KlineClient = Client | OKXClient` type alias. Funding/OI intentionally not implemented (no live detector needs them on the OKX path)
  - `config_validation.py` — coins.json schema validation
  - `telegram.py` — Telegram message sending
  - `live_store.py` — shared in-memory store for live WebSocket data
  - `live_loop.py` — shared Rich live display loop logic
- `web/` — web layer (Phase 4 + 5). See `.claude/context/web.md` for full API + UI reference.
  - `api/` — FastAPI: routers (config, ohlcv, fib, signals, backtest, positions, prices, stream, stats, zones, live_outcomes); `GET /api/active-config`, `GET /api/zones`, `GET /api/backtest/analysis`, `GET /api/live-outcomes` (cross-symbol signal_alert_outcomes roll-up, never cached); stats live fields via `_inject_live_fields()`
  - `ui/` — Svelte 5 + Vite; pages: Chart, Backtest, SignalFeed, Positions, Prices, Stats; build: `make web-build`
- `trade/open_trades.py` — Binance Futures order opener (manual/CLI use; wired via `make buibui-open-trades`). No automation hooked into the signal daemon yet.
- `tools/` — one-shot analysis scripts (not part of the daemon/CLI surface):
  - `strategy_edge_audit.py` — Phase 0 strategy edge audit; aggregates `backtest_trades` by (strategy × tf × regime × session) + combo uplift; deterministic KILL/DEMOTE/KEEP rule. Run via `PYTHONPATH=. poetry run python tools/strategy_edge_audit.py`. See `docs/redesign/buibui-redesign-phase0.md`.
  - `live_outcomes_report.py` — read-only spot-check of `signal_alert_outcomes` after the T2 backfill worker runs; reports the resolved/open mix, per-(strategy, tf, direction) win rate + avg_r, and per-strategy aggregate. Stop-gap until a Stats UI card lands. Run via `PYTHONPATH=. poetry run python tools/live_outcomes_report.py [--days N] [--min-n N]`.
  - `backfill_null_tp_outcomes.py` — retroactive migration for the ~2,141 pre-fix `signal_alert_outcomes` rows written with NULL `tp_price` (unscoreable). Reconstructs the pct-fallback SL/TP from stored `entry_price` + per-(strategy,symbol,tf,direction) `eff_sl_pct`/`eff_tp_r` (resolved from a live config TOML; original structural SL is unrecoverable → best-effort pct fallback, mirroring the forward fix), then resolves via `backfill_outcomes()`. **Read-only by default; `--apply` writes (gated on review). Idempotent.** Run via `PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py [--config <toml>] [--apply]`.
  - `gate_audit.py` — T6 Phase A engine-side gate auditor; replays `backtest_trades` against a candidate gate change (`volume-suppress` / `volume-spike-boost` / `day-filter` / `adr-exempt`) and emits per-(strategy × tf × direction × symbol) **ENABLE / DISABLE / CONCENTRATE / INSUFFICIENT** verdicts under the ±0.05R bar at `min_n ≥ 30`. `CONCENTRATE` fires when `supp_avg_r ≥ +0.05R` but `kept_avg_r ≥ supp_avg_r + 0.05R` (gate is concentrating quality on a higher-grade kept subset — lifting it would dilute net avg_r; surfaced by PR #398 + PR #400 kept-side-concentration shapes). ADR handler reuses `analytics/signal/gates.py::_filter_signals_by_adr` verbatim. Run via `PYTHONPATH=. poetry run python tools/gate_audit.py <gate> [--config <toml>] [--grain strategy_tf_dir]`. See `docs/redesign/buibui-redesign-t6-phase-a-plan.md`.
  - `adr_threshold_audit.py` — T6 Phase A `adr_suppress_threshold` sweep (PR closing Phase A); orthogonal to `gate_audit.py` (which can't natively sweep the global threshold). Mirrors `_filter_signals_by_adr` to mask chasing-direction trades in `[candidate, current_threshold)` per (symbol, tf), aggregates avg_r across non-exempt strategies, and emits the same ENABLE / DISABLE / INSUFFICIENT verdicts per candidate threshold. Replay-only constraint: can test stricter (lower) candidates only — relaxed thresholds need a permissive-baseline run (T6 engine work). Run via `PYTHONPATH=. poetry run python tools/adr_threshold_audit.py --config <toml> [--candidates 0.60,0.65,0.70,0.75] [--per-strategy-at 0.70]`. See `docs/audits/2026-05-17-adr-suppress-threshold.md`.
- `tests/` — pytest suite; tests import from lib modules and pass mock dependencies directly
- `.claude/context/` — long-form module references (`analytics.md`, `signals.md`, `web.md`) split out to keep this file lean
- `config/coins.json` — per-symbol leverage and stop-loss config (gitignored; see `coins.json.example`)
- `config/strategy_params.toml` — shared base config inherited via `extends = "strategy_params.toml"` by `signal_watch.toml`, `signal_watch_all.toml`, `signal_watch_weekdays.toml`. Contains `[smt_pairs]`, `[bias]`, `[backtest]` defaults, per-strategy `volume_suppress` flags, and `tp_r_long` / `tp_r_short` directional overrides. `[bias.htf_ema].suppress_directions` scopes which signal directions F8 may suppress (global list + per-strategy `per_strategy.<name>.suppress_directions` overrides); production ships `["long"]` (soft mode) with flow family exempt (`[]`) and fib family symmetric (`["long","short"]`).

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
- **Regression tests**: `make test-regression` — compares backtest pipeline output to golden JSON files in `tests/fixtures/`; skips if fixture parquets are absent; run `make regression-update` to regenerate golden files after intentional changes

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames), `pyarrow` (parquet fixture I/O)
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Session Memory Protocol

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Do not wait to be asked.

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
| `recalibrate` | `/recalibrate` | Update strategy star ratings in the `confidence_ratings` DB table from accumulated backtest runs (feeds Backtest UI stars, Telegram alerts, live signal-watch quality gate) | After any `make buibui-backtest SAVE=1` adds new runs |
| `volume-sweep` | `/volume-sweep` | Test `volume_suppress` per strategy; compare High Vol vs Low Vol avg R | When adding a new strategy; after entry logic changes that affect signal frequency |
| `new-strategy` | `/new-strategy` | Guided 4-file checklist for adding a new strategy (`analytics/strategies/<name>.py`, `_registry.py`, `signals/registry.py`, tests) | Every time a new strategy is added |
| `backtest-run` | `/backtest-run` | Quick reference for all `buibui backtest` invocations and flags | Any time you need a backtest command and can't remember the flags |
| `investigate-strategy` | `/investigate-strategy` | Debug why a strategy did/didn't fire on a specific candle using `buibui signal test` | When asked to investigate, diagnose, or replay a signal |
| `signal-watch` | `/signal-watch` | Signal daemon workflow, TOML config reference, signal flow diagram | When configuring or debugging the live signal scanner |
| `pr-summary` | `/pr-summary` | Write PR title + summary + test plan to `/tmp/pr-<branch>.md` | After finishing any feature branch |
| `post-branch` | `/post-branch` | Behaviour-gated docs sweep: diff branch changes against CLAUDE.md / README.md / MEMORY.md / Makefile / docker-compose.yml / `.claude/context/`, propose targeted edits, append "Documentation updates" to PR body. Skips for pure refactors. | Immediately after `gh pr create`, before reporting the PR URL |
| `stats-dashboard` | `/stats-dashboard` | Stats page architecture, card inventory, adding new cards, timezone constraints | When working on Stats page or `stats_lib.py` |
| `db-update` | `/db-update` | Routine `make db-update`: backtest (3 configs) → recalibrate → regression golden refresh | After any detector / strategy / config change that affects ratings or fixtures |
| `data-backfill` | `/data-backfill` | OHLCV ingestion via `buibui analytics backfill` / `sync` | First-time setup, wiped DB, new symbol or timeframe, filling a data gap |
| `confluence-backtest` | `/confluence-backtest` | Cross-TF (`--cross-tf`) and same-TF (`--combo`) co-firing backtests; HTF/LTF pair sweeps; post-run spot-check via `tools/combo_health.py` | After adding a strategy, changing entry logic, tuning the live `[combo]` gate, or to confirm combo tables are healthy after a refresh |
| `frontend-svelte` | `/frontend-svelte` | Svelte 5 + Vite UI workflow for `web/ui/` — pages, stores, lightweight-charts, dev/build commands | Any work under `web/ui/`; pair with `/frontend-design` for visual work |
| `journal-trade` | `/journal-trade` | Capture a manual trade into the gitignored `docs/plans/journal/` (structured frontmatter + Thesis/Plan/Execution/Outcome/Retrospective narrative); MYT→UTC timestamps; ground-truth feeder for F2/T2/T5 | When the user says "journal my trade", pastes trade-execution details, or a logged trade closes |

**Always load `/frontend-design` before any Svelte/CSS/UI changes.**

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
