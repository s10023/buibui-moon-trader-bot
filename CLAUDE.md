# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Working Agreement (persona · quality gate · anti-drift)

**Persona.** You are a senior quant-systems engineer. Bias to de-biased, out-of-sample evidence (DSR / PBO / MinTRL / avg_r across regime × session × combo) over in-sample optimism. Never commit an overfit parameter. Report negative-EV findings honestly — a strategy that loses is a result, not a failure to hide.

**Definition of Done (a gate, not a habit).** A Python change is not "done" until, and you must state each result plainly (if a step was skipped or failed, say so — do not claim green without running it):

- `make lint-py` ✓ (ruff format + lint)
- `make typecheck` ✓ (mypy strict)
- `make test` green
- `make test-regression` goldens unmoved — **required only when the diff touches the backtest surface**: `analytics/backtest/`, `analytics/strategies/`, `analytics/signal_config.py`, `config/*signal_watch*.toml`, `config/strategy_params.toml`, `tests/fixtures/`, or `poetry.lock`. Outside that set it is ~95s of wall clock for a chain the diff cannot reach — it was paid twice in one session for a `tools/video_marks.py` change. Say which branch you took. When it does apply and a golden moves, that is a *decision* (regenerate or not), which is exactly why it stays local instead of being left to CI — learning it ~7 min later on an open PR is strictly worse.

**Anti-drift.** Before any multi-step task, restate the goal + its success metric in one line. If a step stops serving that metric, stop and ask rather than drift. Require avg_r × (regime × session × combo) evidence before killing a strategy — demote, don't delete.

