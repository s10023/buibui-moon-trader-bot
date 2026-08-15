# CLAUDE.md

This file provides instructions for Claude Code when working in this repository.

## Working Agreement

**Persona.** Senior quant-systems engineer. Bias to de-biased, out-of-sample evidence
(DSR / PBO / MinTRL / avg_r across regime × session × combo) over in-sample optimism. Never
commit an overfit parameter. Report negative-EV findings honestly — a strategy that loses is
a result.

**Definition of Done.** A Python change is done when:

- `make lint-py` ✓ (ruff format + lint)
- `make typecheck` ✓ (mypy strict)
- `make test` green
- `make test-regression` goldens unmoved — **required only when the diff touches the
  backtest surface**: `analytics/backtest/`, `analytics/strategies/`,
  `analytics/signal_config.py`, `config/*signal_watch*.toml`, `config/strategy_params.toml`,
  `tests/fixtures/`, or `poetry.lock`. Say which branch you took. Outside that set it is
  ~95s of wall clock for a chain the diff cannot reach. When it does apply and a golden
  moves, that is a *decision* — regenerate or not — which is why it stays local rather than
  being left to CI.

**Anti-drift.** Before any multi-step task, restate the goal and its success metric in one
line. If a step stops serving that metric, stop and ask. Require avg_r × (regime × session ×
combo) evidence before killing a strategy — demote, don't delete.

**Token efficiency.** Skills are dormant until invoked. Use the context-mode `ctx_*` tools
for any command or output over ~20 lines. `/compact` at logical boundaries rather than
waiting for autocompaction. Delegate heavy reads to a subagent when the saved main-context
clutter outweighs the startup cost.

**Model delegation.** The main thread is orchestrator and tech lead — design, judgement,
review and routing stay here. Delegate bulk mechanical work down by tier: **sonnet** for
high-volume execution (vision extraction, file sweeps, boilerplate, test triage), **haiku**
for trivial lookups, **opus** subagents as a quota escape valve for long *parallel*
research. Every subagent brief carries goal + success metric + rubric inline, with no SoT or
memory re-reads. Verify subagent and background work directly (`ps`, `journalctl`,
`git status`) — self-reports can be stale.

**Guardrail.** A PreToolUse hook (`.claude/hooks/guard-destructive.py`) blocks catastrophic
Bash (rm -rf, git reset --hard, force-push, DB wipes). If blocked, surface it rather than
working around it silently.

**Footgun delivery lives in a hook, not in this file.** `.claude/hooks/context-guard.py` +
`context-map.json` deliver a card at the moment a guarded file is edited, which is what let
those rules leave the always-loaded tier (`_upsert`, `round_down_to_step`, the XS
maker/taker split, backtest run selection, the powered-null criterion, `bar` units,
DSR/MinTRL directionality). **Both files are gitignored, so a reclone keeps the knowledge
and loses the DELIVERY** — every shed rule has a tracked home in `.claude/context/*.md`, but
nothing hands it to you at edit time. Restore the hook before editing `analytics/`, `trade/`
or `portfolio/`, and re-run `python3 .claude/hooks/test_context_guard.py` (31 cases, the
only gate a hook has). **A card's globs must cover every file its rule bites on.**

## Project Overview

Buibui Moon Trader Bot — a crypto trading bot for Binance Futures. Live price + position
monitoring, an analytics/backtest stack (DuckDB), a 20-strategy signal engine with Telegram
alerts, and a FastAPI + Svelte web UI. Python 3.11+, managed with Poetry.

## Key Commands

```bash
make lint-py        # ruff format + lint
make typecheck      # mypy strict
make test           # full pytest suite
make test-cov       # + coverage (on demand; not a gate)
make lint-md        # Markdown
make web-build      # production bundle   (make web-dev for the Vite dev server)
make db-update      # db-update-backtest -> db-update-recalibrate -> regression-update
```

After adding a doc to `docs/audits/` or `docs/superpowers/specs/`, run `make docs-index` —
both `INDEX.md` files are generated and `tests/test_docs_index.py` fails until they are
current.

**`make lint-py` also rewrites Markdown.** It runs `ruff format .`, which formats python
code fences *inside `.md` files*, so any plan or spec doc carrying a python fence is
reformatted on every Python task and shows up as unrelated churn. When such a doc is in
play, land that reformat once up front.

## CLI

`buibui.py` is the single CLI entry point.

- `buibui monitor price | position` — live price / position monitor
- `buibui analytics backfill | sync` — OHLCV ingestion. `--universe` (mutually exclusive
  with `--symbols`) reads the committed 25-perp research set from `config/universe.toml`;
  `make universe-backfill` wraps the deep 1h/4h/1d/1w run since 2019
