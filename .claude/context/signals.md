# Signals Package Reference

Detailed reference for `signals/`. Load this when working on alert formatting, cooldown, or the signal registry.

## registry.py

- `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` — 20 actionable strategies
- Excluded: `seasonality` (inactive by design), `funding_reversion` (no live feed, partial DB), `fibonacci_retracement` (legacy)
- `confidence` field removed — resolved per-TF at dispatch via `STRATEGY_REGISTRY[name].get_confidence(tf)`

## cooldown_store.py

- Two-layer dedup:
  1. Candle watermark per `(symbol, tf, strategy)` — prevents re-firing same candle
  2. Cooldown timer per `(symbol, strategy, direction)` — time-based suppression
- JSON-persisted to `signal_state.json`
- `last_marked(symbol, tf, strategy) -> int | None` — the raw watermark, or `None`
  if this key has never fired. Backs the catch-up cold-start guard;
  `is_new_candle` cannot serve that role because it answers `True` both for a
  genuinely missed candle and for a key with no history.
- **Watermark-on-send** (ported from wifey #68): the watermark is stamped only
  after a *successful* live Telegram dispatch, not unconditionally at scan time.
  Previously a run without `--telegram` consumed the candle and the next real
  run silently skipped it — the alert was lost permanently. Backfilled
  catch-up candles are the deliberate exception: they mark unconditionally,
  because they are recorded as ledger evidence and never dispatched.

## Missed-candle catch-up (`--catch-up`, SoT N6)

Off by default; the default path stays byte-identical (one candle group).

- **Why:** `scan_symbol` fires on the latest closed candle only, so a cycle that
  never runs loses those signals forever. That makes the live ledger — the OOS
  evidence base — a *biased* sample, not just a smaller one: the hourly
  GH-Actions cron measured 35% delivery, and its run-hour distribution was
  non-uniform at p<0.01 (Asia 0.70x, Off-hours 1.62x).
- **What it does:** `scan_symbol(catch_up=True)` emits every closed candle in the
  window, each at its own close price. `run_scan_cycle` Phase 2b then splits the
  result into one pseudo-result per candle `open_time`, so conflict resolution
  and confluence stacking stay per-candle correct.
- **Alerting asymmetry:** only the newest closed candle may reach Telegram.
  Older recovered candles are persisted + watermarked silently.
- **Cold-start guard:** a key with no watermark is restricted to the latest
  candle, so a fresh `signal_state.json` cannot burst 200 candles on first run.
- **Bounds:** recovery depth is capped by the 200-candle `_SCAN_WINDOW` —
  ~8 days on 1h, ~33 on 4h, ~200 on 1d.
- **Caveat (load-bearing):** regime / HTF-EMA / ADR / DOW bias and
  `confidence_ratings` are evaluated **as-of-now** and applied to historical
  candles. Fine for a few missed days; a backfill reaching past a ratings
  refresh is look-ahead in the *gating* and should be read as backtest output,
  not clean OOS evidence.

## alert_formatter.py

### Dataclasses

- `SignalEvent`: `tp_price: float` (structural TP from detector; `0.0` = use `tp_r` fallback), `volume_spike: bool` (> 3× rolling mean), `confluence_combo: ConfluenceData | None`
- `StatsContext`: `adr_move_up: bool | None`, `wk_low/high_still_ahead_conditioned_pct: float | None`, `wk_move_bucket: str | None`
- `ConfluenceData`: `co_strategy`, `candles_ago`, `avg_r`, `trades`, `win_rate`, `type_a`, `type_b`, `orderflow_signals: list[str]`, `htf_tf: str = ""`, `ltf_tf: str = ""`

### Alert layout (6 sections)

1. Header — strategy/stars/reason
2. Entry — price/time/session
3. Levels — SL/TP
4. Warnings — all notes consolidated (silent unless triggered)
5. Edge — backtest summary + confluence blockquote
6. Context — stats lines

### Warning helpers (`_build_candle_warnings`)

- W1 `_is_marubozu` — both wicks ≤ 10% of body
- W2 `_has_equal_levels` — equal lows below → LONG warn; equal highs above → SHORT warn (liquidity sweep likely)
- W5 `_wick_rejection_against` — wick > 40% of range against signal direction
- W6 `_has_consecutive_candles` — 3 candles same direction (overextension)
- W7 `_is_doji` — body < 10% of range (takes priority over W1)
- W8 `_is_inside_bar` — signal inside prior candle range
- Volume spike/low-volume moved from header into warnings block

### Other

- `format_signal_alert()` / `format_confluence_alert()` — both accept `ohlcv_df: pd.DataFrame | None` (signal candle = last row) + `cme_gap_warning: str | None`
- `_adr_bar(consumed_pct)` — 10-char ASCII bar with `▓` overflow
- `_format_stats_line(ctx, direction)` — direction-aware; line 1: `📐` bull%/P1/ADR; line 2: `🎯` TP window/weekly timing
- Same-TF confluence renders `> ⚡⚡ CONFLUENCE`; cross-TF renders `> ⚡⚡ CONFLUENCE (4h → 15m)`
- `orderflow_signals` is a step-5 extension point for CoinGlass/NPOC lines