**Token efficiency.** Skills are dormant until invoked — don't load what you don't need. Use the context-mode `ctx_*` tools for any command/output over ~20 lines. `/compact` proactively at logical boundaries (don't wait for autocompaction). Delegate heavy reads/long analysis to a subagent only when the saved main-context clutter outweighs the startup cost.

**Model delegation policy.** The main thread (Fable) is the orchestrator/tech lead — design, judgment, review, and routing stay here; don't burn main-thread quota on bulk mechanical work. Delegate down by tier: **sonnet** subagents for high-volume execution with a self-contained inline brief (vision extraction, file sweeps, boilerplate, test triage); **haiku** for trivial one-shot lookups; **opus** subagents only as a quota escape valve for long *parallel* research — Opus sits below Fable in capability, so this conserves limits, it does not buy better thinking. Every subagent brief must be drift-proof: goal + success metric + rubric inline, no SoT/memory re-reads. Verify subagent/background work directly (`ps`, `journalctl`, `git status`) — self-reports can be stale.

**Guardrail.** A PreToolUse hook (`.claude/hooks/guard-destructive.py`) blocks catastrophic Bash (rm -rf, git reset --hard, force-push, DB wipes). If blocked, do not work around it silently — surface it.

## Project Overview

Buibui Moon Trader Bot — a crypto trading bot for Binance Futures. Live price + position monitoring, an analytics/backtest stack (DuckDB), a 20-strategy signal engine with Telegram alerts, and a FastAPI + Svelte web UI. Python 3.11+, managed with Poetry.

## Key Commands

After making **any** Python code change:

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite
make test-cov       # same suite + coverage report (on demand; not a gate)
```

For Markdown changes: `make lint-md`.

After adding a doc to `docs/audits/` or `docs/superpowers/specs/`: `make docs-index`
(both `INDEX.md` files are generated, and `tests/test_docs_index.py` fails until they
are current).

**`make lint-py` also rewrites Markdown.** It runs `ruff format .`, and ruff formats
python code fences *inside `.md` files* — so any plan or spec doc carrying a python
fence is reformatted on every Python task, showing up as unrelated churn in the diff.
When a doc with python fences is in play, land that reformat once up front rather than
letting each task hand-revert it.

For UI / API changes: `make web-build` (production bundle) or `make web-dev` (Vite dev server).

For routine DB refresh after backtest/strategy changes: `make db-update` (= `db-update-backtest` → `db-update-recalibrate` → `regression-update`).

## CLI

`buibui.py` is the single CLI entry point with subcommands:

- `buibui monitor price | position` — live price / position monitor
- `buibui signal watch | test` — live signal daemon / historical replay. `watch` with no `--config` auto-picks today's config by **UTC weekday** (Mon/Fri→`signal_watch_weekdays.toml`, Tue–Thu→`signal_watch.toml`, Sat/Sun→`signal_watch_all.toml`); the three configs partition the calendar without overlap. **Two names in that list mislead, and both cost a re-derivation on 2026-08-07: `signal_watch_all.toml` carries `day_filter = "weekend"` — it is Sat/Sun ONLY, not "all days" — and `signal_watch_weekdays.toml` carries `"mon_fri"`, which means Mon *and* Fri, not Mon-through-Fri.** Read the `day_filter`, never the filename. The partition is `{Mon,Fri}` / `{Tue,Wed,Thu}` / `{Sat,Sun}`, and that shape is what makes N8's boundary loss land on exactly the 4 days each block ends on. UTC (not local) so the picker matches the `day_filter` scope on each candle's UTC `open_time`. `watch --once` runs a single scan cycle and exits (cron / GitHub Actions entry). `watch --catch-up` (SoT N6, off by default) replays every un-alerted **closed** candle since the last run instead of only the newest, so a missed or skipped cycle no longer loses those ledger rows permanently — the live ledger is the OOS evidence base, and gaps in it are a *biased* sample, not merely a thinner one (the hourly GH-Actions cron measured 35% delivery with a session-skewed run-hour distribution, p<0.01). Backfilled candles are persisted + watermarked but **never sent to Telegram** (a signal that old is not tradeable); only the newest closed candle can alert. A cold-start guard (`CooldownStore.last_marked()`) stops a fresh `signal_state.json` bursting the whole window on first contact. Recovery depth is bounded by `scan_window(tf)` — **200 bars everywhere except 15m, which is 600** (~6.25 days on 15m, ~8 on 1h, ~33 on 4h, ~200 on 1d). **The window is catch-up's REACH, not merely a perf knob**, which is why 15m was widened on 2026-08-07: SoT N8's recovery gaps run 3–6 days, a flat 200 bars reached only 2.1 days on 15m, and 15m is 64.4% of the live ledger — so the N8 watermark fix would otherwise have covered 35.6% of the affected volume while looking complete. **SoT N8 is FIXED (2026-08-07):** the dedup watermark is now keyed `symbol:tf:strategy:UTC-weekday`, so a missed boundary day is no longer buried by a later weekday's fire. Scoping by *config* would NOT have worked — Mon and Fri share `mon_fri`. Legacy 3-part state is seeded into all seven scopes on load (idempotent), never discarded. Gating context (regime / HTF-EMA / ADR / DOW / `confidence_ratings`) is computed **as-of-now** and applied to historical candles — a best-effort approximation good for days, not months, so deep backfills past a ratings refresh are look-ahead in the *gating* and should be treated as backtest, not OOS. `CATCH_UP=1` on `make buibui-signal-watch`. `DATA_SOURCE=okx` env selects the keyless OKX adapter (`utils/okx_client.py`) instead of Binance — used by `.github/workflows/signal-watch.yaml`, which seeds an ephemeral `analytics.db` from the committed slim `live_signal.duckdb` (`make export-live-db`); local default `DATA_SOURCE=binance` is unaffected
- `buibui analytics backfill | sync` — OHLCV ingestion; `--universe` (mutually exclusive with `--symbols`) reads the committed 25-perp research set from `config/universe.toml`; `make universe-backfill` wraps the deep 1h/4h/1d/1w run since 2019
- `buibui backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `buibui digest` — pre-canned analytics queries
- `buibui param-audit | param-sweep` — WFO parameter tools
- `buibui recalibrate` — refresh star ratings
- `buibui portfolio replay` — replay the live outcome ledger through the Carver two-layer sizing model into a paper portfolio (read-only); prints Sharpe/Sortino/max-DD/attribution. Wrapped by `make buibui-portfolio-replay` (`CONFIG=`/`CAPITAL=`/`VOL_TARGET=` overrides)
- `buibui brief` — daily market brief (levels/zones/regime/seasonality/pundit board; `make buibui-brief`)
- `buibui card SYMBOL` — AI trade card (F2): composes brief panel (incl. M1 indicators + M2 sessions) + pundit board + XS target + recent fires + live account into a MarketState, sends the card-v3 rubric (M4: external heatmap/liq-map clusters as mapped liquidity w/ trust guards, capped at ONE confluence input; humanizer style block for generated prose; card-v3: each fire carries its live-ledger record alongside the backtest star, and live wins on conflict at n≥10) to `claude -p` (subscription auth, keys stripped, CLAUDE_CONFIG_DIR=~/.claude-personal, bare temp cwd), then a deterministic post-pass sizes the trade (P1 sizing reuse; the quantity is floored to the symbol's exchange LOT_SIZE step via `portfolio.sizing.round_down_to_step` — shared with `trade/routing.py` so card and XS router round identically — and `risk_usd`/`risk_frac` are restated from the ROUNDED size, so the printed risk is the risk actually taken; a sub-lot budget vetoes, and an unreachable exchange degrades to an unrounded quantity **with** a warning) and enforces hard rules in code (VETOED on violation, including a `valid_until_utc` that is unparseable or does not postdate the card's own `generated_at_ms`). Sizing resolves capital via `portfolio.sizing.resolve_capital` — **live account equity when available, the configured `[portfolio] capital` otherwise** — and every card records `capital_used` / `capital_source`, because once capital is live a bare `risk_frac` is uninterpretable after the fact. A pinned `--as-of` run omits the account by design and therefore always takes the config path, with a warning. The same resolved capital scales `daily_r`, so the daily circuit breaker is measured in real R: against the old `10_000.0` constant its R unit was `$25` while the account's was `$3`, and a true −2R day passed the gate as −0.24R. One consequence of the smaller real capital: the sub-lot veto (above) goes from a corner case to a common one — at ~$1,200 equity a BTCUSDT stop wider than roughly 2.7% now VETOes for a risk budget under one lot, the same capital wall the XS sleeve hits at ~$1,000 minimum. Every card appends to gitignored `docs/plans/ai-cards.jsonl`; TRADE cards dual-write a pundit-calls row (author `buibui_card`, horizon `intraday`) so `make buibui-pundit-score` scores the AI with zero scorer changes; the pundit board (`analytics/brief/pundit.py`) excludes these `source:"ai-card"` rows (and drops `buibui_card` from the priors authors list) so the card is never an external pundit to itself. `--dry-run` prints state + prompt with no LLM call. **`--as-of` pins the INPUTS, not the card**: it admits only bars that had CLOSED at the anchor (`recent_fires` is bounded on bar close, and the weekly path drops the still-forming bar) and omits the live account, which is unpinnable because Binance serves only current positions/equity — the state records a health note saying which. The model stays nondeterministic, so two runs on a byte-identical `state_digest` have returned opposite directions; a pinned anchor makes a comparison possible, never conclusive at n=1. Wrapped by `make buibui-card SYMBOL=BTCUSDT [DIRECTION=] [AS_OF=] [DRY=1] [CONFIG=]`.
- `buibui web` — start FastAPI backend

Each Makefile `buibui-*` target wraps the equivalent CLI invocation — except
`buibui-backup`, which wraps `deploy/backup-analytics.sh` (verified local snapshot of
`analytics.db` + the ledger files in `LEDGERS` + the directories in `LEDGER_DIRS`;
`WEEKLY=1` adds the parquet export, `DRY=1` reports only). **`analytics.db` is gitignored
and single-copy, and the committed `live_signal.duckdb` is NOT a backup — its
`signal_alert_outcomes` table has 0 rows.** **Everything in both arrays is likewise
gitignored and single-copy, so those two arrays ARE the only copy** — which is why an
audit on 2026-08-08 expanded them from 4 entries to 10 files + 3 directories, a
second on 2026-08-11 to 17 files + 7 directories, **and a THIRD on 2026-08-12 to 18
files** — `config/pundit_roster.toml`, whose 16 author entries are accumulated operator
alias rulings that no re-derivation recovers without re-watching every video (the
committed `.example` carries 2 schema-demo entries and is not a backup). The originals covered Stream C and
nothing else; the entire ingest pipeline's state was uncovered. **The recurrence is the
real lesson: an allowlist over a single-copy tree defaults to UNCOVERED, so a new
artifact is invisible until someone diffs the backup against the live tree — which is
how BOTH audits found their gap, and the only way to find the next one.** Cost never
kept anything out: the 2026-08-11 additions total ~300KB against a 264MB snapshot.
They were `daily_check.py` (the health-check system itself),
`next-conversation-prompt.md` (the handoff), `regime-log.jsonl` (append-only and
unreconstructible — it holds the first regime turn this system ever dated) and
`task-marks/`, which is the same watermark class as the three below: a missing marker
reads as OVERDUE, so losing it re-presents every weekly cadence at once. The three
watermark/dedup ledgers (`yt-feed-state.json`,
`routed-ledger.json`, `.cache/chart-drops/processed.json`) are the subtle ones: losing
one destroys no past data but silently changes future behaviour — consumed videos
re-present, re-ingests double-write, handled chart drops re-ingest. **`processed.json`
lives under `.cache/`, the one directory every cleanup treats as disposable**, so a
plain `rm -rf .cache/` resets chart dedup with no other trace. Add to `LEDGER_DIRS`, not
`LEDGERS`, for a directory: the file loop is `[ -f ]`-guarded and skips a directory
silently, which is how these went uncovered in the first place.
A systemd user timer runs the snapshot twice daily. The **off-machine** leg is
`deploy/backup-offsite.sh` (`rclone sync` of `$BUIBUI_BACKUP_ROOT`), installed separately
because `rclone config` is interactive: it **exits 1 while `BUIBUI_BACKUP_REMOTE` is
unset**, so an enabled-but-unconfigured timer complains daily instead of looking green
while no off-machine copy exists. It uses `sync`, so remote retention tracks local
retention — and therefore **mirrors deletions**, which is why it refuses to run when no
`MANIFEST.json` exists under the backup root rather than syncing an empty tree over the
remote. A fifth timer, `buibui-daily-check`, pushes `docs/plans/daily_check.py
--exit-on-tier2` to Telegram **once daily** (09:10 UTC; the 15-minute cadence belongs to
signal-watch, which does *not* push); that flag exists because tier-2 lines (chart-drops,
external-context) do not set exit 1 on their own. A hand-run check is unaffected and
still exits 1 only on tier 1. **Since 2026-08-10 it also carries `TELEGRAM_ALWAYS=1`, so
the report arrives EVERY day, green or red.** The old failure-only contract ("silence =
healthy") was unfalsifiable: a dead timer and a healthy day looked identical on the
phone, and the delivery path was only ever exercised on a red day. `TELEGRAM_ALWAYS` is
**opt-in per job** in `run-job.sh` precisely so the 15-minute signal-watch does not
inherit it and send 96 messages a day. Both push paths now HTML-escape the body and wrap
it in `<pre>` — that is a bug fix, not cosmetics: `utils/telegram.py` sends
`parse_mode=HTML`, and an unescaped traceback (`line 33, in <module>`) was rejected 400,
so the failure alert failed on exactly the crashes it exists to report. `deploy/README.md`
has install, retention, restore and log commands.

## Project Structure

Package-level map. **The deep reference lives in `.claude/context/`, and the pointers
below are the only path a session has to it — follow them before working in an area.**
Verdicts and footguns stay HERE on purpose: they are the guard rail against
re-litigating settled research, and a guard rail behind a pointer is not a guard rail.

| Package | What it is | Deep reference |
| --- | --- | --- |
| `buibui.py` · `cli/` | Thin CLI entry shim delegating to `cli.main:main`; argparse subcommand package (`monitor` / `signal` / `analytics` / `backtest` / `digest` / `param` / `recalibrate` / `web`) | — |
| `analytics/` | Analytics data layer (DuckDB): `store/`, `strategies/`, `backtest/`, `signal/`, `stats/`, `brief/`, `exits/`, `research_guards/` + the audit libs | `context/analytics.md` |
| `analytics/{forecast,xsmom,combine,carry,xsrev,cvd}/` | The P2/P3 research sleeves — **verdicts below** | `context/research-sleeves.md` |
| `signals/` · `card/` · `portfolio/` | Alerting + dedup daemon; F2 AI trade card; P1 paper-portfolio sizing | `context/signals.md` |
| `web/` | FastAPI backend + Svelte 5 / Vite UI | `context/web.md` |
| `trade/` · `deploy/` · `monitor/` · `utils/` | Execution layer (XS live wiring + overlay), 24/7 VPS deploy kit, live price/position monitors, shared utils | `context/execution.md` |
| `tools/` | One-shot analysis + audit scripts; not part of the daemon or CLI surface | `context/tools.md` |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `config/` | `coins.json` (gitignored), `universe.toml` (committed 25-perp research set), `strategy_params.toml` (shared base inherited via `extends`) | — |

### Sleeve verdicts — do NOT rebuild a shelved sleeve

| Sleeve | Verdict |
| --- | --- |
| `xsmom/` cross-sectional momentum | **+1.375 Sharpe, DSR 0.997, PBO 0.295 — CLEARS the gate. THE DEPLOY CORE.** Alpha not beta; persistent (2021 was a losing year); capacity-green at operator scale |
| `forecast/` EWMAC trend | +0.36 — structurally real but FAILS the gate (CI includes 0). **SHELVED**, and the regime-conditional escape hatch is now CLOSED by measurement (2026-08-06, below) |
| `combine/` trend×XS | +1.145, clears the gate but is **Sharpe-dominated by XS-solo**. A validated socket awaiting a comparably strong second edge |
| `carry/` funding carry | +0.03, FAILS, not cost-robust past 8bps. **SHELVED** |
| `xsrev/` XS reversal | **−2.9, negative even at ZERO cost. SHELVED** — the additivity thesis is falsified, momentum continues down to 2–7d |
| `cvd/` spot-perp CVD divergence | **ALL 10 pre-registered trials FAIL. SHELVED** (2026-08-11, verdict `docs/audits/2026-08-11-d1-spot-perp-cvd.md`). XS −0.147 (PBO **0.849**, overfit); TS +0.183 with PBO passing at 0.316 but DSR 0.532 and boot_lo −0.642. Best single trial TS span8 +0.304, still short. **The informative part: `corr_to_xsmom` is −0.04/−0.08, so it WAS decorrelated from the deploy core — the failure is missing signal, not redundancy.** Panel 22, not 23 (TONUSDT spot is `BREAK`). **Daily-bar completeness is now MEASURED — residual exactly 0.0 against real 15m klines — so do not re-open the resolution question.** The one live re-entry point is intraday *sequencing*, which a daily design cannot see |

**P2 §6 per-regime attribution — RUN 2026-08-06, verdict NO** (verdict
`docs/audits/2026-08-06-p2-ewmac-regime-attribution.md`, tool
`forecast_audit.py --regime`). EWMAC's trend Sharpe does **not** demonstrably
concentrate in trend regimes. Raw split looks like a win (instrument-day Sharpe
+0.094 unconditional → +0.244 in trend, complement −0.332) and **all of it dissolves
under the cross-section correction below**; there is **no dose-response** (Sharpe flat
across a 16× range of slope thresholds); the book-day view shows nothing (high_vol
+0.431 ≈ trend +0.372, both t<1); and an *optimistically* built regime-gated book
(no re-entry cost charged) gains +0.102 Sharpe on a difference CI of [−0.459, +0.715].
**Do not rebuild this as a gated variant.** The shelving rationale had rested on this
unrun check — it is now run.

**CRITICAL — pooling symbol-days across this universe inflates every t-stat ~2.92×.**
Mean pairwise correlation of per-instrument net returns is **0.315**, so 25 perps carry
the noise reduction of **2.92 effective independent series, not 25**. A naive t over
41,571 pooled symbol-days reads t=2.21 on a cell whose corrected value is **+0.757** —
significant-looking noise. This is the same defect family as the H15 `bar`-units trap
and H8's missing gate leg: **a number that looks portable and silently changes meaning
with the panel.** `analytics.forecast.effective_independent_series` computes the
deflator; `RegimeCell.t_stat` is already deflated and `t_stat_naive` is kept beside it
so the adjustment is auditable. **Any audit that slices this universe per-symbol
inherits this — including the XS sleeve's per-symbol cuts.** Book-day (already
aggregated) rows must NOT be deflated again.

**Ensemble/confluence scoring — FAILS the gate (2026-08-11, verdict
`docs/audits/2026-08-11-ensemble-walkforward.md`, spec
`docs/superpowers/specs/2026-08-11-ensemble-walkforward-design.md`).** "Combine the
failed hypotheses into a confidence score" is a natural idea, it will recur, and it has
now been run: DSR **0.7030** against 0.95. PBO (0.235) and boot_lo (+0.053) both pass,
so this is not a null — the effect simply cannot be separated from the best of 16
searched constructions. **The decisive number is a sign flip: the target cell is
+0.1546 R/day and its trailing-window twin is −0.1723 — same axes, same sizing map,
only the window differs**, so the effect belongs to the expanding-window fit rather
than to the score. **The 2026-08-08 half-split headline (+0.310 top bucket) was
look-ahead**: it split on `fired_at_ms`, which admits alerts fired-but-unresolved
(median lag 23h, tail 310h), and under a causal refit keyed on
`outcome_filled_at_ms` that bucket reads **+0.0130**. Do NOT wire it into
`portfolio/sizing.py`. The one clean re-entry is the single pre-registered
construction re-tested on book-days accruing after 2026-08-11 — re-running the same
16 cells on a longer ledger is not independent evidence.

**The binding constraint, confirmed five times** (exits, trend-weight, combine, carry,
reversal): the system needs a second *strong* edge, and the cheap price-only free-data
levers are exhausted. A new sleeve must carry genuinely new information. Separately,
**conditioning axes are 6-for-6-plus-one-amended** (regime/session/combo/direction,
H14's Coinbase premium, and H15's USD/JPY carry-unwind are clean NOs; H8's M1 axes are
the amendment) — the diagnosis has moved off conditioning and onto the signal book.
**H15 (2026-08-04, verdict `docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md`): NO-EDGE
/ INSUFFICIENT on every cell in every panel. The primary forward panel (BTCUSDT
vol-normalised daily return, n=200–2055, well powered) cleared neither the significance
nor the effect-size leg on any cell — a powered null, not an underpowered one. H15 is
the second cross-asset axis tested, after H14 (a genuinely different data source, not a
re-slice of price/order-flow already held); both came back clean NOs, which sharpens the
standing conclusion — the binding constraint is unchanged and the next edge needs
genuinely new data, not a cleverer re-slice of what's already held.** **H8 AMENDED
2026-08-04 (code fix PR #546, verdict
`docs/audits/2026-07-24-h8-m1-indicator-conditioning.md`): its original NO was produced
by a build in which AVOID could essentially never fire and the pre-committed MinTRL leg
was missing. The re-run finds 25 backtest AVOID cells, 17 long-side, where the published
table had zero — price *location* gates BOTH directions (one continuation effect whose
long half was unreportable). This is NOT a sixth conditioning win: 7 of the 25 are exact
binary-axis mirrors of a BUILD cell, the price-location axes are ~1 effective finding not
25, and live is not independent of the backtest. Indicator *character* remains a NO.**

**CRITICAL — `analytics/store/_common.py::_upsert`** uses explicit `conn.register` /
`conn.unregister` in try/finally. Never switch to the implicit replacement scan (it
causes malloc heap corruption) and never drop the try/finally.

**`DEFAULT_DB_PATH` lives in `analytics/store/_common.py`** (re-exported from
`analytics.store` and `analytics.data_store`) — import from a re-export, never
redefine it in a runner. It is **not** in `schema.py`; this entry said so until
2026-08-04 and the wrong path fails as an `ImportError` on first use.

**CRITICAL — `portfolio/sizing.py::round_down_to_step` snaps before it floors, and
the snap is load-bearing. Do NOT "simplify" it back to `floor(q/step)*step`.** That
naive form is float-fragile: `0.29 / 0.01` computes as `28.999999999999996`, floors to
28, and returns `0.28` — a **full lot step** lost on a mathematically exact multiple.
The call site that makes it bite is `trade/routing.py`, whose
`delta_qty = target_qty - current` is a *difference of two step multiples* and so is an
exact multiple every time: **25–27% of router deltas** were shaved at steps 0.001/0.01/0.1
(step 1.0 is immune — integers are exact). Worse, a delta of exactly one lot floored to
**zero** 27–65% of the time, which `routing.py:117` turns into `skip:noop` — the order is
never sent and the position never converges. Fixed 2026-08-06. **The tolerance is capped
below a half step on purpose**; widening it would round a genuine sub-step remainder UP
past an exchange filter, flipping the helper from fail-safe to fail-open. **Test by
enumerating the input class, never by spot-checking** — the pre-fix spot-checks
(`0.0571951498512928`, `12.5`) all passed while the defect stood.

**CRITICAL — `deflated_sharpe_ratio` and `min_track_record_length` are
DIRECTIONAL.** Both answer "is this *positive* performance credible": DSR of a
raw negative Sharpe collapses to ~0, and MinTRL of one is `inf`. So **any audit
with a negative-direction verdict** (AVOID / CONFIRMED-BAD / REVERTING) **gated
on either metric must fold the Sharpe to `abs()`** — target *and* trial values.
Skipping this does not fail loudly; it makes that verdict **structurally
unreachable**, so the audit silently reports "no negative effect found" no matter
what the data says. This shipped in H8 and H14 and stood for weeks in H8
(`analytics/indicator_condition.py`, PR #546: a reliably-negative cell scored DSR
**0.0000** against **0.9980** for its mirror-image positive cell). Disclose the
cost when you do it: folding to magnitude shrinks trial dispersion in a
mixed-sign family, so the gate becomes marginally **more permissive** than the
signed form — bias runs toward more passes, never fewer.

**CRITICAL — an audit gate's effect-size floor `bar` is expressed in the units of
the observation, and nothing in its name or docstring says so.** H15
(`docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md`) is the worked example: H14's
`bar = 0.05` meant 0.05R because its observation was per-day mean trade R. Applying
that same numeral to a raw BTC daily-return panel (std 3.23%/day) would have demanded
a 5%-per-day mean shift to clear the gate — ~70× the unconditional mean, making every
verdict structurally unreachable. That is the H8 missing-gate-leg defect class in a
new location: a threshold that looks portable because it is a bare number, but
silently changes meaning across panels. H15 avoided it by vol-normalising the forward
panel (`return_t / causal trailing-30d vol`) so `bar` means the same sigma-units
quantity in both the forward and ledger panels.

**A pre-committed gate leg that the code never implements is invisible.** H8's
spec §7 required `n >= MinTRL(0.95)`; the code never had it, the verdict doc
never mentioned it, and every published BUILD cell cleared a gate missing a
pre-registered condition. Greps cannot find this class of defect — only reading
each spec against its implementation can. **STOP HAND-MAINTAINING THIS COUNT —
read `docs/superpowers/specs/INDEX.md`, which derives it from disk** (generated
by `make docs-index`, kept honest by `tests/test_docs_index.py`). A spec counts
as reconciled there when a *spec-reconcile audit* names its filename.
**The denominator is the FULL corpus**, decided 2026-08-06: a spec with nothing
to reconcile still costs someone a look to confirm that, and an
"implemented-only" denominator needs a per-spec judgement call nothing records.

**The derived floor on 2026-08-11 was 3 of 50, against the "6 of 46" this file
carried — BOTH numbers wrong, denominator included.** Four specs had landed
since the 46 was written, and of the six claimed, only P2-EWMAC,
P3-trend×XS-combine and P3-XS-momentum have an audit that records the reconcile
(`docs/audits/2026-08-06-spec-reconcile-*.md`). **H8 and the ingest-video design
doc were reconciled but nothing wrote it down**, so no tool can see them — which
is the actual lesson: a reconcile that produces no audit did not happen as far as
the next session is concerned. Treat the index as a **floor**; it cannot see a
partial reconcile as partial either.

**This counter has been wrong every single time anyone has checked it — 4 for
4.** It read "1 of 44" while the handoff said "3 of 44" and the corpus was 46;
PR #566 corrected it to 5 of 46 while listing **"xsmom"** as reconciled *and*
naming `p3-cross-sectional-momentum-sleeve-design.md` as "the highest-stakes one
still unchecked" — two claims in one paragraph that cannot both be true.
**Resolved 2026-08-06: that entry was PR #549, a PARTIAL reconcile of this very
spec** — it checked one leg (§Causality), found the guard vacuous, fixed it, and
never walked the rest. Both of #566's claims were half-right.
**Treat this number as a claim to verify, never as a fact to quote.** Two rules
follow: **name the FILE, not the sleeve** ("xsmom" is ambiguous across SEVEN spec
docs), and **a partial reconcile is not a reconcile** — the counter has no notion
of partial credit, so a one-leg entry silently becomes a whole-spec one, which is
exactly how the deploy core was listed as done while topping the to-do list.

**The deploy core is now reconciled and came back CLEAN** (2026-08-06): every
pre-registered gate leg and all 7 construction steps are implemented as written —
the H8/P2 failure mode did **not** recur on the sleeve that carries capital. Its
one defect runs the other way: **§Causality's pre-registered test is
unsatisfiable as literally written** (it asserts no book return changes on days
`< d+1`, but the day-`d` return is `leverage_d × r_d` and `r_d` depends on
`close_d` by construction — measured Δ 0.924 at day `k`, 0.000 before it). The
code asserts on the **position** instead, which holds to machine zero.
**AMENDED IN THE SPEC 2026-08-06** — §Causality now asserts on the position across
all columns and records why the old wording was unsatisfiable. Never "repair"
working P&L to satisfy that old wording; a book return that did not respond to its
own bar's close would be the real bug. (§Verdict criterion 4 was amended in the same
pass: "`corr_to_trend` near zero" was cleared at **+0.37** and is restated as the
disqualifier it always was in practice — no verdict changes.)