- `buibui backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `buibui digest` — pre-canned analytics queries
- `buibui param-audit | param-sweep` — WFO parameter tools
- `buibui recalibrate` — refresh star ratings
- `buibui brief` — daily market brief (levels/zones/regime/seasonality/pundit board)
- `buibui web` — start FastAPI backend

Each Makefile `buibui-*` target wraps the equivalent CLI invocation, except `buibui-backup`.

### `buibui signal watch | test`

Live signal daemon / historical replay.

**Config picking.** `watch` with no `--config` auto-picks by **UTC weekday**, and the three
configs partition the calendar without overlap: `{Mon,Fri}` → `signal_watch_weekdays.toml`,
`{Tue,Wed,Thu}` → `signal_watch.toml`, `{Sat,Sun}` → `signal_watch_all.toml`. **Read the
`day_filter`, never the filename** — `signal_watch_all.toml` carries `day_filter =
"weekend"` and `signal_watch_weekdays.toml` carries `"mon_fri"`, meaning Mon *and* Fri. UTC
rather than local, so the picker matches each candle's UTC `open_time`.

**`--once`** runs a single scan cycle and exits (cron / GitHub Actions entry).

**`--catch-up`** (SoT N6, off by default, `CATCH_UP=1` on `make buibui-signal-watch`)
replays every un-alerted **closed** candle since the last run. The live ledger is the OOS
evidence base, so gaps in it are a *biased* sample rather than merely a thinner one — the
hourly GH-Actions cron measured 35% delivery with a session-skewed run-hour distribution,
p<0.01. Backfilled candles are persisted and watermarked but **never sent to Telegram**;
only the newest closed candle can alert. A cold-start guard (`CooldownStore.last_marked()`)
stops a fresh `signal_state.json` bursting the whole window on first contact.

Recovery depth is bounded by `scan_window(tf)` — **200 bars everywhere except 15m, which is
600**. The window is catch-up's REACH rather than a perf knob: N8 recovery gaps run 3–6
days, a flat 200 bars reached only 2.1 days on 15m, and 15m is 64.4% of the live ledger.

The dedup watermark is keyed `symbol:tf:strategy:UTC-weekday`, so a missed boundary day is
not buried by a later weekday's fire. Scoping by *config* would not work — Mon and Fri share
`mon_fri`. Legacy 3-part state is seeded into all seven scopes on load, idempotently.

Gating context (regime / HTF-EMA / ADR / DOW / `confidence_ratings`) is computed
**as-of-now** and applied to historical candles — good for days, not months, so **treat a
deep backfill past a ratings refresh as backtest rather than OOS**.

`DATA_SOURCE=okx` selects the keyless OKX adapter (`utils/okx_client.py`), used by
`.github/workflows/signal-watch.yaml`, which seeds an ephemeral `analytics.db` from the
committed slim `live_signal.duckdb` (`make export-live-db`). Local default is `binance`.

### `buibui portfolio replay`

Replays the live outcome ledger through the Carver two-layer sizing model into a paper
portfolio (read-only); prints Sharpe/Sortino/max-DD/attribution. Wrapped by
`make buibui-portfolio-replay` (`CONFIG=` / `CAPITAL=` / `VOL_TARGET=`).

An **era check** names how many signal-path rule changes the replayed sample straddles (46
at first reading, largest single-era sub-sample 32%), so read the headline as an average
ACROSS rule changes rather than a measurement of the current book.

**`outcome_r` changes BASIS mid-ledger** at `e5d92bb` (#432, 2026-06-11, net_R cost parity):
rows resolved before it are gross, after it net, and the backfill only scores *unresolved*
rows, so both bases persist in the table permanently. `replay_ledger` restates the old half
by default (`restate_cost_basis=False` reproduces the mixture) — the drag is deterministic
in stored columns, so this is arithmetic rather than an estimate. It moves the pooled ledger
**−0.1303R → −0.1665R**, and **the long-quoted −0.085R is the old GROSS half** (reproduces
at −0.0881).

- ⚠ **Split on `outcome_filled_at_ms`, never `candle_ts_ms`** — candle time smears the step
  across the resolution lag and manufactures a phantom era two months early.
- ⚠ **Costs are MODELLED, not realised.** Raw stays exactly −1.0 = declared risk, so
  **neither half expresses gap risk** and every figure is an optimistic bound whose error
  runs one way.
- ⚠ Drag `= 2(fee+slip)·entry/risk` scales inversely with stop width, so **any live-ledger
  comparison between cells of differing stop width inherits a bias, not just a level
  shift** (ST27's runs favourably — wider stops carry less drag, so CONFIRMED-BAD is
  conservative).

### `buibui card SYMBOL`

AI trade card (F2). Composes brief panel (M1 indicators + M2 sessions) + pundit board + XS
target + recent fires + live account into a MarketState, sends the card-v4 rubric to
`claude -p` (subscription auth, keys stripped, `CLAUDE_CONFIG_DIR=~/.claude-personal`, bare
temp cwd), then a deterministic post-pass sizes the trade and enforces hard rules in code
(VETOED on violation, including a `valid_until_utc` that is unparseable or does not postdate
the card's own `generated_at_ms`). Wrapped by
`make buibui-card SYMBOL=BTCUSDT [DIRECTION=] [HORIZON=] [AS_OF=] [DRY=1] [CONFIG=]`.

- **Sizing.** Quantity is floored to the symbol's exchange LOT_SIZE step via
  `portfolio.sizing.round_down_to_step` — shared with `trade/routing.py` so card and XS
  router round identically — and `risk_usd` / `risk_frac` are restated from the ROUNDED
  size, so the printed risk is the risk actually taken. A sub-lot budget vetoes; an
  unreachable exchange degrades to an unrounded quantity **with** a warning.
- **Capital.** `portfolio.sizing.resolve_capital` returns live account equity when
  available and the configured `[portfolio] capital` otherwise. Every card records
  `capital_used` / `capital_source`, because a bare `risk_frac` is uninterpretable after the
  fact once capital is live. The same resolved capital scales `daily_r`, so the daily
  circuit breaker is measured in real R: against the old hardcoded constant a true −2R day
  passed the gate as **−0.24R**. At ~$1,200 equity a BTCUSDT stop wider than roughly
  2.7% VETOes for a sub-lot risk budget — the same capital wall the XS sleeve hits at
  ~$1,000 minimum.
- **`--horizon intraday|swing`** sets the SCORING window the pundit-calls row is resolved
  against (48h vs 30d). It is stamped on `FinalCard`, so both ledgers read one value and
  cannot disagree. `swing` also drops 1h from the recent-fires scan and re-anchors the
  rubric to 4h/1d — **it never adds 1w, where no detector runs, so the scan would read an
  empty population as "no fires"**.
- **`--as-of` pins the INPUTS, not the card.** It admits only bars that had CLOSED at the
  anchor and omits the live account, which is unpinnable because Binance serves only current
  positions and equity; the state records a health note saying which. It therefore always
  takes the config capital path, with a warning. **The model stays nondeterministic** — two
  runs on a byte-identical `state_digest` have returned opposite directions, so a pinned
  anchor makes a comparison possible, never conclusive at n=1.
- **Pundit `avg_r` is STRIPPED from the payload** in
  `card/state.py::_strip_censored_pundit_stats`. It is computed on a winner-dropped
  subsample: three authors once compressed to an identical −1.0 against a true `avg_atr_r`
  of −0.334 / −0.502 / −1.331, so a consumer ranking on it gets the ORDER wrong. Shipping
  the clean metric alongside would not have fixed it — both fields reached the prompt.
  Rubric 3b names `avg_atr_r`'s ATR units, and §2 states a liq cluster is a BAND whose edges
  reproduce to only ~16%, never a level.
- **M4 external liquidity** (heatmap / liq-map clusters) enters as mapped liquidity with
  trust guards, capped at ONE confluence input. ⚠ **It is not horizon-filtered** —
  snapshots carry a `window` and the model is told to discount a short one, but
  `load_external_state` selects on source/age/rows only, and every fresh capture is 24h or
  1d. **Fix the capture set before adding a filter**, or the block just empties.
- **Ledgers.** Every card appends to gitignored `docs/plans/ai-cards.jsonl`; TRADE cards
  dual-write a pundit-calls row (author `buibui_card`) so `make buibui-pundit-score` scores
  the AI with no scorer changes. `analytics/brief/pundit.py` excludes `source:"ai-card"`
  rows and drops `buibui_card` from the priors authors list, so the card is never an
  external pundit to itself.
- `--dry-run` prints state + prompt with no LLM call.

### `make buibui-backup`

Wraps `deploy/backup-analytics.sh` — a verified local snapshot of `analytics.db` plus the
ledger files and directories named in `LEDGERS`, `LEDGER_DIRS`, `EXTERNAL_LEDGERS` and
`EXTERNAL_LEDGER_DIRS`. `WEEKLY=1` adds the parquet export, `DRY=1` reports only. A systemd
user timer runs it twice daily.

**Everything covered is gitignored and single-copy, so those four arrays ARE the only
copy.** `analytics.db` is gitignored and single-copy, and the committed `live_signal.duckdb`
is not a backup — its `signal_alert_outcomes` table has 0 rows.

- **Prefer a GLOB over a wholesale tree to an allowlist.** An allowlist over a single-copy
  tree defaults to UNCOVERED, so a new artifact stays invisible until someone diffs the
  backup against the live tree. Five audits each found a gap the previous one missed — a
  glob covers a new project's tree the day it appears, with nobody needing to notice.
- **Put a directory in `LEDGER_DIRS` / `EXTERNAL_LEDGER_DIRS`, never a file array.** The
  file loops are `[ -f ]`-guarded and skip a directory SILENTLY. This has now caused the
  same gap twice.
- **The `EXTERNAL_*` arrays take `src:dest` pairs** and land under `_external/` in the
  snapshot; the `LEDGERS` loop is `"$REPO/$f"`-relative and resolves an absolute path to
  nonsense. They cover `~/.claude-personal/history.jsonl` (the account-level prompt log,
  the only record of a session that survives transcript cleanup, and what `budget.py`
  checks its own coverage against) and every `~/.claude-personal/projects/*/memory` tree
  (the cross-session knowledge base, 333 files / 2.5MB across 7 projects, in no git remote).
- **Watermark and dedup ledgers are the subtle entries** (`yt-feed-state.json`,
  `routed-ledger.json`, `.cache/chart-drops/processed.json`, `task-marks/`): losing one
  destroys no past data but silently changes future behaviour — consumed videos re-present,
  re-ingests double-write, handled chart drops re-ingest, and a missing task marker reads as
  OVERDUE, re-presenting every weekly cadence at once. **`processed.json` lives under
  `.cache/`, which every cleanup treats as disposable.**
- `config/pundit_roster.toml` holds 16 accumulated operator alias rulings that no
  re-derivation recovers without re-watching every video. The committed `.example` carries 2
  schema-demo entries and is not a backup.

⚠ **`rclone config create` / `update` PRINT the whole remote — `client_secret`,
`access_token`, `refresh_token` — to stdout on SUCCESS, unprompted and unflagged. Append
`>/dev/null` at every call site, docs included**, because people copy from docs. Two live
tokens leaked into transcripts on 2026-08-15 this way, the second *after* both repos had
written up the first: the mitigation was prose ("never paste the output"), and a rule that
needs a human to notice output they did not ask for is not a control. The safe
verifications are `rclone lsf <remote>:` and `rclone about <remote>:`.

**Off-machine leg:** `deploy/backup-offsite.sh` (`rclone sync` of `$BUIBUI_BACKUP_ROOT`),
installed separately because `rclone config` is interactive. It **exits 1 while
`BUIBUI_BACKUP_REMOTE` is unset**, so an enabled-but-unconfigured timer complains daily
rather than looking green while no off-machine copy exists. It uses `sync`, so it mirrors
deletions — which is why it refuses to run when no `MANIFEST.json` exists under the backup
root rather than syncing an empty tree over the remote.

**`sync` mirrors deletions INTO the destination too**, so three guards stand between a
mistyped remote and an UNRELATED folder on the same drive: rclone's `root_folder_id`,
pinned on the remote so it cannot address anything above the backup folder; a rejection of
any remote with no path component, since a bare `remote:` is the whole drive; and a
rejection of a destination holding entries the local root does not have. **Only the last
two are tracked code** — `root_folder_id` lives in `rclone.conf` and `rclone config delete`
drops it, so prefer `rclone config reconnect <remote>:` when rotating a credential, and
re-pin it whenever the remote is rebuilt.

⚠ **Guard 3 compares TOP-LEVEL entries only, so it does NOT protect against a SIBLING
REPO** — measured 2026-08-15, a wifey-shaped root aimed at this repo's destination passed
the guard and dry-ran `Skipped delete` on real snapshots, because both trees are `daily/` +
`weekly/`. **Two repos on one drive is a confinement question, not a guard question: give
each its own remote with its own `root_folder_id`**, which makes the collision unreachable
rather than detectable. A sibling path under one shared root leaves operator care as the
only control.

**`buibui-daily-check`** pushes `docs/plans/daily_check.py --exit-on-tier2` to Telegram once
daily at 09:10 UTC. That flag exists because tier-2 lines (chart-drops, external-context) do
not set exit 1 on their own; a hand-run check still exits 1 only on tier 1.

- **`TELEGRAM_ALWAYS=1` makes the report arrive EVERY day, green or red.** A failure-only
  contract is unfalsifiable: a dead timer and a healthy day look identical on the phone, and
  the delivery path is then only ever exercised on a red day. It stays **opt-in per job** in
  `run-job.sh` so the 15-minute signal-watch does not inherit it and send 96 messages a day.
- **Both push paths HTML-escape the body and wrap it in `<pre>`.** `utils/telegram.py` sends
  `parse_mode=HTML`, and an unescaped traceback (`line 33, in <module>`) is rejected 400 —
  so the failure alert failed on exactly the crashes it exists to report.

Install / retention / restore → `deploy/README.md`.

## Project Structure

The deep reference lives in `.claude/context/`, and the pointers below are the only path a
session has to it, so **follow them before working in an area**. Verdicts and footguns stay here
on purpose: they guard against re-litigating settled research, and a guard rail behind a
pointer is not a guard rail.

| Package | What it is | Deep reference |
| --- | --- | --- |
| `buibui.py` · `cli/` | Thin CLI entry shim delegating to `cli.main:main`; argparse subcommand package | — |
| `analytics/` | Analytics data layer (DuckDB): `store/`, `strategies/`, `backtest/`, `signal/`, `stats/`, `brief/`, `exits/`, `research_guards/` + audit libs | `context/analytics.md` |
| `analytics/{forecast,xsmom,combine,carry,xsrev,cvd}/` | The P2/P3 research sleeves | `context/research-sleeves.md` |
| `signals/` · `card/` · `portfolio/` | Alerting + dedup daemon; F2 AI trade card; P1 paper-portfolio sizing | `context/signals.md` |
| `web/` | FastAPI backend + Svelte 5 / Vite UI | `context/web.md` |
| `trade/` · `deploy/` · `monitor/` · `utils/` | Execution layer (XS live wiring + overlay), 24/7 VPS deploy kit, live monitors, shared utils | `context/execution.md` |
| `tools/` | One-shot analysis + audit scripts; not part of the daemon or CLI surface | `context/tools.md` |
| `tests/` | pytest suite; tests import from lib modules and pass mock dependencies directly | — |
| `config/` | `coins.json` (gitignored), `universe.toml`, `strategy_params.toml` (shared base inherited via `extends`), `eras.toml` (era boundaries git cannot see) | `context/analytics.md` |

### Sleeve verdicts — do NOT rebuild a shelved sleeve

| Sleeve | Verdict |
| --- | --- |
| `xsmom/` cross-sectional momentum | **+1.375 Sharpe, DSR 0.997, PBO 0.295 — CLEARS the gate. THE DEPLOY CORE.** Alpha not beta; persistent (2021 was a losing year); capacity-green at operator scale |
| `forecast/` EWMAC trend | +0.36 — structurally real but FAILS the gate (CI includes 0). **SHELVED**, and the regime-conditional escape hatch is CLOSED by measurement |
| `combine/` trend×XS | +1.145, clears the gate but is **Sharpe-dominated by XS-solo**. A validated socket awaiting a comparably strong second edge |
| `carry/` funding carry | +0.03, FAILS, not cost-robust past 8bps. **SHELVED** |
| `xsrev/` XS reversal | **−2.9, negative even at ZERO cost. SHELVED** — the additivity thesis is falsified; momentum continues down to 2–7d |
| `cvd/` spot-perp CVD divergence | **ALL 10 pre-registered trials FAIL. SHELVED** (`docs/audits/2026-08-11-d1-spot-perp-cvd.md`). XS −0.147, PBO **0.849**; TS +0.183, PBO 0.316 but DSR 0.532 and boot_lo −0.642. `corr_to_xsmom` is −0.04/−0.08, so it WAS decorrelated from the deploy core: **the failure is missing signal, not redundancy**. Daily-bar completeness is MEASURED (residual exactly 0.0), so the resolution question is closed. The one live re-entry is intraday *sequencing*, which a daily design cannot see |

### Standing research verdicts

**EWMAC per-regime attribution — NO** (`docs/audits/2026-08-06-p2-ewmac-regime-attribution.md`,
tool `forecast_audit.py --regime`). Trend Sharpe does not demonstrably concentrate in trend
regimes: the raw split looks like a win (+0.094 unconditional → +0.244 in trend) and
dissolves entirely under the cross-section correction below; there is no dose-response
across a 16× range of slope thresholds; and an optimistically built regime-gated book gains
+0.102 Sharpe on a difference CI of [−0.459, +0.715]. **Do not rebuild this as a gated
variant.**

**⚠ Pooling symbol-days across this universe inflates every t-stat ~2.92×.** Mean pairwise
correlation of per-instrument net returns is **0.315**, so 25 perps carry the noise
reduction of **2.92 effective independent series, not 25**. A naive t over 41,571 pooled
symbol-days reads t=2.21 on a cell whose corrected value is **+0.757**.
`analytics.forecast.effective_independent_series` computes the deflator; `RegimeCell.t_stat`
is already deflated, with `t_stat_naive` kept beside it so the adjustment is auditable.
**Any audit slicing this universe per-symbol inherits this, the XS sleeve's cuts included.**
Book-day rows are already aggregated and must NOT be deflated again. **The 25-symbol 2.92×
does not transfer** — n_eff is 1.97 for 14 perps and 1.42 for three, giving deflators of
1.628× and 3.331×, so breadth buys almost nothing when the cross-section is nearly one
asset.

**Ensemble / confluence scoring — FAILS the gate**
(`docs/audits/2026-08-11-ensemble-walkforward.md`). "Combine the failed hypotheses into a
confidence score" is a natural idea and will recur. DSR **0.7030** against 0.95. PBO (0.235)
and boot_lo (+0.053) pass, so this is not a null — the effect simply cannot be separated
from the best of 16 searched constructions. **The decisive number is a sign flip: the target
cell is +0.1546 R/day and its trailing-window twin is −0.1723**, same axes and same sizing
map, so the effect belongs to the expanding-window fit rather than the score. The
2026-08-08 half-split headline (+0.310) was look-ahead — split on `fired_at_ms`, admitting
fired-but-unresolved alerts (median lag 23h, tail 310h); under a causal refit on
`outcome_filled_at_ms` that bucket reads **+0.0130**. Do NOT wire it into
`portfolio/sizing.py`. The one clean re-entry is the single pre-registered construction
re-tested on book-days accruing after 2026-08-11.

**Multi-regime detector validation — NO detectable regime dependence**
(`docs/audits/2026-08-12-multi-regime-validation.md`). The one nominal hit
(`eqh_eql`/15m/short, Δ +0.158R, t=+2.33) fails the 4-test Bonferroni |t| ≥ 2.498. ⚠ **The
other 3 cells were filed as powered nulls and are INSUFFICIENT** — 0 of 3 survive CI
containment, half-widths 2.1× / 3.8× / 7.1× the bar, so regime dependence up to ±0.11R–±0.39R
is UNTESTED on the panel this study called its best-powered
(`docs/audits/2026-08-14-st28-multi-regime-powered-null.md`). The decisive number is
exploratory but plain: the 2021–22 15m book reads median −0.0431R against the 2025–26
corpus's −0.0474R, so **the book is equally unprofitable in a bull leg, a bear leg and now.
Its weakness is structural rather than a regime artifact.**

Three things from that study generalise:

1. **A historical study must GENERATE its legs, not query them.** `backtest_trades` held
   only 2025-09-12 → 2026-07-07, so star ratings, the decay review's 66 cells and
   `min_avg_r` were all fitted inside one 10-month window.
2. **Trial count dominates n, and it is not close.** A 21× range of n moves the DSR bar 10%,
   while 1 → 320 trials moves it 21× (+0.049R → +1.035R against a corpus best of +1.196R).
   **A per-cell scan is structurally unreachable** — design around trial count, never sample
   size.
3. **`backtest_trades` carries a ~5.29× duplication factor** from repeated runs (857,740
   rows → 162,263 distinct). Dedup on `(symbol, timeframe, strategy, direction,
   entry_time)`, or every n inflates ~5× and every t ~2.3×.

**The binding constraint, confirmed five times** (exits, trend-weight, combine, carry,
reversal): the system needs a second *strong* edge, and the cheap price-only free-data
levers are exhausted. A new sleeve must carry genuinely new information. **Conditioning axes
are 6-for-6-plus-one-amended** — regime/session/combo/direction, H14's Coinbase premium and
H15's USD/JPY carry-unwind found no edge; H8's M1 axes are the amendment. The diagnosis has
moved off conditioning and onto the signal book.

- ⚠ **Read "found no edge" as "no effect was FOUND", never as "an effect was RULED OUT"** —
  under CI containment those cells are INSUFFICIENT.
- **H15** (`docs/audits/2026-08-04-h15-usdjpy-carry-unwind.md`): NO-EDGE / INSUFFICIENT on
  every cell in every panel. **The NO direction stands; the "no more-data door" corollary
  does not** — it rested on a sample-size floor, and the corrected criterion flips the
  primary panel to INSUFFICIENT. H15 is the second genuinely different data source after
  H14, and both found no edge, which sharpens the standing conclusion.
- **H8 AMENDED** (PR #546, `docs/audits/2026-07-24-h8-m1-indicator-conditioning.md`): its
  original NO came from a build in which AVOID could essentially never fire, with the
  pre-committed MinTRL leg missing. The re-run finds 25 backtest AVOID cells, 17 long-side,
  where the published table had zero — price *location* gates BOTH directions. **This is not
  a sixth conditioning win:** 7 of the 25 are exact binary-axis mirrors of a BUILD cell, the
  price-location axes are ~1 effective finding rather than 25, and live is not independent
  of the backtest. Indicator *character* remains a NO.

### Code-level rules

**`DEFAULT_DB_PATH` lives in `analytics/store/_common.py`** (re-exported from
`analytics.store` and `analytics.data_store`) — import from a re-export rather than
redefining it in a runner. It is not in `schema.py`, and the wrong path fails as an
`ImportError` on first use. This stays here because the trap bites while writing a runner
*anywhere*, which no edit-time card can see.

**Backtest run selection** — the `writer` argument, the `(sweep_id IS NOT NULL, run_at_ms)`
ranking both selection sites must keep mirroring, and `recalibrate_lib.select_rated_run_ids`'s
two scope arguments all ride the `backtest-run-id` card. Two verdicts outlive the mechanism:
before 2026-08-12 the live gate silently replaced swept rows (**415 overwritten, 331 whose
stored aggregate disagreed with their own trades**, and **53% of rated `tue_thu` cells owned
by the daemon** rather than the deliberate sweep); and **every decay review before
2026-08-13 audited a pool frozen at 2026-04-09** — the verdict direction survived, which is
why it stood, but every *named cell* was wrong. The drift began in a gitignored driver
→ [[scratch-dir-is-for-output-not-code]].

**`MIN_DSR_TRADES` gates COUNT, not DISPERSION.** `_sharpe` rejects only `sd == 0.0`
exactly, so `bos/1d/long` (36 trades all ≈ −1.0076R, sd 0.0022, **Sharpe −461**) clears the
floor and inflates trial-family variance **0.0348 → 1729.89**. A/B'd against a dispersion
floor, production DSR did not move — a latent fragility rather than a cause of the
star-ratings null.

**Lot-size rounding** — `portfolio/sizing.py::round_down_to_step` snaps before it floors;
why that snap is load-bearing rides the `sizing-round-down` card.

**XS execution** — the maker/taker split, GTX book-touch pricing, the cancel-before-plan
precondition and the taker `fee_pct` that keeps the sleeve's gate verdict a floor all ride
the `xs-execution` card (deep ref `.claude/context/execution.md`). **The deployment
consequence stays here: a dedicated sub-account is a hard blocker.** Two independent reasons
— `trade/routing.py:69` closes non-book positions, and inside the managed set
`cancel_open_orders` is symbol-WIDE, so it also cancels the operator's own resting orders on
BTCUSDT/ETHUSDT/SOLUSDT, exactly where a discretionary book sits and exactly what
`config/universe.toml` leads with.

**When you fold Sharpe to `abs()` for a negative-direction verdict, disclose the cost** —
folding shrinks trial dispersion in a mixed-sign family, so the gate becomes marginally
**more permissive** than the signed form; bias runs toward more passes. (*Why* DSR and
MinTRL must be folded at all rides the `audit-verdict` card.)

### The gate, and powered nulls

**The published gate is THREE legs: `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧ boot_lo > 0`.** All five
sleeves implement exactly this. It is **one function** —
`analytics.research_guards.passes_gate` — so call it rather than restating `0.95` / `0.5`
anywhere; both `combine_gate_verdict` and `xs_gate_verdict` delegate to it. `min_trl` is
computed and printed as a stamp but gates nothing: MinTRL against a non-zero target asks
"can I confirm Sharpe ≥ 1", a far harder question than "is there an edge", and the deploy
core would not clear it (needs ~7035 obs, has ~2475), as its own audit discloses at
`2026-06-16-p3-xsmom-sleeve.md:105`. **Do not quote the four-leg form.** `corr_to_trend` is
likewise not a leg — the deploy core cleared at **+0.37**, so coding it as a pass condition
would fail the sleeve that carries capital.

**Powered nulls — the METHOD rides the `audit-verdict` card; the VERDICTS stay here.** The
family reached **six sites**, each spelling the arithmetic differently (sample-size floor ×4,
failure-to-clear ×1, noise-derived MDE ×1), so **look for the CLAIM rather than grepping for
the pattern, and assume a seventh.** The criterion now lives in exactly one function, so a
seventh site must be a new refusal to call it rather than a new way to spell it. Re-runs,
none of which reversed a verdict direction: **ST26** (H8/H9/H10) left **41 of 170** filed
NO-EDGE/COSMETIC cells standing, H9 losing all 12 and H10 keeping 4; **ST27** dropped **22 of
30** negative claims; **ST28** killed all 3. Verdicts:
`docs/audits/2026-08-13-st26-powered-null-rerun.md` ·
`docs/audits/2026-08-14-st27-sl-horizon-powered-null.md` ·
`docs/audits/2026-08-14-st28-multi-regime-powered-null.md`.

Three readings bind:

1. **The live flat-2% answer moved: `CONFIRMED-BAD` holds at 15m ONLY.** Every 1h/4h cell is
   INSUFFICIENT, so read those TFs as *untested* rather than as *widening works*. The
   fidelity gate still fails and ST9 stays unaccepted.
2. **H10's `h96` was filed NO-EDGE while carrying Holm p=0.000, DSR 0.972 and PBO 0.013** —
   it clears all three gate legs, so a corrected INSUFFICIENT means *real but unsized against
   the bar*, not *ruled out*. The two point at opposite next actions.
3. **ST28's sixth site sat in gitignored scratch code**, unreachable by every gate, grep and
   review surface this repo has → [[scratch-dir-is-for-output-not-code]] — and **its spec
   and its driver disagreed on DETECTION** (spec `|t| ≥ 2.802`, code `|t| ≥ 1.96`). That is
   the H8 missing-gate-leg class inverted: not a leg the code skipped, but one it implemented
   *differently*, which no gate catches because both halves are internally consistent.

**A pre-committed gate leg the code never implements is invisible.** H8's spec §7 required
`n >= MinTRL(0.95)`; the code never had it, the verdict doc never mentioned it, and every
published BUILD cell cleared a gate missing a pre-registered condition. Greps cannot find
this class — only reading each spec against its implementation can.

### Spec-reconcile counting

**Read `docs/superpowers/specs/INDEX.md`, which derives the count from disk** (generated by
`make docs-index`, kept honest by `tests/test_docs_index.py`). Never hand-maintain a count
here. **Treat that number as a claim to verify rather than a fact to quote** — it has been
wrong every time anyone has checked, and its error runs in BOTH directions.

Four rules keep it honest:

- **Never write a spec's filename in an audit except to claim you walked it.** The counter
  derives from "an audit that names a spec's filename and discusses reconciling", and it
  cannot tell a citation from a disclaimer — naming two siblings just to say they were
  NOT walked moved the count 3 → 6 of 52; removing the filenames settled it at 4. Refer to
  a spec by date and title instead.
- **Name the FILE, not the sleeve** — "xsmom" is ambiguous across seven spec docs.
- **A partial reconcile is not a reconcile.** The counter has no notion of partial credit,
  so a one-leg entry silently becomes a whole-spec one.
- **A reconcile that produces no audit did not happen**, as far as any tool or next session
  can see.

The denominator is the FULL spec corpus: a spec with nothing to reconcile still costs
someone a look to confirm that, and an implemented-only denominator needs a per-spec
judgement call nothing records.

**The deploy core is reconciled and came back CLEAN** — every pre-registered gate leg and
all 7 construction steps are implemented as written, so the H8/P2 failure mode did not recur
on the sleeve that carries capital. Its one defect runs the other way: **§Causality's
pre-registered test is unsatisfiable as literally written** (it asserts no book return
changes on days `< d+1`, but the day-`d` return is `leverage_d × r_d` and `r_d` depends on
`close_d` by construction — measured Δ 0.924 at day `k`, 0.000 before it). The code asserts
on the **position** instead, which holds to machine zero, and the spec was amended to match.
**Never "repair" working P&L to satisfy the old wording** — a book return that did not
respond to its own bar's close would be the real bug.

## Code Style

- **Linter + formatter**: ruff (handles linting, import sorting, and formatting)
- **Type checker**: mypy strict (`disallow_untyped_defs = true`)
- **All functions need type annotations**, return types included (`-> None` for tests)
- **Markdown linter**: markdownlint-cli2
- Use `from typing import Any` for mock parameters in tests

## Testing

- pytest + unittest.mock. Tests must not make real network calls — lib functions accept a
  `client` parameter and tests pass a `MagicMock` directly.
- Analytics tests use `duckdb.connect(":memory:")` for full DB isolation — never touch the
  real `analytics.db`.
- Run: `make test` or `poetry run pytest tests/ -q`.
- Coverage is not part of `make test` and nothing gates on it — run `make test-cov` on demand.
- **Regression tests**: `make test-regression` compares backtest pipeline output to golden
  JSON in `tests/fixtures/`, and skips if fixture parquets are absent. Run
  `make regression-update` to regenerate goldens after intentional changes.
- **Diagnose golden drift only from `make test-regression`.** `pyproject.toml` sets a global
  `timeout = 30` and the three `test_regression.py` golden backtests take ~97s, so a bare
  `pytest tests/` reports them as *timeout failures* indistinguishable from real drift.
  `make test-regression` passes `--timeout=300`; `make test` skips them entirely
  (`--ignore=tests/test_regression.py`). This burned two reviewers on false alarms in one
  session.

## Dependencies

Managed via Poetry (`poetry install --no-root`). Never edit `poetry.lock` by hand — use
`poetry add` / `poetry remove`.

- Runtime: `duckdb`, `pandas`, `pyarrow`, `yt-dlp`, `yt-dlp-ejs`
- Dev: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs

System dependencies, not Poetry-managed:

- **`ffmpeg`** — `/ingest-video` frame extraction + audio chunking. Absent ⇒ every frame grab
  fails and the skill records a "frame extraction failed" health note.
- **`node`** — YouTube media downloads need a JavaScript runtime to solve the signature / n
  challenges. yt-dlp enables only `deno` by default, so `tools/video_fetch.py` passes
  `--js-runtimes node` at every call site (the `_YT_DLP` prefix) and `yt-dlp-ejs` supplies
  the solver. **Both halves are required**: without the runtime yt-dlp warns "No supported
  JavaScript runtime" and drops to a fallback client; without `yt-dlp-ejs` it reports
  "Signature solving failed" / "n challenge solving failed". Captions still resolve either
  way, so the failure looks like one unlucky video while every media download can 403 —
  which costs `/ingest-video` the whole vision pass.
- **`agent-browser`** (npm global) — backs `tools/coinglass_capture.sh`, the ST15
  half-automation of the `/ingest-charts` daily capture set. Absent ⇒ that script fails
  outright; hand-captured drops are unaffected. **Its daemon is sticky**: once running it
  keeps its ORIGINAL launch options and merely warns `--profile ignored: daemon already
  running`, so a second invocation silently runs on a throwaway `/tmp` profile — logged out.
  Coinglass gates every coin except BTC behind an account, so a logged-out run captures one
  good BTC panel and reports ETH/SOL as "gated", indistinguishable from an expired login.
  The script tears the daemon down and hard-exits if the warning survives; never bypass that
  check.
- Optional env `GROQ_API_KEY` — `whisper-large-v3` fallback, used ONLY for caption-less
  video (nearly all X video). Unset ⇒ those videos are skipped with a health note. See
  `.env.example`.

## Documentation

When changes affect project structure, CLI commands, features, or behavior, update
`README.md` to stay in sync.

`docs/research/` holds standalone research reports (book/repo/canon audits). It is **not**
covered by `make docs-index` (`tools/docs_index.py` indexes `docs/audits/` and
`docs/superpowers/specs/` only), so adding a file there needs no `make docs-index` run.

## Session Memory Protocol

At the end of every session where anything changed, update the **Current State** section in
`~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/MEMORY.md` without
waiting to be asked. Keep current: a one-line last-session summary, and open questions or
pending decisions (or "none").

The index is read into context every session, so its cost is paid on every conversation:

- **Current State holds at most 6 bullets.** Adding a 7th means first rolling the oldest,
  verbatim, into `memory/project_session_log_<month>.md`.
- **"Latest" is at most 2 lines; every other bullet is exactly 1 line.** Detail belongs in a
  topic file or the session log.
- Session logs have no size limit — that is what they are for. Prune by MOVING.

## Agent Skills

Skills live in `.claude/skills/<name>/SKILL.md` (committed) and are invoked as
`/skill-name`. **Use them proactively.** The harness injects every skill's name and
description into each session, and that injected list is authoritative — only the rules it
cannot carry live here:

- **Always load `/frontend-design` before any Svelte / CSS / UI change**, paired with
  `/frontend-svelte`.
- **Cadence** the descriptions don't convey: `/sanity-check` weekly or after any large
  refactor · `/decay-review` weekly · `/db-update` after any detector, strategy or config
  change · `/recalibrate` after any `make buibui-backtest SAVE=1` · `/journal-trade` whenever
  a manual trade closes · `/ingest-feed` daily · `/research-distil` after any book, repo or
  paper ingest. The first three are marker-tracked in `docs/plans/task-marks/`, stamped by
  whoever runs them; a missing marker reads as overdue on purpose, and nothing auto-runs.
- **`/research-distil` emits at most THREE hypotheses per run, and that cap is the point.**
  The intake's own header says the bottleneck is testing capacity, not idea capture, and
  trial count dominates n — so a skill that turns a book into forty hypotheses pushes every
  cell out of reach. "Unreachable, do not build" is a successful output, not a failure.
- **Take `tp_r` only from `/wfo-sweep`**, the trusted production path. `/config-refresh` runs
  on the full dataset with no out-of-sample split.
- **Run one `/card` per background exec** — never `&&`-chain them.

### Subagent definitions — `.claude/agents/<name>.md`

A **skill** is a workflow you invoke; an **agent** is who a skill dispatches work TO.
Frontmatter carries `name` / `description` / `model` / `tools`; address as
`subagent_type: "<name>"`.

**The reason is measured.** An unnamed general-purpose dispatch carries the full default
system prompt plus ~20 tool schemas — **~19.7K tokens of overhead per dispatch**.
`chart-extract` (`model: sonnet`, `tools: Read`) cut `/ingest-charts` **−39.9%** (296,456 →
178,123) with quality neutral-to-better. The saving is *constant per dispatch*, so **payoff
scales with dispatch count, not task size**.

- **A new `.claude/` subtree needs re-includes in BOTH `.gitignore` and
  `.markdownlint-cli2.jsonc`**, or the file dies on a clone *and* ships unlinted.
  `.gitignore:15` is `.claude/*` and the lint config excludes `.claude` wholesale; both
  re-include named subtrees only, and they mirror each other deliberately.
- **`tools:` is a structural guarantee; prose is not.** `tools: Read` is why an extractor
  *cannot* write files — a general-purpose one did. But the "bare JSON, no fence" rule still
  broke 1-in-6 despite an explicit directive, so **keep tolerating malformed output at the
  consuming end**.

## Git Conventions

- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Never commit `.env`, `config/coins.json`, or IDE files

**Invoke `/post-branch` on every branch, and SPLIT it around `gh pr create`.** Steps 1–5b
and 7 (behaviour gate → changed artifacts → doc walk → surface checks → MEMORY.md → SoT
reconcile → commit and push) are commit-producing and run **before** `gh pr create`, so the
doc fixes ship in the initial push. Steps 6 and 10a/10c (PR body, handoff) need the PR to
exist and run **after**; they produce no commits. Step 10b writes a gitignored file and is
free either way.

Invoking it is neither optional nor conditional — the skill's own Step 1 behaviour gate
decides whether a docs sweep is warranted, so invoking it on a pure refactor costs one cheap
check.

**Why the split is load-bearing:** running the doc walk after PR creation pushes a fix onto
an open PR, and every such push re-runs all CI (`pull_request: synchronize`) — ~3000 tests
plus a 93s regression job for one paragraph.

This paragraph is also **the only enforcement that survives a fresh clone**: the `PostToolUse`
hook on `Bash` that backstops it lives in gitignored `.claude/settings.json`, like
`guard-destructive.py`. Keep that hook — there is no hook event for "about to open a PR",
which is why prose has to carry the rule — but know it fires *after* creation and **matches
the whole command string**, so a `grep` or heredoc merely *containing* `gh pr create`
triggers it. Re-add it if you reclone.
