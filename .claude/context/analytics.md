# Analytics Module Reference

Detailed API reference for `analytics/`. Load this when working on any analytics module.

## data_store.py — DB schema + upsert/query helpers

- `upsert_signals(conn, df)` / `get_signals_history(conn, symbol, tf, start_ms, end_ms)`
- `list_backtest_runs(conn)` — newest-first; JOINs `stars`, `long_stars`, `short_stars` from `confidence_ratings` by `(strategy, tf, day_filter, direction)`; PARTITION BY includes `adr_suppress_threshold` so ADR-on/off runs appear as separate rows
- `upsert_backtest_run` / `upsert_backtest_trades`
- `upsert_confidence_ratings(conn, config_name, ratings, win_rates, day_filter=None, direction="combined", dsr_map=None)` — PK `(config_name, strategy, tf, direction)`; direction = `'combined'` | `'long'` | `'short'`; `dsr_map={strategy: {tf: dsr}}` writes the additive `dsr REAL` column (NULL when absent)
- `get_confidence_ratings(conn, config_name, direction="combined")` / `get_directional_confidence_ratings(conn, config_name)` → `{strategy: {tf: {"long": stars, "short": stars}}}`
- `backtest_runs` columns: `adr_suppress_threshold REAL NULL`, `recovery_factor DOUBLE NULL`, `volume_suppress BOOLEAN NULL`
- `_backtest_run_id` appends `|adr:X` / `|vol_suppress` for unique run_id per param combo
- **D10 same-TF**: `backtest_combos` table; `upsert_combo_run` → stable `combo_id` (`symbol|tf|A+B|wN|day_filter`, no timestamp → `INSERT OR REPLACE`); `list_combo_runs(conn)`; `get_combo_lookup(conn)` → `dict[(symbol, tf, frozenset({a,b})), row_dict]` best avg_r per pair
- **D10 cross-TF**: `backtest_cross_tf_combos` keyed by `(symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf, window_hours, day_filter)`; `upsert_cross_tf_combo_run` / `list_cross_tf_combo_runs` / `get_cross_tf_combo_lookup` → ordered key (not frozenset — HTF/LTF roles are distinct)
- **CRITICAL**: `_upsert` uses explicit `conn.register`/`conn.unregister` in try/finally — do NOT switch to implicit replacement scan; it causes malloc heap corruption at `conn.close()`. Never drop the try/finally.
- `DEFAULT_DB_PATH` lives here — import from here, do not redefine in runners

## data_fetcher.py / data_sync.py / analytics_runner.py

- `data_fetcher.py` — pure fetch: Binance Futures API → DataFrames (no DB). `fetch_klines` accepts `KlineClient = Client | OKXClient` and branches on `isinstance(client, OKXClient)` (OKX returns a ready OHLCV DataFrame; Binance maps raw rows). `DATA_SOURCE=okx` selects the OKX adapter via `utils.binance_client.create_data_client`
- `data_sync.py` — pure orchestration: paginated backfill + incremental sync (`backfill` / `sync` retyped to `KlineClient`, so they drive Binance or OKX). `backfill_funding_rates` paginates full funding-rate history from `since_ms` (Binance-only, 1000/page), wired into `run_backfill` via `_sync_ancillary(funding_since_ms=)` so a deep OHLCV backfill now pulls matching funding depth (incremental `sync` keeps recent-only funding via `sync_funding_rates`)
- `analytics_runner.py` — thin wrapper: creates client, opens DB, calls sync lib

## strategies/ — strategy signal detection package

(After strat-3 the prior `indicators_lib.py` shim is removed; the 22 detect_* functions and the registries live in `analytics/strategies/`. Public entry: `from analytics.strategies import ...`.)