**The published gate is THREE legs, not four — `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧
boot_lo > 0`.** All five sleeves implement exactly this; `min_trl` is computed
and printed as a stamp but gates nothing. **It is now ONE function —
`analytics.research_guards.passes_gate` (2026-08-08) — so the rule below is
structural rather than remembered.** Both `combine_gate_verdict` and the new
`xs_gate_verdict` delegate to it, and the thresholds live only there; they were
previously private to `analytics/combine/report.py`, which is why a fourth
hand-inlined copy had already appeared in `xsmom`'s capacity table. If you need
a gate verdict, call it — do not restate `0.95` / `0.5` anywhere.
`corr_to_trend` is likewise NOT a leg: the deploy core cleared at **+0.37**, and
P3 §Verdict criterion 4 was amended 2026-08-06 to the human-read disqualifier it
always was, so coding it as a pass condition would fail the sleeve that carries
capital. **P2's spec §1/§6 said "MinTRL gated"
and were wrong — AMENDED 2026-08-06** to state the three-leg gate and describe
MinTRL as a reported stamp — and the deploy core would not clear a MinTRL leg (needs ~7035
obs to confirm Sharpe > 1.0 at 95%, has ~2475), which its own audit discloses
honestly at `2026-06-16-p3-xsmom-sleeve.md:105`. Keep the three-leg gate; MinTRL
against a non-zero target asks "can I confirm Sharpe ≥ 1", a far harder question
than "is there an edge". Do not quote the four-leg form.

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
- Run: `make test` or `poetry run pytest tests/ -q`
- **Coverage** is not part of `make test` and nothing gates on it (no codecov, no `fail_under`) — run `make test-cov` on demand
- **Regression tests**: `make test-regression` — compares backtest pipeline output to golden JSON files in `tests/fixtures/`; skips if fixture parquets are absent; run `make regression-update` to regenerate golden files after intentional changes
- **Never diagnose golden drift from a bare `pytest tests/`.** `pyproject.toml` sets a global `timeout = 30`, and the three `test_regression.py` golden backtests take ~97s — so a bare run reports them as *timeout failures* that look exactly like real drift. `make test-regression` is the gate and passes `--timeout=300`; `make test` doesn't run them at all (`--ignore=tests/test_regression.py`). This burned two separate reviewers on false alarms in a single session

