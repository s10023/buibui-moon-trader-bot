# Signals Package Reference

Detailed reference for `signals/`. Load this when working on alert formatting, cooldown, or the signal registry.

## registry.py

- `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` — 20 actionable strategies
- Excluded: `seasonality` (inactive by design), `funding_reversion` (no live feed, partial DB), `fibonacci_retracement` (legacy)
- `confidence` field removed — resolved per-TF at dispatch via `STRATEGY_REGISTRY[name].get_confidence(tf)`

## cooldown_store.py

- Two-layer dedup:
  1. Candle watermark per `(symbol, tf, strategy, UTC weekday of the candle)` —
     prevents re-firing same candle
  2. Cooldown timer per `(symbol, strategy, direction)` — time-based suppression
- JSON-persisted to `signal_state.json`
- `last_marked(symbol, tf, strategy, open_time) -> int | None` — the raw
  watermark for that candle's weekday scope, or `None` if the scope has never
  fired. Backs the catch-up cold-start guard; `is_new_candle` cannot serve that
  role because it answers `True` both for a genuinely missed candle and for a
  key with no history. It takes `open_time` so the guard resolves the **same**
  scope it protects.
- **Why the key carries a weekday (SoT N8, fixed 2026-08-07).** The three
  signal-watch configs partition the week with zero overlap — `{Mon,Fri}` /
  `{Tue,Wed,Thu}` / `{Sat,Sun}` — and the picker chooses by *today's* UTC
  weekday while `day_filter` gates on each *candle's* weekday, so a candle is
  only scannable on a day whose config admits it. With a flat 3-part key the
  watermark is one strictly monotonic number: miss Monday, and Tuesday's own
  fires drag it past Monday, so by Friday — the next day admitting Monday — the
  candle sits below the watermark and is refused forever, logged only at
  `debug`. A day self-heals iff the **next** day runs the **same** config
  (Tue→Wed, Wed→Thu, Sat→Sun); Mon/Thu/Fri/Sun each *end* a block and were
  permanently lossy. Because the loss bit only keys that fired in between, it
  preferentially deleted the highest-activity cells — day-of-week **shaped**
  bias on an axis that is itself a live conditioning gate, i.e. a skewed sample
  rather than a thinner one.
  **Scoping by CONFIG would not have worked:** Mon and Fri share `mon_fri`, so a
  Friday fire would still bury Monday. The scope must be the weekday.
- **Legacy state is seeded, not discarded.** `_migrate` expands any 3-part key
  into all seven weekday scopes at the same value, so day-one behaviour is
  identical to pre-fix. Discarding instead would leave every key cold and the
  cold-start guard would clamp each to the latest candle — a week of degraded
  catch-up for no gain. Idempotent, so it is safe on every load. Verified
  against the real 176-key file: 176 → 1232 scopes, every legacy watermark still
  suppressing its own candle.
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
- **Bounds:** recovery depth is capped by `scan_window(tf)`, which is **200 bars
  for every timeframe except 15m, where it is 600** — ~6.25 days on 15m, ~8 on
  1h, ~33 on 4h, ~200 on 1d.
  **The window is the REACH of catch-up, not just a performance knob**, and that
  is why 15m is widened (2026-08-07). N8 recovery gaps run 3 days (Fri→Mon) to 6
  (Sun→next Sat); at a flat 200 bars 15m reached only **2.1 days**, short of even
  the shortest gap, so no 15m boundary candle could ever be replayed no matter
  how the watermark was keyed. 15m is **64.4% of the live ledger** (2,846 of
  4,422 rows), so fixing the watermark alone would have addressed 35.6% of the
  affected volume while presenting as a complete fix.
  Measured cost of the widening: 46ms → 107ms per symbol (2.31×), and only
  BTC/ETH/SOL carry 15m, so ~+0.2s against a ~20s median cycle in a 900s budget.
  **Other timeframes are deliberately not widened** — they already clear 6 days,
  and unneeded width is data loaded every cycle for nothing.
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