- **Per-detector modules**: `wick_fills.py`, `marubozu_retest.py`, `orb_breakout.py`, `liquidity_sweep.py`, `fvg.py`, `market_structure.py` (= `bos`), `funding_extreme.py`, `smt_divergence.py`, `eqh_eql.py`, `order_block.py`, `cvd_divergence.py`, `trend_day.py`, `engulfing.py`, `pin_bar.py`, `inside_bar.py`, `hammer_hanging_man.py`, `doji.py`, `morning_evening_star.py`, `fibonacci_retracement.py` (legacy), `fib_golden_zone.py`, `ote_entry.py`, `ema.py` — one file per `detect_*` function
- **`_base.py`** — `ParamSpec`, `StrategySpec`, `SIGNAL_COLUMNS`
- **`_shared.py`** — `_find_bos_swing`, `volume_confirm`, `compute_ema`, `ema_cross_count`, `is_trending`, `_empty_signals`, `_signals_to_df`, `_fmt_time`
- **`_seasonality.py`** — `seasonality_stats`, `SEASONALITY_COLUMNS` (returns stats DataFrame, not signals)
- **`_registry.py`** — explicit-tuple-driven assembler holding `STRATEGY_REGISTRY` (21 entries), `DETECTOR_REGISTRY` (19 entries; `seasonality` / `smt_divergence` / legacy `fibonacci_retracement` excluded), `KNOWN_STRATEGIES`, `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS`, `INCOMPATIBLE_PAIRS`, `patch_confidence_scores`
- **`__init__.py`** — eager re-exports of every leaf + registry symbol; the public entry for callers
- 21 active strategies in `STRATEGY_REGISTRY`: `seasonality`, `wick_fill`, `marubozu`, `orb`, `liquidity_sweep`, `fvg`, `bos`, `smt_divergence`, `eqh_eql`, `order_block`, `cvd_divergence`, `trend_day`, `engulfing`, `pin_bar`, `inside_bar`, `hammer_hanging_man`, `doji`, `morning_evening_star`, `fib_golden_zone`, `ote_entry`, `ema` (`fibonacci_retracement` legacy/commented-out; `funding_extreme` exists as a function but isn't registered — needs a 2-arg signature, called directly)
- `StrategySpec.confidence: dict[str, int] | int` — use `get_confidence(tf)` (falls back to `"default"` key then `3`)
- `StrategySpec.tp_r_long/tp_r_short: float | None` — Gate 3 direction-split TP; use `get_tp_r(direction)` (falls back to 2.0)
- `StrategySpec.strategy_type` — one of `structural`, `fib`, `price_action`, `candlestick`, `flow`, `session`, `trend`
- `KNOWN_STRATEGY_TYPES`, `STRATEGY_TYPE_GROUPS: dict[str, list[str]]` — type → strategy names
- `INCOMPATIBLE_PAIRS` — blocks bos+fib_golden_zone, bos+ote_entry (both embed BOS internally)
- `SIGNAL_COLUMNS` includes `tp_price` — fib_golden_zone/ote_entry populate with 1.618 ext; others leave `0.0`
- `_candle_too_small(high, low, close, min_range_pct)` — filter for `(high-low)/close < min_range_pct`
- `min_range_pct: float = 0.0` added to all 6 candlestick detectors (default 0.0 = disabled); each has a `ParamSpec` in `STRATEGY_REGISTRY` for TOML tuning

## backtest_lib.py — pure backtest engine

- `Trade`, `BacktestResult`, `run_backtest`, format helpers
- Costs (P0b): `net_R = raw_R − fee_R − slippage_R − funding_R`. Fee + slippage drag are both `2 * pct * entry / risk` (slippage_pct ← `[backtest].slippage_bps`, default 2bps/leg); `funding_r` is precomputed at close. `min_sl_pct` widens SLs too close to entry
- Volume tiers: `_is_low_volume` (< 1.5× 20-candle mean) / `_is_volume_spike` (> 3× mean)
- `run_backtest(volume_suppress, volume_suppress_long/short=None, tp_r_long/short=None, atr_sl_floor=False, *, live_parity: LiveParityConfig | None = None, bias_cfg: BiasConfig | None = None, regime_series: pd.Series | None = None, strategy_params: dict[str, StrategyOverride] | None = None, htf_slope_series_by_anchor: Mapping[tuple[str,int,int], pd.Series] | None = None, slippage_pct: float = 0.0, funding_series: pd.Series | None = None)` — directional params take precedence over symmetric; `atr_sl_floor=True` widens structural sl_price via `max(structural_dist, atr_sl_multiplier × ATR14)` (no-op otherwise — every active strategy emits structural sl_price, which short-circuits the bare ATR branch). The first five keyword-only args are the T6 live-parity wiring: PR-1 plumbing; PR-2 regime gate (per-signal HTF regime lookup against `regime_series`); PR-3 ports two more gates — `direction_filter` is a pure per-event flag check on `strategy_params[strategy].suppress_long`/`.suppress_short`, and F8 `htf_ema` reuses the per-signal "last fully closed HTF candle" lookup (`_resolve_series_at` mirrors `_resolve_regime_at`) over the pre-computed slope series in `htf_slope_series_by_anchor` (keyed by `(anchor_tf, period, slope_lookback)`); PR-4 ports the ADR bias gate — `_apply_adr_bias_gate_to_signals` honours per-direction `_is_adr_exempt` so PR #380's `adr_exempt_long`/`adr_exempt_short` propagate into backtest replay, and the runner skips its legacy pre-filter when `live_parity.adr_bias=True` to avoid double-filtering. (The runner's pre-filter `_apply_legacy_adr_pre_filter` is itself now per-direction-aware via the Bucket C schema extension on `backtest_config.StrategyOverride`.) Each gate is guarded by `live_parity.is_on(...)` AND `bias_cfg.*_enabled` / `adr_suppress_threshold is not None` AND the relevant inputs being supplied. Default `None` for all is a true no-op; existing callers see zero behavioural change. PR-4b ports the conflict resolver via runner-level cross-strategy pooling (see `backtest_runner.py::_resolve_conflicts_for_signals_map`): the engine is unchanged because resolution requires the cross-strategy event pool, which `run_backtest` (per-strategy) does not have. PR-5 ports cooldown — `_apply_cooldown_gate_to_signals` walks signals in `open_time` order against a `_CooldownState` ledger keyed by `(symbol, tf, strategy, direction)`; the ledger is instantiated inside `run_backtest()` so each call has fresh state (per T6 plan Q1). Baked-in defaults: 15m=4, 1h=3, 4h=2, 1d=1 bars; overridable via `live_parity.cooldown_bars_per_tf` (TOML `[backtest.live_parity.cooldown_bars]`). **P0b honest costs:** the last two kw-only args — `slippage_pct` (per-leg price fraction, fee-shaped, applied to every `Trade`) + `funding_series` (funding_time-indexed `pd.Series`) — extend the block; `run_backtest` precomputes per-trade `funding_r` at close = `side_sign·Σrate·entry/risk` over the `(entry,exit]` window (long +1 pays / short −1 receives), and `Trade.pnl_r` deducts `net_R = raw − fee − slippage − funding`. Both default to a byte-stable no-op (`slippage_pct=0.0`, `funding_series=None`).
- `Trade.low_volume` / `Trade.volume_spike` tag volume tier per trade; both persist to `backtest_trades` (BOOLEAN columns) since 2026-05-15 — readable for gate-audit replay
- `BacktestResult` exposes: `low/normal/spike_vol_closed_trades` + `*_avg_r`; 6 directional×volume cross-tabs (`long/short_low/normal/spike_vol_*`)
- `format_volume_split()` — 3-way table; `format_directional_volume_split()` — ↑/↓ × Low/Normal/Spike
- Rolling detectors (fib_golden_zone, ote_entry, order_block, eqh_eql, cvd_divergence) fire at every historical candle; last-candle-only detectors fire at most once per run
- `BacktestResult` directional split: `long/short_closed_trades`, `long/short_win_count/rate/avg_r/total_r`
- `max_drawdown_r` (peak-to-trough) + `recovery_factor` (total_r / max_drawdown_r, 0.0 when no drawdown)
- **D10 same-TF**: `ComboBacktestResult`; `_find_cofire_signals` — greedy ±N-candle, same direction, each B once; `run_combo_backtest`; `format_combo_table`
- **D10 cross-TF**: `CrossTfComboBacktestResult(strategy_htf, strategy_ltf, tf_htf, tf_ltf, window_hours, result)`; `_find_cross_tf_signals` — LTF within `[ltf_time - window_hours, ltf_time]` of any same-direction HTF (no exclusivity); `run_cross_tf_combo_backtest`; `format_cross_tf_combo_table`

## backtest_runner.py — thin wrapper

- Opens DB, loads OHLCV/funding, calls indicator + backtest libs
- `run_digest_cmd(query, min_trades, top_n)` for CLI digest
- `run_combo_backtest_cmd(...)` — sweeps all symbol×TF×strategy-pair; skips `INCOMPATIBLE_PAIRS`; parallel via `ProcessPoolExecutor`; `_combo_worker` top-level (pickling); `workers=None` → `min(4, cpu_count-1)`; `workers=1` bypasses pool; symbols fall back to `coins.json`
- `_SWEEP_STRATEGIES` / `_non_seasonal` both exclude `("seasonality", "funding_reversion")`
- OHLCV cache in `_collect_sweep_results` — fetches once per `(symbol, timeframe)`. PR-4b restructures the function into three phases (detect → resolve conflicts → backtest+save); default-off branch is byte-identical because phase 2 is a no-op when `ratings_map is None`. Sibling helpers `_build_confidence_ratings_map(conn, cfg)` (loads `confidence_ratings.avg_r` keyed by `(strategy, tf, direction)` for the conflict resolver tiebreaker — returns None when the `conflict_resolver` gate is off) and `_resolve_conflicts_for_signals_map(signals_map, ratings_map)` (groups by `(symbol, tf)` then `open_time`, delegates to `_apply_conflict_resolver`, mutates the map in place). `run_backtest_sweep` builds the pre-compute caches (regime, HTF slope, ratings — all gated — plus the always-on funding series via `_build_funding_series_by_symbol`, P0b) once at the outer scope and threads them into `_collect_sweep_results` via kw-only args (`funding_by_symbol`, etc.) so the "loaded N ratings" log fires once per sweep, not per call. The 4 in-scope `run_backtest` call sites (sweep / tp-sweep / atr-sweep / `run_backtest_cmd`) pass `slippage_pct=cfg.slippage_pct` + `funding_series`; combo / cross-TF / `bt_cache` / `param_sweep` stay byte-stable (deferred).
- `run_cross_tf_combo_backtest_cmd(...)` — sweeps symbol × (tf_htf, tf_ltf) × all strategy ordered pairs; `_cross_tf_combo_worker` chunked by `(symbol, tf_htf, tf_ltf)`; `_DEFAULT_HTF_LTF_PAIRS = [("4h","15m"),("4h","1h"),("1h","15m"),("1d","4h"),("1d","1h")]`

## perf_timer.py

- `timed(label)` context manager — prints `[perf] label: Xs`; import via `from analytics.perf_timer import timed`

## regime.py

- `classify_series(df, timeframe) → pd.Series[str]` — labels each row as `trend`/`range`/`high_vol`/`unknown` per §6 of `docs/redesign/buibui-redesign.md`
- `high_vol` if ATR-14% ≥ 90-day rolling 80th-percentile; else `trend` if `|EMA-50 slope|` ≥ 0.5% over 10 bars; else `range`; `unknown` for rows lacking enough history
- Used by `tools/strategy_edge_audit.py` (Phase 0). Live as soft-mode gate since 2026-05-10 — wired into `run_scan_cycle` as Step −1 of the bias chain via `analytics/signal/gates.py::_apply_regime_gate`.

## exits/ — exit-policy research package (diagnostic-only)

- Exit spec: `docs/redesign/2026-06-05-exit-improvement-spec.md`; N2 ships §2 only — `policies.py` / `replay.py` land with the exit-sweep PR, gated on the N2 verdict (`docs/audits/2026-06-11-mfe-mae-diagnostic.md`)
- `mfe_mae.py::compute_excursions(conn) → pd.DataFrame[EXCURSION_COLUMNS]` — per-resolved-`signal_alert_outcomes`-row MFE_R/MAE_R (÷ |entry − sl|) over the actually-held window `candle_ts_ms < open_time ≤ outcome_filled_at_ms`; (symbol, tf)-grouped OHLCV fetch + `np.searchsorted` row windows (same batching as `backfill_outcomes`); zero-risk / missing-OHLCV rows dropped
- Conservative intrabar clamps (spec §4 adverse-first applied to measurement): loss exit bar's favorable extreme excluded from MFE; win MFE = `max(prior_fav, rr_ratio)` (no overshoot credit) but exit-bar adverse counts in MAE; expired counts both extremes of every bar; both floored at 0. Excursions are GROSS of costs (net lives in `outcome_r`)
- `mfe_mae.py::aggregate_cohorts(excursions, *, by=("strategy","tf","direction"), min_n=30)` — cohort (win/loss/expired) × cell table: `n`, `mfe_mean/p50`, `mae_mean/p50`, `reach_05`/`reach_10` (share with MFE ≥ 0.5R/1.0R), `tp_r_p50`, `bars_held_p50`, `outcome_r_mean`; `by=()` = overall roll-up
- CLI: `tools/exit_audit.py` (read-only diagnose mode; `--min-n`, `--csv`)

## param_sweep.py — WFO sweep lib

- `run_param_sweep(conn, strategy, symbol, tf, days, param_ranges, wfo_split, min_trades, fee_pct, top_n, adr_suppress_threshold=None, day_filter="off", atr_sl_multiplier=None, atr_sl_floor=False)` → `ParamSweepReport(rows: list[SweepRow], gate: CommitGateVerdict, n_grid: int)` — `atr_sl_multiplier`/`atr_sl_floor` forwarded to every grid `run_backtest()` call (F9 joint sweeps)
- **P0a-2 commit gate (`sweep_guard.py`):** `ParamSweepReport.gate` = `evaluate_commit_gate(chosen, all_trials, n_grid)`. The recommended (top non-overfit) row commits only if `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ n_obs ≥ MinTRL(0.95)`, computed over the **full grid pre-truncation** (N floored at grid size so top-N can't inflate DSR). `< 2` trials or `< 2·n_splits` trades → `INSUFFICIENT`. `format_sweep_results(rows, ..., gate=...)` renders the `COMMIT-GATE` stamp; `/wfo-sweep` + `/param-sweep-apply` hard-refuse non-`COMMIT` cells.
- **P0a-2 audit-tool gate (`audit_guard.py`, sub-PR 2):** `evaluate_audit_cells(cells, *, bar=0.05, alpha=0.05, min_n=30, ...)` → `list[CellVerdict]` (`ENABLE` / `DISABLE` / `CONCENTRATE` / `INSUFFICIENT`). Replaces the ±0.05R bar in `tools/gate_audit.py` + `tools/adr_threshold_audit.py` with two gates that BOTH must hold: a `block_bootstrap_ci` on the suppressed slice's mean R clearing the ±bar AND a `haircut_sharpe` Holm-adjusted p-value (over the tested-cell family) `< alpha`. Below-`min_n` cells → `INSUFFICIENT`, excluded from the family. Tools gain `--alpha` / `--n-boot` / `--seed` flags + `ci_lo` / `ci_hi` / `adj_p` columns.
- `run_strategy_audit(...)` → `list[AuditRow]` — same `atr_sl_multiplier`/`atr_sl_floor` kwargs as `run_param_sweep`; forwarded to each worker's `run_backtest()` (commit gate not yet wired here — deferred sub-PR)
- Applies `day_filter` before IS/OOS split — grades same population the live daemon sees
- `SweepRow` / `AuditRow` expose `long/short_oos_avg_r`, `long/short_oos_n` (Gate 3)
- `_directional_split_hint(row)` fires when |↑OOS − ↓OOS| ≥ 0.1R and n ≥ 3 each
- Parallelized: Phase 1 detects signals sequentially (needs DB conn), Phase 2 runs grid via `ProcessPoolExecutor` using `_sweep_grid_worker` / `_audit_strategy_worker` (picklable, take pre-computed DataFrames)

## digest_lib.py — aggregation over backtest_runs

- 12 pre-canned queries: `symbol`, `strategy`, `tf`, `combos`, `adr_ab`, `volume_ab`, `day_filter_ab`, `direction_bias`, `consistency`, `recovery_factor`, `co_firing`, `cross_tf_combos`
- `run_digest(conn, query, min_trades=None, top_n, scope)` — `min_trades=None` resolves via `_QUERY_MIN_TRADES` (default 5; co_firing/cross_tf_combos default to 3)
- `DigestScope(day_filter, fee_pct, symbols, min_trades, min_trades_per_tf)` — `_scope_clauses()` + `_min_trades_expr()` (per-TF CASE)
- `query_co_firing` deduplicates via `QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol, timeframe, strategy_a, strategy_b, window_candles, day_filter ORDER BY run_at_ms DESC) = 1`
- `query_cross_tf_combos` deduplicates by `(symbol, tf_htf, tf_ltf, strategy_htf, strategy_ltf, window_hours, day_filter)`
- Powers `GET /api/backtest/analysis?use_config=true` and `buibui digest` CLI

## cme_gap_lib.py

- `CMEGap(gap_low, gap_high, gap_up, filled)`
- `get_recent_cme_gap(ohlcv_df, _now_sec=None)` — most recent Fri 21:00–Sun 22:00 UTC gap
- `cme_gap_alert_warning(gap, direction, entry, tp_price)` — LONG: unfilled gap below entry; SHORT: gap in TP path

## zones_lib.py — structural zone extraction (geometry only, no trade signals)

- `extract_fvg_zones(df)` — bull/bear FVG boxes; `active=False` + `close_ms` when CE midpoint crossed
- `extract_order_block_zones(df)` — OB boxes; `active=False` + `close_ms` when mitigated
- `extract_eqh_eql_zones(df)` — EQH/EQL lines from swing pivots; `active=False` + `close_ms` at first wick-touch
- `extract_bos_zones(df)` — active (unbroken) + last 5 broken levels with `close_ms`
- `extract_fib_golden_zones(df)` — current 0.5–0.618 box from most recent BOS swing
- `extract_ote_zones(df)` — current 0.618–0.786 OTE box
- `extract_swing_points(df)` — recent 3-bar pivot highs/lows as dot annotations
- All return `list[dict[str, Any]]` with `zone_low/high`, `price`, `start_ms`, `close_ms`, `active`, `direction`; active zones first + 3–5 recent inactive
- Imports `_find_bos_swing` from `analytics.strategies` for Fib/OTE

## signal_lib.py — pure scan lib

- `scan_symbol()` — runs strategies on one symbol/tf
- `run_scan_cycle()` — 3-phase: Phase 1 pre-fetches all DB data sequentially (`funding_map`/`ohlcv_map`), Phase 2 fans out via `ThreadPoolExecutor` (pure pandas, GIL released; workers = `min(cpu_count-1, n_pairs)`), Phase 3 fan-in: cooldown/backtest/upsert sequentially
- `ohlcv_cache: dict[(symbol, tf), DataFrame] | None` — daemon hot path skips DB reads in Phase 1
- `confidence_override` (combined) + `directional_confidence_override` ({strategy: {tf: {direction: stars}}}) — directional takes precedence
- `_compute_backtest()` respects `fee_pct`, `day_filter`, `min_sl_pct`, `atr_sl_multiplier`, `atr_sl_floor`, `since`; label shows `since YYYY-MM-DD` when set. `atr_sl_floor` flows through to `run_backtest()` so the alert's backtest gate evaluates trades with the same widened SLs the live path would apply
- `run_scan_cycle(atr_sl_multiplier=None, atr_sl_floor=False, ...)` — `atr_sl_floor` enables the F9 live widener; Phase 3 calls `analytics/signal/atr_floor.py::_apply_atr_floor` on returned events before conflict/dedup/bias/DB writes, so persisted `signals.sl_price` and Telegram alerts use the corrected SL/TP. Per-strategy / per-symbol+TF overrides via `strategy_params.atr_sl_floor` + `atr_sl_floor_per_tf`; resolver `_resolve_atr_sl_floor` mirrors the `atr_sl_multiplier` hierarchy. `_backtest_run_id` keyed on `atr_sl_floor` so cached/persisted runs don't bleed across on/off
- `_excluded_from_registry = {"seasonality", "funding_reversion"}` — skip silently (no WARNING)
- `_filter_signals_by_adr(ohlcv_df, signals_df, threshold)` — directional: suppresses chasing direction (LONGs when close > range midpoint, SHORTs when close < midpoint)
- `_is_adr_exempt(strategy_params, strategy)` — bypasses live gate + `_compute_backtest` filter; stores NULL `adr_suppress_threshold`
- Volume gate: `_resolve_volume_suppress_long/short` — directional overrides → symmetric fallback; `SignalEvent.volume_spike` still tagged for analysis (no longer gates behaviour after the boost deprecation — see `docs/audits/2026-05-20-volume-spike-boost-structural-inertness.md`)
- `_compute_stats_context()` computes `StatsContext` once per cycle
- CME gap: `get_recent_cme_gap(ohlcv_df)` per (symbol, tf); passes `cme_gap_warning` to formatter
- **Same-TF co-fire**: `combo_lookup`, `combo_window=5`, `combo_min_avg_r=1.0`; `_find_live_cofire` checks same-cycle pairs + cross-cycle DB signals; attaches `ConfluenceData`
- **Cross-TF co-fire**: `cross_tf_lookup`, `cross_tf_pairs`, `cross_tf_window_hours=4.0`, `cross_tf_min_avg_r=1.0`; `_find_cross_tf_cofire` queries DB signals history for HTF; same-TF and cross-TF both evaluated — higher avg_r wins
- `_parse_htf_ltf_pairs(list[str])` — parses `["4h:15m", ...]` TOML strings

## stats_lib.py — pure stats lib

- `compute_p1p2_daily` → `P1P2Result` (incl. `p1_strong_pct`)
- `compute_hourly_extremes` (incl. `peak_high/low_hour_by_dow` per-DOW MODE)
- `compute_adr` → `ADRResult(adr_14, adr_30, today_range_pct, today_consumed_pct, today_move_up: bool | None)`
- `compute_dow_patterns` (incl. `avg_return_pct`, `strong_high/low_pct`)
- `compute_session_breakdown`, `compute_weekly_p1p2`, `compute_weekly_p2_timing` → `WeeklyP2Timing`
- `compute_weekly_flip_risk_conditioned` → `WeeklyFlipRiskConditioned`; p1_direction="low"=bullish, "high"=bearish
- `compute_all` → `StatsBundle`
- Live (never-cached) functions via `_inject_live_fields()`: `compute_weekly_current_state`, `compute_daily_distance`, `compute_weekly_wick_percentile`
- All times MYT (UTC+8): `(epoch_ms + INTERVAL 8 HOUR)::TIMESTAMP`; raises `ValueError` on empty data

## signal_runner.py — thin daemon wrapper

- Creates client, opens DB, syncs candles, polls `run_scan_cycle` in a loop
- All TOML params wired through: `sl_pct`, `cooldown_seconds`, `fee_pct`, `day_filter`, `bias_cfg`
- Loads `confidence_override` + `directional_confidence_override` from DB at startup
- **OHLCV cache**: `_update_ohlcv_cache()` re-fetches from `cached_max_ts` inclusive; replaces cache[-1] + appends new rows; invalidates when `>2` rows arrive (`_CACHE_INVALIDATE_THRESHOLD = 2`)
- **Combo refresh**: `combo_lookup` + `cross_tf_lookup` reloaded every `_COMBO_REFRESH_CYCLES = 10` cycles
- **T2 outcome backfill**: after each `run_scan_cycle`, calls `analytics/signal/outcome_backfill.py::backfill_outcomes(conn, now_ms, fee_pct=..., slippage_pct=...)` on the same write conn. Resolves `signal_alert_outcomes` rows where `outcome IS NULL` by walking OHLCV forward; mirrors the backtest engine's same-bar-tie-to-loss rule. Past `max_hold_bars` without TP/SL touch → `expired` with MTM `outcome_r`. **P0b PR-3:** resolved `outcome_r` is net of costs — `_net_outcome_r` replicates `Trade.pnl_r` (`net_R = raw − 2·(fee+slippage)·entry/risk − funding_r`); funding stamps read from `funding_rates` over `(entry, exit]` (long pays / short receives), empty table → 0.0 (e.g. OKX GH-Actions path); fee/slippage threaded from `BacktestFilterConfig`, kw-only defaults 0.0 keep raw behaviour for legacy callers. Failure logs but never blocks the cycle. The writer (`run_scan_cycle`) now persists a non-NULL SL/TP for **every** fired event via `_resolve_outcome_sl_tp` (structural SL when valid, else the alert formatter's `entry*(1±eff_sl_pct)` pct fallback floored by `min_sl_pct`), so all rows are scoreable — closing the prior ~89% NULL-`tp_price` hole that left `bos` (0/256) invisible. The pre-fix NULL backlog is reconstructable via `tools/backfill_null_tp_outcomes.py`.

## signal_test_runner.py

- `run_signal_test(symbol, timeframe, strategy, at_ms, lookback, ...)` — read-only, no DB writes, no cooldown
- `--at` pins to historical candle (Unix ms or ISO datetime); `--lookback` default 200
- `secondary_map: dict[str, str] | None` — loads secondary OHLCV for `smt_divergence`
- `buibui.py` builds secondary map from `coins.json smt_secondary` automatically

## signal_config.py — signal_watch TOML config loader

- `BacktestFilterConfig`: `fee_pct`, `min_sl_pct`, `min_avg_r`, `min_avg_r_long/short: float | None`, `since: str | None`
- Hard-mode gate uses `min_avg_r_long/short` when set, falls back to `min_avg_r`
- `SymbolOverride` — per-symbol tp_r/sl_pct/atr_sl overrides
- `StrategyOverride`: `tp_r_long/short`, `adr_exempt`, `volume_suppress` (symmetric + directional long/short + per-tf-direction)
- `SignalWatchConfig.effective_tp_r(strategy, symbol, tf, direction="")` — resolution: `symbol+TF → symbol → TF → directional → strategy → global`
- `effective_volume_suppress(strategy)` — per-strategy → global; directional variants return `bool | None`
- `BiasConfig` from `[bias]`: `adr_suppress_threshold`, `dow_soft_suppress`, `dow_suppress_min_abs_return`; F8 fields `htf_ema_enabled/mode/default_tf/default_period/default_slope_lookback/deadband_pct/per_strategy` (+ `htf_ema_anchor(strategy)` resolver); **`suppress_directions`** scopes which signal directions F8 may suppress when a signal opposes the HTF EMA slope — precedence: per-strategy `per_strategy.<name>.suppress_directions` → global `[bias.htf_ema].suppress_directions` → built-in `("long","short")` (symmetric); `[]` = full exempt (signal always passes); omitting the key entirely reproduces the original symmetric behavior (back-compat). Production config ships `mode = "soft"` (log-only) with global `["long"]` (suppress counter-trend longs only — the 2026-06-01 IS ablation found counter-trend shorts win on most strategy families); flow family (`cvd_divergence`, `smt_divergence`) is exempt (`[]`); fib family (`fib_golden_zone`, `ote_entry`) keeps symmetric (`["long","short"]`); hard-mode flip is OOS-gated (use `tools/htf_ema_gate_replay.py --oos-frac 0.3` to validate before flipping); regime fields `regime_enabled/mode/htf_tf/enabled_regimes/per_strategy` (+ `regime_allowed(strategy, strategy_type, regime)` resolver — `unknown` regime + unmapped types fall open)
- `ComboConfig`: same-TF `window=5`, `min_avg_r=1.0`; cross-TF `cross_tf_pairs`, `cross_tf_window_hours=4.0`, `cross_tf_min_avg_r=1.0`
- `_deep_merge` + `_load_toml_with_extends` — config may declare `extends = "strategy_params.toml"`

## backtest_config.py — backtest sweep TOML loader

- `BacktestSweepConfig`: `min_sl_pct`, `liq_sweep_use_fib`, `volume_suppress`, `since: str | None`
- `is_adr_exempt(strategy)` — strategy-wide flag accessor (legacy shim); `effective_adr_exempt(strategy, direction, tf=None)` resolves the Bucket C per-direction overrides — precedence is per-tf-direction (`adr_exempt_long_per_tf[tf]` / `adr_exempt_short_per_tf[tf]`) → per-direction (`adr_exempt_long` / `adr_exempt_short`) → strategy-wide (`adr_exempt`). Used by the runner's per-direction split helper `_apply_legacy_adr_pre_filter(cfg, strategy, tf, ohlcv, signals)`, which threads `tf` so a single (tf, direction) cell can flip without dragging other tfs along.
- `StrategyOverride` mirrors signal_config: `adr_exempt`, `adr_exempt_long/short`, `adr_exempt_long_per_tf` / `adr_exempt_short_per_tf`, `volume_suppress` (+ `_long/_short`), `tp_r_long/short`
- `effective_tp_r` / `effective_volume_suppress` / directional variants — mirror signal_config resolution
- `strategy_timeframes` / `strategy_timeframes_long` / `strategy_timeframes_short` — populated from the same TOML the live daemon reads (via `load_signal_config(path)` so the parser is single-source-of-truth). `effective_strategy_timeframes(strategy, direction)` resolves Q-BC-2 additive narrowing (per-tf-direction → per-direction → base list). Consumed by `_collect_sweep_results` + `_collect_signals_map`: base allowlist hard-skips the (symbol, tf, strategy) cell before detection; directional layer applied via `_apply_strategy_timeframes_directional_filter(cfg, strategy, tf, signals)` post-detection to drop signal rows whose direction is restricted. Combo + cross-TF workers (`_combo_worker`, `_cross_tf_combo_worker`) iterate all strategies for confluence and are deferred to a follow-up.
- Same `_deep_merge` + `_load_toml_with_extends` as signal_config.py

## recalibrate_lib.py / recalibrate_runner.py

- `get_backtest_win_rates(conn)` → DataFrame with combined + directional columns
- `compute_recalibrated_ratings(conn, min_trades)` → `dict[str, dict[str, int]]`
- `compute_directional_ratings(conn, min_trades=5)` → `{strategy: {tf: {"long": stars, "short": stars}}}`
- `compute_dsr_ratings(conn, day_filter=None, adr_suppress_threshold=None, min_trades=MIN_DSR_TRADES)` → `{strategy: {tf: {"combined"|"long"|"short": dsr}}}` (P0a-2 sub-PR 3). Pools `backtest_trades.pnl_r` over the same latest-run-per-(strategy, tf, symbol) set the ratings use (shared `_build_run_filter` scoping), computes each cell's per-trade Sharpe, and deflates it against the per-pass cell family via `research_guards.deflated_sharpe_ratio` (N = per-pass cell count = an N-floor on the true search → optimistic). `MIN_DSR_TRADES = 30` gates family membership + DSR computation (a tiny-n low-dispersion cell's extreme Sharpe would otherwise inflate the cross-trial variance and collapse every DSR to ~0); `DSR_SUSPECT_THRESHOLD = 0.95`
- `write_confidence_to_db(conn, config_name, ratings, win_rates, day_filter=None, directional_ratings=None, dsr_ratings=None)` — `dsr_ratings` sliced per direction into `upsert_confidence_ratings(dsr_map=)` → the `confidence_ratings.dsr` column
- `format_recalibration_report(..., dsr_ratings=None)` appends a "⚠ Suspect (★≥4 but DSR<0.95)" line
- `write_confidence_to_source` — legacy: patches `analytics/strategies/_registry.py` directly
- Runner: `--config <toml>` derives `day_filter`, `config_name`, `adr_suppress_threshold`; `--apply` writes to DB (with config) or source (without config)