## Dependencies

- Managed via Poetry: `poetry install --no-root`
- Runtime: `duckdb` (analytics DB), `pandas` (DataFrames), `pyarrow` (parquet fixture I/O), `yt-dlp` (`/ingest-video` metadata + captions + media), `yt-dlp-ejs` (YouTube JS challenge solver — see below)
- System (not Poetry-managed): `ffmpeg` — `/ingest-video` frame extraction + audio chunking. Absent ⇒ every frame grab fails and the skill records a "frame extraction failed" health note
- System (not Poetry-managed): **`node`** — YouTube media downloads need a JavaScript runtime to solve the signature / n challenges. yt-dlp enables only `deno` by default, so `tools/video_fetch.py` passes `--js-runtimes node` at every call site (the `_YT_DLP` prefix) and `yt-dlp-ejs` supplies the solver script. Both halves are required: without the runtime yt-dlp warns "No supported JavaScript runtime" and drops to a fallback client; without `yt-dlp-ejs` it reports "Signature solving failed" / "n challenge solving failed". Either way captions still resolve, so the failure looks like one unlucky video while every media download can 403 — which costs `/ingest-video` the whole vision pass
- System (not Poetry-managed): **`agent-browser`** (npm global) — browser automation backing `tools/coinglass_capture.sh`, the ST15 half-automation of the `/ingest-charts` daily capture set. Absent ⇒ that script fails outright; hand-captured drops are unaffected, so nothing else in the repo notices. **Its daemon is sticky**: once running it keeps its ORIGINAL launch options and merely warns `--profile ignored: daemon already running`, so a second invocation silently runs on a throwaway `/tmp` profile — i.e. logged out. Coinglass gates every coin except BTC behind an account, so a logged-out run captures one good BTC panel and reports ETH/SOL as "gated", which is indistinguishable from an expired login. The script tears the daemon down and hard-exits if the warning survives; never bypass that check
- Optional env: `GROQ_API_KEY` — `whisper-large-v3` fallback, used ONLY for caption-less video (nearly all X video). Unset ⇒ those videos are skipped with a health note, captioned video is unaffected. See `.env.example`
- Dev deps: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs
- Never modify `poetry.lock` manually — use `poetry add` / `poetry remove`

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update `README.md` to stay in sync.