1. Header — strategy/stars/reason (+ the detector's `context`)
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
- **`_restate_context_tp` points a detector's baked `TP=` claim at the number the Levels block prints, and the Levels block is the one authority (SoT ST81).** Ten detectors render `TP=<n>` into `context` at DETECTION time, and three things recompute the traded target after that string exists: `analytics/signal/atr_floor.py::_apply_atr_floor` widens a tight structural SL and rebuilds TP from it, `_apply_min_sl_floor` widens SL again at render time, and **seven of those ten set no `tp_price` at all**, so §3 derives TP from `sl_dist × tp_r`. That last one diverged on EVERY fire with no floor involved — `fibonacci_retracement` advertised the swing high (`TP=130.00`) beside a Levels block reading `104.28`. Restating at render time rather than editing the ten detectors keeps one authority, so a detector added later cannot reintroduce it; stored `context` is never rewritten. ⚠ **Only the number moves** — `swing_high=…`, a trailing `(1.618 ext)` and everything else is the target's PROVENANCE and stays. ⚠ **This is a PRESENTATION fix, not a levels change:** whether a detector's structural target should flow into `tp_price` and become the traded TP is a separate behaviour question, untouched here.
- `_adr_bar(consumed_pct)` — 10-char ASCII bar with `▓` overflow
- `_format_stats_line(ctx, direction)` — direction-aware; line 1: `📐` bull%/P1/ADR; line 2: `🎯` TP window/weekly timing
- Same-TF confluence renders `> ⚡⚡ CONFLUENCE`; cross-TF renders `> ⚡⚡ CONFLUENCE (4h → 15m)`
- `orderflow_signals` is a step-5 extension point for CoinGlass/NPOC lines

---

## Package map (moved out of the always-loaded tier 2026-08-04; index in `AGENTS.md`)

- `signals/` — signal detection daemon package (alerting + dedup only — detection lives in `analytics/`). See `.claude/context/signals.md` for full reference.
  - `registry.py` — `SignalPlugin` TypedDict + `SIGNAL_REGISTRY` (20 actionable strategies; `seasonality` / `fibonacci_retracement` excluded)
  - `cooldown_store.py` — two-layer dedup: candle watermark + cooldown timer; JSON-persisted to `signal_state.json`. `last_marked()` returns the watermark or `None` — it backs the N6 catch-up cold-start guard, which `is_new_candle` cannot serve because that collapses "genuinely missed candle" and "key has never fired" to the same `True`
  - `alert_formatter.py` — `SignalEvent`, `StatsContext`, `ConfluenceData`; 6-section alert layout; W1–W8 candle warnings
  - `DEFAULT_DB_PATH` lives in `analytics/store/_common.py` (re-exported from `analytics.store` and `analytics.data_store`) — import from a re-export, do not redefine in runners. It is **not** in `schema.py`; `from analytics.store.schema import DEFAULT_DB_PATH` raises `ImportError`
- `card/` — F2 AI trade-card package (spec `docs/superpowers/specs/2026-07-08-f2-trade-card-design.md` + 2026-07-11 addendum): `config.py` (`CardConfig.from_toml` `[card]` block, incl. `live_window_days` — lookback for the per-fire live record, **default 60**, `0` = all time as an explicit opt-in. `outcome_r` only became net of costs 2026-06-11 (#432) and resolved rows were never restated, so an all-time window mixes bases by ~0.06R — which exceeded some cells' entire live edge, producing citations that were true and evidentially empty (BTC's card cited a cell at +0.036R all-time; the same cell over 30d is −0.325R). Note the window is *relative* while the contamination boundary is *absolute*, so 60d self-cleans over time; 30d would be clean at once but thins n below the `live_n≥10` floor the live-beats-backtest rule needs. Also `timeout_s` — **480.0**, raised from a 180.0 that sat below the fastest real card, so both attempts timed out and every card returned nothing while burning ~6 min; a test asserts headroom over the measured 349 s worst case. And two opt-in reasoning knobs, both defaulting to CURRENT behaviour: `max_thinking_tokens` (`None` = leave the env unset) and `restrict_tools` (`False`). Measured 2026-08-05 on one real prompt, restricting the toolset — the intuitive latency lever — bought **zero** (245.6 s → 254.6 s), while `max_thinking_tokens = 0` gave **30.7 s, 8.0×, output 21,705 → 2,040**. They are opt-in because that card was good on the sample (8/8 spot-checked citations exact, nothing invented) but n=1, and its verdict differed from baseline in a way one sample cannot separate from model variance; tool restriction is still worth enabling alongside for determinism (4 turns → 1) and input cost (~200K → ~25.7K), just not for speed. Reachable as `make buibui-card ... CONFIG=<path>`), `state.py` (`snapshot_market_state` — composes brief bundle (panel incl. indicators/sessions, session_clock, PunditBoard) + XS target row + recent fires **dual-annotated** from `confidence_ratings` (backtest star, via the additive `get_confidence_rating_rows` getter) AND `analytics/stats/live_outcomes.py::compute_live_outcomes` (live `live_n`/`live_avg_r` on the same (strategy, tf, direction) cell — cross-symbol for parity with the star, which pools symbols; no `combined` fallback on the live side because the ledger stores a real direction, so a miss means no history) + injected `AccountProvider`; per-block failure isolation; `state_digest` sha256. **`_strip_censored_pundit_stats` removes `avg_r` + `r_coverage` from every pundit cell — `authors`, `families`, AND the prior nested inside each `recent_calls` row, the third place it hides — before the payload leaves `to_dict()`.** `avg_r` needs a stated stop and winners disproportionately lack one (WIN coverage 43% vs LOSS 79%), so it is a winner-dropped subsample: on 2026-08-12 three authors compressed to an identical −1.0 against a true `avg_atr_r` of −0.334 / −0.502 / −1.331, meaning a consumer ranking on it gets the ORDER wrong, not merely the level, and all three cards in that batch cited it. **Stripping here rather than from `PunditBoard` is deliberate — the Brief renderer and the web board DO show `avg_r`, correctly paired with its denominator, and a pundit's own stated risk is a real question; only the card loses it, because only the card has no reader to weigh it.** The strip is scoped to the pundit board: `recent_fires.avg_r` is a DIFFERENT metric sharing the name (the backtest star over simulated trades, which all have stops) and rubric 3a depends on it, so a test asserts it survives. **Note this changes `state_digest` — digests are comparable only within one payload shape.** The account block resolves capital via the shared `portfolio.sizing.resolve_capital` (live equity when finite and positive, else the configured `[portfolio] capital`) before deriving `daily_r` for the circuit breaker, and a non-positive risk unit (capital × `r_base`) fails open to `daily_r=0.0` with a `daily_r unavailable: non-positive risk unit` health note rather than silently reading as a flat day. **`recent_fires` is bounded at `now_ms - tf_ms` — a bar CLOSE, not a bar open.** `signals` rows are keyed by `open_time` and the daemon writes one only after the bar closes, so the old `<= now_ms` bound admitted a bar still forming at the anchor the moment its row landed: a card dated T citing a fire only knowable after T. Measured 2026-08-05 at anchor `12:28:03Z` — an `eqh_eql/1h/short` row with `open_time` 12:00Z (closing **+32 min** after the anchor) was absent from a 12:58Z compose and present at 13:15Z. **`account_skip_reason`** carries WHY the block is absent, because a deliberate `--as-of` omission and a credentials failure both leave `account=None` and the generic "no provider (degraded)" note cannot tell them apart; `cli/card.py::_account_provider_for` is the one place that decides), `prompt.py` (`PROMPT_VERSION="card-v5"` — **no longer byte-stable; this entry claimed `card-v3` + "byte-stable rubric" until 2026-08-12, and both halves are now false**; M4 deltas: panel.external in the liquidity map + confluence (capped 1 input) + style block; card-v3 adds step 3a: the live record beats the backtest star on conflict at `live_n≥10`, a low-star cell with a positive live record is a genuine agreeing input, and a null `live_n` is no evidence either way — never read as a bad record. **card-v4 adds step 3b and a band clause in step 2. 3b names `avg_atr_r` the only per-author pundit outcome and states its units are ATR, not R — it pairs with the payload strip below, because the strip alone would leave the model reading an ATR figure on an R scale. The step-2 clause states a liq cluster is a BAND whose edges reproduce to only ~16% on a same-input re-extraction (mean drift 20–43% of band width, measured 2026-08-12), so an entry/SL/TP placed on a cluster edge is a rubric violation even when the cluster is real; intensity IS reliable. The version bump is what makes cards comparable within a rubric — `prompt_version` is stamped on every `ai-cards.jsonl` row.** **card-v5 (2026-08-20) inserts step 4, a four-angle steelman (htf counter / underweighted confluence / catalyst risk / the other trader) that runs BEFORE the decision, renumbering the old 4/5 to 5/6.** It is a REQUIRED `TradeCard.steelman`, validated at exactly `_STEELMAN_ANGLES` = 4 non-empty bullets on a TRADE and permitted absent on a NO_TRADE — a pinned count is what makes a skipped angle FAIL rather than read as a card that argued all four, which is the same reasoning as `FinalCard.horizon` having no default. It reaches `ai-cards.jsonl` free via `to_dict`'s `asdict`, and nothing in the tree reads that file back, so the v4/v5 field break costs no consumer (`pundit_row`'s 12 keys are untouched). **`card/telegram.py` is deliberately unchanged and a test pins the phone card carrying NO steelman** — four more prose bullets against the 4096-char guard re-open the exact readability defect the medium-specific layout fixed. v5 also extends the Style block to cover `steelman` and bars a JSON field path from generated prose (`range_state.pos 0.4955` becomes "price sits mid-range on the daily"), which is what made the reasoning read as a JSON dump; the anti-invention requirement to cite a concrete number survives it. Rubric provenance: @TraderMorin 2026-07-25, `docs/plans/mechanics-backlog.md`, whose stated non-goal (the steelman is not there to talk you out of the trade) is carried into the rubric because without it a disconfirmation step aimed at a negative book drifts into a veto. **ST12 SHIPPED 2026-08-12i: `--horizon intraday|swing`.** The horizon block is appended in `build_prompt` BESIDE the rubric, not baked into it, so `RUBRIC` stays byte-stable and the version pin keeps meaning something; it states the scoring window (48h vs 30d), moves `expected_hold` into days for swing, and tells the model to discount a short-`window` external snapshot. `CardConfig.horizon` is constrained to `CARD_HORIZONS` — a strict subset of `VALID_HORIZONS` that EXCLUDES `unspecified`, because that key scores on 14 days and a card always knows its own horizon; a test binds the subset to `WINDOWS_MS` so a member added without a window cannot fall through silently. `fires_timeframes` now defaults to `None` and resolves through `resolved_fires_timeframes` (swing = `('4h','1d')`, dropping 1h; **never 1w — no detector runs there**), with an explicit config value always winning), `client.py` (`LLMClient` protocol + `ClaudeCliClient` subprocess backend — env-strip + personal config dir + temp cwd are load-bearing. `restrict_tools` adds `--strict-mcp-config` + `--disallowed-tools` over `_ALL_TOOLS`; `max_thinking_tokens` sets `MAX_THINKING_TOKENS` in the child env, and only when not `None`, so the shipped default leaves the CLI's own behaviour untouched. `_strip_fences` takes the FIRST fenced block anywhere in the reply, not only a fence at position 0 — the old form destroyed a whole card whenever the model prefixed any prose, measured 2026-08-05 when an apology for a `ToolSearch` call preceded a complete, schema-valid card and it died in `json.loads`; historical opening-fence strip retained as fallback for an unterminated fence), `card.py` (`TradeCard` validation + `post_pass` deterministic sizing/hard rules → `FinalCard`; `post_pass(qty_step=)` floors the quantity to the symbol's exchange LOT_SIZE step via the shared `portfolio.sizing.round_down_to_step` and restates `risk_usd`/`risk_frac` from the **rounded** size, so the printed risk is the risk actually taken — flooring only ever moves risk below budget; a budget under one lot vetoes rather than silently sizing to zero, and an absent step (exchange unreachable) keeps the raw quantity but adds an explicit warning. Sizing capital is resolved via `portfolio.sizing.resolve_capital` — live account equity when finite and positive, else the configured `[portfolio] capital` — and `FinalCard` carries `capital_used`/`capital_source` (`"live_equity"` | `"config"`, both cleared to `None` on a VETO); a config fallback appends a warning that the risk fraction is against a constant, not the account. A `valid_until_utc` that is unparseable or does not postdate `generated_at_ms` vetoes — checked against the card's own generation time, not wall-clock, so re-reading an old card does not retroactively void it; the `min_rr` floor gates on `rr_tp1_net` (gross minus `portfolio.sizing.round_trip_drag_r`), both numbers carried on `FinalCard` and named in the veto reason — a gross floor passed exactly the tight-stop cards it should reject, since the drag carries `entry / risk`; + `_live_negative_fires` — deterministic warning when a cited cell is live-negative AND readably worse than backtest, deduped per cell, `_LIVE_MIN_N=10` / `_LIVE_NOISE_R=0.15` mirrored in the rubric so code and prompt cannot drift. **Display only** — a veto would promote the live ledger into a gate, which needs the n≥30 the golden-signal loop is blocked on), `run.py` (orchestrator, one re-ask), `ledger.py` (ai-cards.jsonl + TRADE-only pundit-calls dual-write), `render.py`, `telegram.py` (`--telegram` / `TG=1` message body. **The medium gets its own layout — it does NOT reuse `render_card`**, because the card holds two content types with opposite wrapping needs and one wrapper cannot serve both: aligned numbers go INSIDE `<pre>` (Telegram collapses space runs and the columns break) while reasoning prose goes OUTSIDE it (`<pre>` never soft-wraps, so a paragraph forces horizontal scroll in small monospace and buries entry/SL/TP/size). Operator verdict on the first `render_card`-verbatim send, 2026-08-18: "not human friendly / readable" — third instance of [[green-gates-are-blind-to-rendering]] and the first on a NON-BROWSER surface, so the ask-for-a-screenshot mitigation did not apply. Escaping is `quote=False`: `utils.telegram` posts `parse_mode=HTML` and Telegram decodes only `&lt;` `&gt;` `&amp;`, so `html.escape`'s default turns every apostrophe into a literal `&#x27;` on the phone — caught on the second real send. A 4096-char guard drops reasoning bullets rather than losing the message. The headline's long/short badge is imported from `alert_formatter.DIRECTION_LABELS` rather than restated — duplicated formatting across two renderers that mirror each other is exactly how the Brief/CLI `avg_r` pair diverged. Pure formatting; `cli/card.py` owns the send, after the ledger write and gated on the flag, so an exploratory or batch card never reaches the phone unasked. Pushes on EVERY verdict — a veto is how the daily-R breaker trip and the sub-lot capital wall become visible there), `errors.py`; `cli/card.py` builds the real Binance provider and fetches the LOT_SIZE step through the executor's own `BinanceFuturesAdapter.get_filters` (`mode="dry_run"` — a pure read; failure degrades to `None`, never raises). Advisory only — no order routing.
- `portfolio/` — P1 paper-portfolio package (sizing + replay; spec `docs/redesign/2026-06-05-p1-sizing-spec.md`). Pure libs over a DuckDB conn (no live risk, no schema changes): `sizing.py` (`SizingConfig` frozen dataclass + `from_toml` + Carver two-layer math — `vol_governor`/`regime_multiplier`/`effective_risk_fraction`/`cluster_of`/`apply_caps`; defaults: capital 10k, r_base 0.25%, 20% vol-target, 2% concurrent-risk cap, 1% majors-cluster cap. **`__post_init__` rejects every degenerate numeric field at construction** — `capital`/`r_base`/`vol_target_annual`/`annualization_days`/`vol_window_days` must be finite and positive, the caps and governor bounds finite and non-negative, `g_vol_min ≤ g_vol_max`, and `bool` is refused everywhere because it is an `int` subclass and `capital = true` would otherwise size the book against $1. These fail SILENTLY without the guard, which is why it is at construction rather than at each use: `capital = -5.0` yields a **positive, entirely plausible TRADE card** (`round_down_to_step` returns the magnitude by contract and the sign is never re-applied), and a `nan` propagates through `risk_usd`/`risk_frac`/`notional_usd` without raising, surfacing only *after* the paid-for LLM call. `dataclasses.replace` re-runs it, so `from_toml` and `cli/portfolio.py`'s `--capital`/`--vol-target` overrides are covered without their own checks. **Smallness is not degeneracy** — a $50 account is honoured, the same contract `resolve_capital` keeps for live equity. The guard NARROWS but does not close the non-positive risk unit path that `card/state.py`'s health note covers: `capital = 5e-324` is positive and finite, clears the guard, and still underflows `capital × r_base` to exactly 0.0 — so that note is live defence, not dead code, and `tests/test_card_state.py` pins it through that route), `book.py` (`LedgerTrade`/`SizedTrade`/`BookResult`/`PaperBook` — single causal forward pass over the entry-ordered ledger; live concurrent + cluster caps; **causal** vol governor reads trailing realized vol of the compounding curve strictly before each entry; marks open positions to **dual-basis** daily MTM curves — fixed-notional headline + compounding governor-feedback), `metrics.py` (sharpe/sortino/max_drawdown/calmar/annual_return/annual_vol/avg_exposure/risk_turnover/attribution; √365 annualization; degenerate curves → 0.0 not NaN), `replay.py` (`replay_ledger` — the only DB-touching module: resolved `signal_alert_outcomes` + 1d OHLCV close-alignment + 1d regime labels → `PaperBook`. **Restates the pre-`e5d92bb` half of the ledger onto the net-of-cost basis** via `restate_gross_r`, because `outcome_r` changed basis at #432 (2026-06-11) and the backfill never rescored already-resolved rows — replaying the mixture averages the book across an accounting change. Keyed on `outcome_filled_at_ms`, never `candle_ts_ms`: the causal clock is the one recording the event being attributed (a code deploy), not the observation. `restate_cost_basis=False` reproduces the old mixed figure. ⚠ The costs are MODELLED — a stop-out's raw component is still exactly −1.0, the *declared* risk — but since ST68 that is a MEASUREMENT, not a missing mechanism: both books price a gapped fill via `analytics/backtest/fills.py`, and **0 of 3,028** resolved rows have ever gapped (100% join coverage, closest approach **+0.0072R INSIDE** the level), because crypto's 24/7 tape keeps the open continuous with the prior close. Read it as *no gap has occurred here*, never *no basis can express it*), `report.py` (`format_report` terminal renderer). Driven by `cli/portfolio.py` + `make buibui-portfolio-replay`. Baseline verdict: `docs/audits/2026-06-14-p1-portfolio-baseline.md` (Sharpe −0.53; caps throttle 92% of the single-majors-cluster alert stream — sizing does not rescue the −0.12R/alert edge). Exit-policy replay (sub-project B) is a follow-up that reuses this `PaperBook`.

## Live-daemon config (moved out of the always-loaded tier 2026-08-04; index in `AGENTS.md`)

- `config/strategy_params.toml` — shared base config inherited via `extends = "strategy_params.toml"` by `signal_watch.toml`, `signal_watch_all.toml`, `signal_watch_weekdays.toml`. Contains `[smt_pairs]`, `[bias]`, `[backtest]` defaults (incl. P0b `slippage_bps = 2.0` per-leg → `slippage_pct`), per-strategy `volume_suppress` flags, and `tp_r_long` / `tp_r_short` directional overrides. `[bias.htf_ema].suppress_directions` scopes which signal directions F8 may suppress (global list + per-strategy `per_strategy.<name>.suppress_directions` overrides); production ships `["long"]` (soft mode) with flow family exempt (`[]`) and fib family symmetric (`["long","short"]`).

- `config/coins.json` — per-symbol leverage and stop-loss config (gitignored; see `coins.json.example`)
- `config/universe.toml` — committed 25-perp research universe (N3): criterion string + symbol list; loaded via `analytics/universe.py::load_universe`, refreshed via `tools/select_universe.py` (reviewed diff, never auto-written)