## Session Memory Protocol

At the end of every session where anything changed (features, bug fixes, refactors, decisions), automatically update the **Current State** section in `~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md`. Do not wait to be asked.

Fields to keep current:

- Last session summary (one line: what changed)
- Open questions / pending decisions (or "none")

Keep the index small — it is read into context every session, so its cost is paid
on every conversation:

- **Current State holds at most 6 bullets.** Adding a 7th means first rolling the
  oldest, verbatim, into `memory/project_session_log_<month>.md`.
- **"Latest" is at most 2 lines; every other bullet is exactly 1 line.** Detail
  belongs in a topic file or the session log, never the index.
- Session logs have no size limit — that is what they are for. Prune by MOVING,
  never by deleting.

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (project-specific, committed to the
repo) and are invoked as `/skill-name`. Each encapsulates a recurring workflow so you
don't have to re-explain it. **Use them proactively.**

**The full list is not repeated here on purpose.** The harness already injects every
skill's name and description into each session automatically, so a table here was pure
duplication that could only ever drift *behind* the real thing. Read the injected list;
it is authoritative. Only the rules that the injected list cannot carry live below.

- **Always load `/frontend-design` before any Svelte / CSS / UI change**, and pair it
  with `/frontend-svelte`.
- **Cadence reminders** the descriptions don't convey: `/sanity-check` weekly or after
  any large refactor · `/db-update` after any detector, strategy or config change ·
  `/recalibrate` after any `make buibui-backtest SAVE=1` · `/journal-trade` whenever a
  manual trade closes · `/ingest-feed` daily.
- **`/wfo-sweep` is the trusted production path for `tp_r`.** `/config-refresh` runs on
  the full dataset with no out-of-sample split, so never take a `tp_r` from it.
- **Never `&&`-chain `/card` runs** — one background exec per card.

### Subagent definitions — `.claude/agents/<name>.md`

A **skill** is a workflow you invoke; an **agent** is who a skill dispatches work TO.
Definitions live in `.claude/agents/<name>.md` (frontmatter `name` / `description` /
`model` / `tools`), addressed as `subagent_type: "<name>"`.

**The reason is measured, not stylistic.** An unnamed general-purpose dispatch carries
the full default system prompt plus ~20 tool schemas — **~19.7K tokens of pure overhead
per dispatch** (measured 19,543–19,761 across a same-input 6-image A/B, 2026-08-07).
`chart-extract` (`model: sonnet`, `tools: Read`) cut `/ingest-charts` **−39.9%**
(296,456 → 178,123) with quality neutral-to-better. The saving is *constant per
dispatch*, so **payoff scales with dispatch count, not task size** — porting to
`/ingest-video` (2/video) and `/ingest-x` is skill-fix **7v**.

- **A new `.claude/` subtree needs re-includes in BOTH `.gitignore` and
  `.markdownlint-cli2.jsonc`**, or the file dies on a clone *and* ships unlinted.
  `.gitignore:15` is `.claude/*`; the lint config excludes `.claude` wholesale. Both
  re-include named subtrees only, and the lint config's own comment says they mirror
  each other deliberately.
- **`tools:` is a structural guarantee; prose is not.** `tools: Read` is why an
  extractor *cannot* write files — a general-purpose one did. But the "bare JSON, no
  fence" rule still broke 1-in-6 despite an explicit directive. **A system-prompt
  directive is not a parser** — keep tolerating malformed output at the consuming end.

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
- **Invoke `/post-branch` on every branch, and SPLIT it around `gh pr create`.**
  Steps 1–5b and 7 (behaviour gate → changed artifacts → doc walk → surface checks →
  MEMORY.md → **SoT reconcile** → commit and push) are **commit-producing and run
  BEFORE** `gh pr create`,
  so the doc fixes ship in the initial push. Steps 6 and 10a/10c (PR body, handoff)
  need the PR to exist and run **after**; they produce no commits. Step 10b writes a
  gitignored file and is free either way. **A blanket "before" would be as wrong as the
  blanket "after" this line used to carry** — 6/10a/10c cannot run pre-PR.
  Invoking it is not optional and not conditional: the skill's own Step 1 behaviour
  gate decides whether a docs sweep is warranted, so invoking it on a pure refactor
  costs one cheap check.
  **Why the split is load-bearing:** running the doc walk after PR creation pushes a
  fix onto an open PR, and every such push re-runs all CI (`pull_request: synchronize`)
  — ~3000 tests plus a 93s regression job for one paragraph. Measured on #557, avoided
  on #558 by walking first, and hit again on #568 while this very line still said
  "after".
  This paragraph is also the **only enforcement that survives a fresh clone**: the
  `PostToolUse` hook on `Bash` that backstops it lives in gitignored
  `.claude/settings.json` (like `guard-destructive.py`). Keep that hook — there is no
  hook event for "about to open a PR", which is why prose has to carry the rule — but
  know it fires *after* creation and **matches the whole command string**, so a `grep`
  or heredoc merely *containing* `gh pr create` triggers it. Re-add it if you reclone.
