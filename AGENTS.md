# AGENTS.md

Instructions for coding agents in this repository. `CLAUDE.md` imports this file, so every
agent reads the same thing. Add repo-wide instructions **here, not there**; keep `CLAUDE.md`
for guidance that only applies to Claude Code's own harness.

## Working Agreement

**Persona.** Senior quant-systems engineer. Bias to de-biased, out-of-sample evidence
(DSR / PBO / MinTRL / avg_r across regime × session × combo) over in-sample optimism. Never
commit an overfit parameter. Report negative-EV findings honestly — a strategy that loses is
a result.

**Definition of Done.** A Python change is done when:

- `make lint-py` ✓ (ruff format + lint)
- `make typecheck` ✓ (mypy strict)
- **The full suite runs ONCE per branch, and `make preflight` IS that run** — a fresh clone of the branch's committed HEAD, the same pytest invocation, at `/post-branch` Step 7. Measured 2026-08-20 on 4205 tests: **300.1s against `make test`'s 294.9s, +1.8%**, so hermeticity costs five seconds and buys the gitignored-path class (#586, #666) that `make test` structurally cannot see. ⚠ **Do NOT run `make test` and then `make preflight` over the same code** — that is the same suite twice for nothing. This bullet used to list `make test` as the gate with preflight as an exception to it, and on 2026-08-26 a session reading it that way ran both, which is why the framing is inverted here rather than merely clarified. **While iterating, run the TARGETED files you touched** (seconds, and they catch your own breakage before a five-minute run does); `make test` earns its place only when you need a whole-suite answer about code that is NOT COMMITTED YET — a clone cannot see that, which is exactly why preflight refuses on a dirty tree.
- `make test-regression` goldens unmoved — **required only when the diff touches the
  backtest surface**, which is **exactly CI's regression paths filter** (`lint.yaml:173-183`),
  mirrored here: `analytics/**/*.py`, `pyproject.toml`, `poetry.lock`, `config/*.toml`,
  `tests/test_regression.py`, `tests/fixtures/**.parquet`, `tests/fixtures/golden_*.json`,
  `scripts/extract_regression_fixture.py`, `.github/workflows/lint.yaml`. Say which branch
  you took. ⚠ **This list was a STRICT SUBSET of CI's until 2026-08-25 (ST89), and every
  divergence ran one way — the local rule was the PERMISSIVE one.** It named three
  `analytics/` subpaths where CI filters the whole package, so a diff elsewhere in
  `analytics/` read as outside-the-surface locally while CI golden-checked it anyway; the
  session then learned the goldens had moved **from a metered CI run on an open PR**, which
  is the precise outcome the next sentence rejects. ⛔ Do NOT resolve a future divergence by
  narrowing CI — mirror CI here instead. Outside that set it is
  ~95s of wall clock for a chain the diff cannot reach. When it does apply and a golden
  moves, that is a *decision* — regenerate or not — which is why it stays local rather than
  being left to CI. **It is a separate gate rather than a slower one: `make test` passes
  `--ignore=tests/test_regression.py`, so a green `make test` says NOTHING about the
  goldens** — skipping this line inside the backtest surface leaves them unchecked, not
  checked-later.

**Background anything measured in MINUTES; foreground anything measured in SECONDS.**
Background: `make test` (~4m55s — re-measured 2026-08-20; the long-quoted ~145s is stale by ~2x), `make test-regression` (~93s, NOT re-measured), `make wait-ci`,
`make wait-ci-main`, any CI poll. Foreground: `make lint-py`, `make typecheck`,
`make lint-md` — seconds each, and their failures should stop the next edit. The line is
minutes-vs-seconds, **not** tests-vs-not-tests. Use `run_in_background: true`; the harness
re-invokes on exit, so there is nothing to poll and no wakeup to schedule.

⚠ **The one hard constraint: never edit anything under the Python tree while a run is in
flight.** Both run against the working tree and pytest imports modules at collection, so a
green run against a tree that no longer exists is worse than no run — it is a false
"verified". Safe to overlap: MEMORY.md and memory topic files, anything under gitignored
`docs/plans/`, drafting a PR body under `/tmp`, reading code.

⚠ **With a SECOND SESSION in the same checkout the rule is wider than "while a run is in
flight", and both of the usual framings are too narrow** (measured 2026-08-25, two parallel
sessions). It bites at pytest **COLLECTION** — a half-saved module is imported and the whole
run errors, not just the file being edited — and it bites during a **COMMIT**: `pre-commit`
stashes **every unstaged file in the repo**, reverting the other session's in-progress work to
HEAD on disk and restoring it seconds later. **The stash is the tool's, not git's, and it
ignores the pathspec entirely** — so "I named my paths, so I cannot affect yours" is correct
about git and wrong here, which is exactly why it survives scrutiny. The restore reports
success either way; the patch survives at `~/.cache/pre-commit/patch<ts>-<pid>`. ⇒ **Any
operation that reads or rewrites the tree AS A UNIT — suite, collection, commit, `git stash`,
checkout — needs the other session quiet, and quiet must be DECLARED out loud, not assumed.**
Two sessions in one directory also share ONE checkout and therefore one branch
→ [[parallel-session-protocol]].

**`make preflight` is the one exception, and it is structural rather than a dispensation.**
It takes a clone of *committed* state in its first second and runs everything inside that
clone, so a later working-tree edit cannot reach the run at all — there is no window in
which a green result describes a tree that no longer exists. That is a second reason to
prefer it over `make test` on a branch, beyond hermeticity: it hands the working tree back
immediately.

This rule lived only in memory until 2026-08-19 and was skipped that day for exactly that
reason — memory is a rung below always-loaded prose, so there was nothing in context to
skip *from*. Detail: memory `feedback_background_test_runs.md`.

**Anti-drift.** Before any multi-step task, restate the goal and its success metric in one
line. If a step stops serving that metric, stop and ask. Require avg_r × (regime × session ×
combo) evidence before killing a strategy — demote, don't delete.

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

make status            # repo shape: file counts, always-loaded KB, MEMORY.md KB + bullets
make post-branch-checks  # the mechanical half of /post-branch (12 legs, ADVISORY)
make post-branch-text FILE=<path>  # screen a PR title/body pre-flip (GATES; FILE=- is stdin)
make sanity-checks       # the mechanical half of /sanity-check (7 legs, GATES, runs in CI)
make wait-ci PR=<n>      # wait on a PR's checks    (make wait-ci-main for main's push run)
make preflight           # ST45 clean-clone gate; /post-branch Step 7, REPLACES make test
```

**`make post-branch-checks` and `make sanity-checks` ARE the MECHANICAL walk — hand-walking
their legs is not.** They replace the shell blocks those two skills used to carry, which ran
only when a session remembered to copy them. ⚠ **Scoped on purpose: mechanical is not WHOLE.**
A green sweep carries none of the judgement in `/post-branch`'s later steps, and this sentence
said plain "ARE the walk" until 2026-08-25, when BOTH parallel sessions on one wave read it as
licence to substitute the sweep for the skill — neither was careless, and both had the rule in
context. The sweep now closes by naming the steps it does not cover (ST88), because the fix
belongs on reachability rather than on another rule. Deep reference
`.claude/context/tools.md`. Three rules ride them:

- **A SKIP is not a PASS.** `sanity_checks.py` degrades legs that need project imports to
  SKIPPED so the sweep stays CI-portable — which means a broken import looks exactly like a
  correct CI run. Confirm the legs RUN once locally after touching it.
- **`wait_ci.py`'s exit codes are invisible through `make`** (GNU make collapses any recipe
  failure to its own exit 2). Read the printed banner, or call the script directly.
- **A tool that imports from the repo bootstraps its own `sys.path` so a bare
  `python3 tools/<name>.py` works, and the GUARANTEE is a `test_bare_invocation_works`
  test, never the bootstrap line.** CI runs `sanity_checks.py` exactly that way, with **no**
  `PYTHONPATH`, while the Make targets set it — so a green `make sanity-checks` proves
  nothing about CI's invocation. That gap shipped a red CI on 2026-08-19 with every local
  gate green. ⛔ **Do not enumerate the tools here.** This bullet read "all three" and was
  wrong by omission twice: `distil_power.py` joined 2026-08-20 (ST59), the one tool a gate
  MANDATES running that died on the obvious invocation, then `route_dedup.py` and
  `route_reconcile.py` by 2026-08-26. ⚠ **Scoped, not blanket** — a tool importing nothing
  from the repo gets none (`tools/x_truncated.py`), where the line would be dead code
  masking the breakage the moment the first `analytics.*` import appears.

After adding a doc to `docs/audits/` or `docs/superpowers/specs/`, run `make docs-index` —
both `INDEX.md` files are generated and `tests/test_docs_index.py` fails until they are
current.

**A new audit must state its verdict as PROSE under a Verdict heading**, or CI fails
(`TestEveryNewAuditExposesItsVerdict`). A table, blockquote or `**Date:**` line under the
heading is deliberately rejected — each renders as a plausible-but-wrong verdict. **This is
not a style rule: an unparseable verdict is invisible to the check that asks whether anyone
OWNS it.** 17 pre-2026-08-17 audits are grandfathered in a frozen set that can only shrink.

**An audit whose verdict is ACTIONABLE needs a SoT row naming its filename** — the
`daily_check.py` tier-2 `audit verdicts` line reds until one exists, and a row recording
*where it was already satisfied* clears it just as well as building the thing. It exists
because `2026-06-26-structural-entry-sim-harness.md` returned **BUILD** — the only BUILD in
47 audits — and sat unbuilt for seven weeks in plain sight in a generated, test-enforced
index. (That BUILD was **withdrawn 2026-08-18** as a look-ahead artifact; the ownership
lesson stands, and the seven-week delay is now also the reason the defect went unfound.)
**The class: a research chain made of audits has an owner at every link except the
last**, because each link's owner is the next audit and the terminal recommendation is
production code. ⚠ Naming an *audit* file in the SoT is safe; the opposite direction — a
*spec* filename inside an audit — is what the spec-reconcile counter derives from, and
`docs_index.py` never reads the SoT, so the two cannot collide.

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
- `buibui brief` — daily market brief (levels/zones/regime/seasonality/pundit board), plus
  a bundle-level **bear score** read from BTC — how many of {50W SMA, 50W EMA, 200D EMA,
  200D SMA, 20W SMA, 21W EMA} the daily close sits below, 0-6, with the nearest average
  below price as the trigger. ⛔ **It is a NUMBER and must never gate, size or suppress
  anything**: ST53 measured the effect as a CLIFF at 6 (-3.25% mean, n=73 dates, Welch
  t=-4.76) but those dates span ~3 distinct bear markets, so **n_eff ≈ 3**. The renderer
  carries a "display only, never a gate" marker and a test pins it. ⚠ **The four WEEKLY
  averages are resampled from 1d, never read from the `1w` table, and the reason is
  LOOK-AHEAD rather than staleness.** Those bars were stale from 2026-06-08 until ST61a put
  every timeframe on the routine sync (2026-08-23), so the old second reason is gone — but
  the first is stronger: `sync` stores the FORMING bar on purpose and `ohlcv_all` has no
  `is_closed` column, so the newest `1w` row is an in-progress week for up to seven days
  and nothing in the schema says so. **Do not "simplify" this to read `1w` now that it is
  fresh.** The resample drops the in-progress week, and a week's average takes effect only
  from the following Monday; both halves are mutation-guarded, because with only the first
  the historical path behind `days_at_score` stayed unguarded (measured: 8 of 8 tests
  passed with the effective-date shift removed). A partial score is never reported — if any
  one average lacks history the whole block degrades to a health note, since a "3" out of
  four averages is a different statistic wearing the same label.
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
- ⚠ **Costs are MODELLED, not realised**, so every figure is an optimistic bound whose error
  runs one way. Raw reads exactly −1.0 = declared risk on both halves — but since ST68 that is a
  **MEASUREMENT, not a missing mechanism**: both books price a gapped fill through
  `analytics/backtest/fills.py`, and **0 of 3,028 resolved rows have ever gapped** (100% join
  coverage, closest approach **+0.0072R INSIDE** the level, p1 +0.045R, median +0.40R), because
  crypto's 24/7 tape keeps the open continuous with the prior close. ⚠ **The wifey fork's
  −0.2192 → −0.2553 does NOT transfer** — that is an equities artifact of overnight and weekend
  gaps, and it is why the 172 EQUITY/COMMODITY perps on Binance's rail would need this
  re-measured before any of them is traded.
- ⚠ Drag `= 2(fee+slip)·entry/risk` scales inversely with stop width, so **any live-ledger
  comparison between cells of differing stop width inherits a bias, not just a level
  shift** (ST27's runs favourably — wider stops carry less drag, so CONFIRMED-BAD is
  conservative).

### `buibui card SYMBOL`

AI trade card (F2). Composes brief panel (M1 indicators + M2 sessions) + pundit board + XS
target + recent fires + live account into a MarketState, sends the card-v5 rubric to
`claude -p` (subscription auth, keys stripped, `CLAUDE_CONFIG_DIR=~/.claude-personal`, bare
temp cwd), then a deterministic post-pass sizes the trade and enforces hard rules in code
(VETOED on violation, including a `valid_until_utc` that is unparseable or does not postdate
the card's own `generated_at_ms`). Wrapped by
`make buibui-card SYMBOL=BTCUSDT [DIRECTION=] [HORIZON=] [AS_OF=] [DRY=1] [TG=1] [CONFIG=]`.

- **Sizing.** Quantity is floored to the symbol's exchange LOT_SIZE step via
  `portfolio.sizing.round_down_to_step` — shared with `trade/routing.py` so card and XS
  router round identically — and `risk_usd` / `risk_frac` are restated from the ROUNDED
  size, so the printed risk is the risk actually taken. A sub-lot budget vetoes; an
  unreachable exchange degrades to an unrounded quantity **with** a warning.
- **The `min_rr` floor is NET of cost.** `rr_tp1_net = rr_tp1 −
  round_trip_drag_r(entry, sl)`, and the veto names both numbers. The drag carries
  `entry / risk`, so it is *inversely* proportional to stop width — a 2% stop pays
  0.07R, a 0.5% stop pays 0.28R. A gross floor therefore passed exactly the trades it
  should reject, and the bias ran ONE way. Gating on net strictly tightens it: net <
  gross always. `round_trip_drag_r` (`portfolio/sizing.py`) is the ONE spelling —
  `Trade.pnl_r` is its origin and deliberately does NOT delegate, because it uses the
  split form, which is not bit-identical in floating point and generated every golden.
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
- **card-v5 (2026-08-20) adds a four-angle steelman at step 4, BEFORE the decision** (htf
  counter · underweighted confluence · catalyst risk · the other trader), as a required
  `steelman` field: exactly four non-empty bullets on a TRADE, absent on a NO_TRADE. The
  count is PINNED so a skipped angle fails validation rather than reading as a card that
  argued all four, and the rubric keeps the source's own non-goal — it is not there to talk
  the card out of the trade, it is so the other side never surprises you. **Deliberately
  absent from the Telegram card**, where four more prose bullets against the 4096-char guard
  re-open the readability defect the medium-specific layout fixed; a test pins the omission.
  v5 also bars a JSON field path from generated prose (`range_state.pos 0.4955`): the number
  stays, the path goes. `ai-cards.jsonl` carries a v4/v5 break, and nothing in the tree reads
  that file, so it costs no consumer.
- **M4 external liquidity** (heatmap / liq-map clusters) enters as mapped liquidity with
  trust guards, capped at ONE confluence input. ⚠ **It is not horizon-filtered**, and every
  fresh capture is 24h or 1d, so **fix the capture set before adding a filter** or the block
  just empties. ⚠ **`window` IS in `load_external_state`'s dedup key** — the tuple is
  `(source, venue, scope, panel, window)` at `analytics/brief/external.py:236-242`, which is
  the opposite of the "selects on source/age/rows only" this file claimed until 2026-08-25.
  The conclusion survived the correction; the mechanism did not, and the difference bites:
  a dedup DIMENSION means two windows are two SURVIVING snapshots rather than one filtered
  out, so free-text variants (`"1 day"` vs `"1d"`) STACK instead of superseding — which is
  ST74, and why that item and this correction are one change. **Selection proper is symbol,
  schema, allowed source, not-in-the-future and `max_age_hours`; `window` never appears in
  it.**
- **Ledgers.** Every card appends to gitignored `docs/plans/ai-cards.jsonl`; TRADE cards
  dual-write a pundit-calls row (author `buibui_card`) so `make buibui-pundit-score` scores
  the AI with no scorer changes. `analytics/brief/pundit.py` excludes `source:"ai-card"`
  rows and drops `buibui_card` from the priors authors list, so the card is never an
  external pundit to itself.
- `--telegram` (`TG=1`) also pushes the rendered card to Telegram — **every verdict,
  VETOED included**, since a veto is how the daily-R breaker trip and the sub-lot capital
  wall become visible on the phone. Opt-in per run, mirroring `run-job.sh`'s per-job
  `TELEGRAM_ALWAYS=1`, so a batch of exploratory cards does not reach the phone unasked.
  `card/telegram.py` gives the MEDIUM its own layout rather than reusing `render_card`:
  aligned numbers inside `<pre>` (Telegram collapses space runs, so the columns need it),
  reasoning prose OUTSIDE it (`<pre>` never soft-wraps, so a paragraph in one forces
  horizontal scroll on a phone and buries the four numbers you act on). Escaping is
  `quote=False` — **Telegram decodes only `&lt;` `&gt;` `&amp;`, so an escaped apostrophe
  renders literally as `&#x27;`**. A 4096-char guard drops reasoning bullets rather than
  losing the whole message. The long/short badge comes from
  `signals.alert_formatter.DIRECTION_LABELS` (`LONG 🟢` / `SHORT 🔴`) — **imported, not
  restated**, so the two operator-facing renderers cannot drift.
- `--dry-run` prints state + prompt with no LLM call (and so never pushes).

### `make buibui-backup`

Wraps `deploy/backup-analytics.sh` — a verified local snapshot of `analytics.db` plus the
ledger files and directories reached by `LEDGERS`, `LEDGER_DIRS`, `EXTERNAL_LEDGERS` and
`EXTERNAL_LEDGER_DIRS`. **`LEDGERS` is a GLOB over `docs/plans/*` since 2026-08-20**, plus
four explicit entries outside that tree; the other three arrays remain enumerated. `WEEKLY=1` adds the parquet export, `DRY=1` reports only. A systemd
user timer runs it twice daily.

**Everything covered is gitignored and single-copy, so those four arrays ARE the only
copy.** `analytics.db` is gitignored and single-copy, and the committed `live_signal.duckdb`
is not a backup — its `signal_alert_outcomes` table has 0 rows.

- **Prefer a GLOB over a wholesale tree to an allowlist.** An allowlist over a single-copy
  tree defaults to UNCOVERED, so a new artifact stays invisible until someone diffs the
  backup against the live tree. Five audits each found a gap the previous one missed — a
  glob covers a new project's tree the day it appears, with nobody needing to notice.
  **`LEDGERS` finally took its own advice on 2026-08-20**, after a sixth diff found two
  more (a hand-run gate script beside the health check, and a hand-taken `.bak` of a
  covered ledger): it is now `docs/plans/*`, and `tests/test_backup_ledger_glob.py` asserts
  a file **named nowhere in the script** still lands in the snapshot. Both loops over the
  array — the dry-run report and the real copy — must expand the glob, or the report
  promises coverage the copy does not deliver; a test pins each independently.
- **Put a directory in `LEDGER_DIRS` / `EXTERNAL_LEDGER_DIRS`, never a file array.** The
  file loops are `[ -f ]`-guarded and skip a directory SILENTLY. This has now caused the
  same gap twice.
- **The `EXTERNAL_*` arrays take `src:dest` pairs** and land under `_external/` in the
  snapshot; the `LEDGERS` loop is `"$REPO/$f"`-relative and resolves an absolute path to
  nonsense. They cover `~/.claude-personal/`: `history.jsonl` (the account-level prompt log,
  the only record of a session that survives transcript cleanup, and what `budget.py`
  checks its own coverage against), every `projects/*/memory` tree (the cross-session
  knowledge base, in no git remote), the account-level `CLAUDE.md` + `settings.json`, and
  since 2026-08-19 `tools/` + `skills/` + `commands/`. **The tracker's own rollup is the
  irreplaceable one** — the tracker is blind to a deleted session unless it ran first, so
  that rollup holds weeks no transcript can rebuild. ⚠ **`.credentials.json` and
  `.claude.json` are deliberately EXCLUDED: the off-site leg rclone-syncs this root to a
  cloud drive, so anything added here is copied to a third party.**
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

- **The soft exit needs BOTH `SOFT_FAIL_RC=2` and `SuccessExitStatus=2`.** `run-job.sh`
  preserves the wrapped exit code, so declaring only the first leaves systemd recording a
  routine tier-2 red as a FAILED unit — measured 2026-08-25, the phone reading "ok, warnings"
  while `systemctl --user list-units --failed` held the job. ⇒ **Fixing one surface of a
  two-surface ambiguity relocates it rather than closing it.**
- **`TELEGRAM_ALWAYS=1` makes the report arrive EVERY day, green or red.** A failure-only
  contract is unfalsifiable: a dead timer and a healthy day look identical on the phone, and
  the delivery path is then only ever exercised on a red day. It stays **opt-in per job** in
  `run-job.sh` so the 15-minute signal-watch does not inherit it and send 96 messages a day.
- **Both push paths HTML-escape the body and wrap it in `<pre>`.** `utils/telegram.py` sends
  `parse_mode=HTML`, and an unescaped traceback (`line 33, in <module>`) is rejected 400 —
  so the failure alert failed on exactly the crashes it exists to report.
- **`tg_send` FOLDS the body to 46 columns, and folds BEFORE the 3400-byte cap.** `<pre>`
  preserves alignment by never soft-wrapping, so one over-wide line drags the whole report
  sideways on a phone. Folding after the cap would instead add its newlines on top of the
  budget the cap exists to hold. Both legs live in `tg_send` rather than at the call sites,
  so the success and failure paths cannot drift apart.

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
| `docs/agents/` | `surfaces.toml`, the repo-specific doc-surface list + budget thresholds the checkers and skills read | `context/tools.md` |

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

**Structural first-touch entries — NO, and the BUILD is WITHDRAWN**
(`docs/audits/2026-08-18-structural-touch-confirmation-causality.md`, superseding the
2026-06-26 entry-sim harness). Under confirmation causality all six (zone_type × direction)
cells return NO-EDGE at 1d and **all six flip sign** — fvg/long +0.540R → **−0.056**,
eqh_eql/long +0.407 → **−0.163**, bos/long +0.218 → **−0.156** — five with bootstrap CIs
excluding zero on the negative side, so this is confirmed-bad rather than a powered null.
**The +0.5R was a selection effect, not an edge:** every zone type is *defined* by a
condition on the bars after its formation bar, so a trade entered inside that window carries
a stop the zone's own definition guarantees is unreachable (FVG one bar — `low[i+1]` IS the
band's top edge; BOS five — no high in `i+1…i+5` exceeds the swing). A same-data
legacy-geometry control reproduces the filed table within 0.011R, so the flip is the geometry
alone. **Do not rebuild a `structural_touch` detector.** ⚠ The parent touch-decay audit
shares `index_touches` and is **superseded but unrecomputed** — treat its excursion result as
unverified. ⚠ The one live re-entry is a *different* hypothesis: whether impulse continuation
pays when entered legally at `i+2`.

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
**2.668×** and **1.452×**, so breadth buys almost nothing when the cross-section is nearly
one asset. ⚠ **The deflator is `sqrt(k / n_eff)`, never n_eff itself** — check any pair with
`n_eff × deflator² == k`, and note it RISES with k, so a pair that falls as k rises is
mis-assigned. **This line read 1.628× / 3.331× until 2026-08-21, which are real published
figures from the OTHER regime's panels** (`2026-08-12-multi-regime-validation-design.md`:
3 symbols at n_eff 1.13 → 1.628×, 15 at n_eff 1.35 → 3.331×) — so the defect was bull-panel
n_eff crossed with bear-panel deflators, and crosswise at that. **Never carry a deflator
between panels — run `effective_independent_series` rather than quoting one**, which is what
`docs/audits/2026-08-20-st56-wick-fill-anchor-power.md` concluded when it caught this clause
failing to reproduce. At k=25 n_eff 2.92 and deflator 2.926 coincide to three
digits, which is exactly why the conflation is invisible at the worked example above.

**Day-CLUSTERING is this same correction computed a second way, so apply one or the other in
any one place — never both** (ST80, `docs/audits/2026-08-25-st80-audit-guard-cluster-key.md`).
`effective_independent_series` is `n_eff = k / (1 + (k−1)·ρ)`; a design effect on a
fully-populated cross-section is `DEFF = 1 + (k−1)·ρ`, so `n_eff = k / DEFF` is the same line
read twice — at ρ=0.315, k=25 that is DEFF 8.56 and 25/8.56 = **2.921**, the filed figure to
three digits. **The rule is stronger than "do not conflate them": they are one phenomenon with
two estimators, living in different modules.** The overlap buys a free property — the book-day
rule above **enforces itself** under a day key, since one row per day is a singleton cluster,
ICC 0, DEFF 1, so the deflation is a no-op rather than something a reader must remember.
`analytics.research_guards.cluster` owns the design-effect route and
`analytics.forecast.effective_independent_series` the series route.

⚠ **`audit_guard` corrected NEITHER until 2026-08-25, and the CI leg was blind for a reason
worth carrying: no trade query feeding an `AuditCell` has an `ORDER BY`.** Its block bootstrap
resampled runs adjacent in DuckDB *storage* order, so a guard whose docstring claimed
serial-correlation awareness was ordering on nothing — and same-day cross-symbol rows are not
adjacent under any ordering. Measured on 125 cells / 166,384 distinct trades: trade-weighted
DEFF **4.991** (median only 1.670 — the deflation concentrates in the 15m cells that are 64.4%
of the ledger, so **the median is not the decision statistic**), and 70 of 125 cells read
significant where 52 survive. `AuditCell.cluster_key` is now REQUIRED and fails closed. **No
filed verdict moved: `powered_null` holds on 0 of 125 cells before and after.** ⚠ The unit is
`utc_day_keys`, a documented **lower bound** on a 24/7 tape — DEFF rises 1.508 (12h) → 1.670
(day) → 1.873 (2d) → 2.366 (1w) with no plateau — so read a design effect as a floor and a
surviving verdict as conservative. **Do not unify it with the equities fork's
`session_day_keys`**, which is exact there because RTH sits inside one UTC date.

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

**Give-back is REAL, and exit tuning stays CLOSED anyway**
(`docs/audits/2026-08-18-st17-giveback-heat-and-run.md`). Of the 1,790 live rows reaching
+1R, **28.9% finish at or below zero**; median give-back **+0.8131R** on a CI of
[+0.7153, +0.9455], median capture **0.554**. ⚠ **This does NOT re-open exit tuning as a
P&L lever** — the 2026-08-07 whole-book time-stop sweep is monotone in hold time and
negative at EVERY setting, so banking the give-back earlier was already tested and lost; a
large give-back is not evidence against that answer. ⚠ **The timeframe gradient (capture
+0.633 at 15m → −0.423 at 1d, stop width held constant) is the flat-2%-SL defect
resurfacing, not a new axis** — the stop is 2.000% at the median on EVERY tf while median
bars held runs 96/24/10/2, so a 1d trade is resolved in two bars by a stop tiny against a
daily range. Any trail/breakeven rule is a NEW construction inheriting the full three-leg
gate under its own pre-registration.

**A paired stop-geometry comparison is owned by its INTRABAR TIE-BREAK, not by its geometry**
(`docs/audits/2026-08-20-st56-wick-fill-anchor-result.md`, ST56). Testing `wick_fill`'s own wick
extreme against the geometry that discards it returned **INDETERMINATE at n=336,669** — not an
underpowered null. Resolving an ambiguous bar as the loss gives **−0.1072R** on [−0.1121, −0.1020];
resolving it as the win gives **+0.2386R** on [+0.2332, +0.2441]. Both CIs are tight, both exclude
zero, in opposite directions, and flipping the rule moves the difference **0.3458R — three times the
effect either reading claims**. The cause is structural: the wick arm's stop is tighter, the target
is derived from the stop distance, so both levels sit closer to entry and it hits **42,814** ambiguous
bars against production's **4,007**, a 10.7× asymmetry in exposure to the very rule being applied.
⚠ **This binds on any comparison where one arm is systematically tighter and ambiguity is resolved
from OHLC alone** — the flat-2% family, ATR-widening and ST17's capture gradient are all that shape,
so read a stop-width verdict resolved this way as unproven rather than settled; **how badly depends
on which way the bias runs, which the next paragraph settles per study.** ⚠ **More data cannot
fix it**: 73.9% of `wick_fill` fires are 15m and `analytics.db` holds nothing below 15m, so only the
26.1% at 1h and above is resolvable with held data. ⚠ **ST56 never tested the FALLBACK path** — entry
at the next bar's open leaves the wick on the correct side 336,697 of 337,294 times, so the flat
fallback never fires and the result speaks to the `min_sl_pct` FLOOR only. **Do not read it as
evidence the wick anchor works, or that it does not.**

**Which way the tie-break bias RUNS decides whether it is conservative or dangerous — name the
DIRECTION per study, never just its presence** (ST57, measured 2026-08-20 by reading the code, not
by re-running anything). This repo resolves every same-bar SL/TP tie ADVERSE-FIRST at three sites —
`analytics/backtest/engine.py:1073`, `analytics/exits/replay.py:12`, `analytics/exits/mfe_mae.py:17`
— so every `avg_r`, `win_rate` and star rating carries a uniform PESSIMISTIC bias: harmless within
one stop width, biased ACROSS widths, which bites because the flat-2% defect pins 78% of the ledger
at one width while other detectors sit elsewhere. **`analytics/giveback.py` is the exception and the
model to copy — it COUNTS `intrabar_ambiguous` (28 rows, 1.6%) instead of resolving it.** The tighter
arm eats more ambiguous bars, so adverse-first penalises the NARROW arm hardest and the bias
therefore FAVOURS widening. ⇒ **ST9/H11 ATR-widening (`analytics/sl_horizon.py` → `replay_exits`,
comparing `k ∈ 0.5…3.0`, a 6× width range) is EXONERATED: it returned CONFIRMED-BAD at 15m against
the thumb on the scale, so that verdict is conservative and stands STRENGTHENED.** ⚠ **The exposure
is FORWARD-looking, and it is why this paragraph exists: 1h and 4h are INSUFFICIENT — untested, not
cleared — so a re-run there returning "widening WORKS" lands in exactly the direction the bias
pushes and must not be believed without intrabar resolution, while a NULL there needs no such
discount.**

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

**`ohlcv` is a VIEW; `ohlcv_all` is the table, and `venue` is in its PRIMARY KEY.** Writes go
through `upsert_ohlcv(conn, df, *, venue=...)` — required and keyword-only, because a silent
default on this writer is the defect the whole design removes. Reads keep using `ohlcv` unchanged;
it resolves ONE row per `(symbol, timeframe, open_time)` from a preference order stored in
`db_meta.read_venue_order` (`binance` locally, `okx,binance` on the committed slim DB, so the CI
signal-watch keeps its deliberately MIXED series — Binance history underneath, OKX's synced tail on
top). **A `DATA_SOURCE=okx` run can no longer overwrite Binance bars.** Deep ref:
`docs/superpowers/specs/2026-08-21-st60b-ohlcv-venue-key-design.md`.

Four things still bite:

- **Any database created before this must be migrated ONCE** — `tools/migrate_ohlcv_venue.py`,
  which refuses without a backup under 24h old. `init_schema` raises `UnmigratedDatabaseError`
  until then. ⚠ **A FAILED `init_schema` leaves an empty `ohlcv_all` and `db_meta` behind**, so
  "is this migrated?" keys on whether `ohlcv` is still a TABLE (`duckdb_tables()` lists tables,
  never views) and **never on `ohlcv_all` existing**. Two guards were first written the other way
  and both were wrong — one refused to fire, the other refused to run.
- ⚠ **The laptop timers run the WORKING TREE**, so landing a schema change on a branch takes the
  live daemon down within 15 minutes. Migrate before landing it, or stop the timer first.
- **`FABRICATED_CVD_SQL` scans `ohlcv_all`, not the view** — it guards history across every venue,
  and the view would blind it. It is `SELECT DISTINCT` on purpose: two venues at one bar-time
  otherwise satisfy `neutral_cvd_runs`' adjacency test at **delta 0** and manufacture a false
  2-bar "run", flagging an isolated bar the 90-day signature would have ignored.
- **A caller that hardcodes its client must pass `venue=` explicitly.** `resolve_venue()` reads
  `DATA_SOURCE`, which is correct only for callers that pick their client the same way
  (`create_data_client()`). `analytics_runner.py` uses `create_client()` unconditionally and so
  pins `venue="binance"` at both call sites.

**The routine OHLCV refresh is SPLIT across two callers, and only their UNION is coverage.**
signal-watch's 15-minute timer syncs the `coins.json` majors on 15m/1h/4h; `make
buibui-universe-sync` syncs the 25-symbol universe on 1h/4h/1d/1w. Until 2026-08-23 the second
ran `--timeframes 1d` **alone**, so nothing at all covered the universe on 1h/4h/1w: measured at
the fix, **22 of 25 symbols were frozen 17.9d on 1h, 61.2d on 4h and 76.2d on 1w, every one of
them TRADING**, while every check stayed green — the tier-1 coverage line asks only about 1d,
the shape of the 2026-07-23 failure it was written for. ⚠ **Widening a detector or a study onto
a timeframe is therefore not enough; check the series is actually being REFRESHED.**
`tools/ohlcv_freshness.py` now watches every `(symbol, timeframe)` and reds tier 2 in the daily
check. Two properties of it are load-bearing: staleness is measured in **BAR units**, never
hours (three days is healthy on 1w and 72 bars behind on 1h, so one wall-clock threshold has to
pick a timeframe to be wrong about), and the tolerance is **per timeframe, set by the REFRESH
CADENCE rather than the bar length** — `tolerance_bars_for` returns `2 + gap/bar`, where the flat
2 absorbs the in-progress bar (`sync` stores the FORMING bar on purpose, and `ohlcv_all` has no
`is_closed` column to tell the two apart) and the second term is one whole refresh cycle. That
gives 20 bars on 1h, 6.5 on 4h, 2.75 on 1d, 3.0 on 15m. ⚠ **A flat 2 bars was WRONG for every
universe series and the error was invisible because it never lied about a majors one** (ST90,
2026-08-25): `buibui-xsmom-daily.timer` is the only thing scheduling a universe sync — 00:20 /
02:20 / 06:20 UTC — so its worst gap is the **18-hour overnight hole**, which is 18 bars on 1h.
Measured: 21 of 103 series flagged at 08:30 UTC purely because the 06:20 sync was two hours old,
the line red **~19 hours of every 24** and therefore red at every 09:10 UTC scheduled run. **A
permanently-red tier-2 line is worse than no line — it teaches its reader to skip it**, the same
failure the delisted-symbol exclusion already exists to prevent, reached by a different route.
⚠ **Widening a tolerance is the one change that can silently make a guard mute, so the teeth are
re-asserted against the NEW default**: `tests/test_ohlcv_freshness.py` replays the ST61a freeze
(1h 429.6 bars, 4h 367.2, 1w 10.9) and all three still fire. The majors take the universe cadence
too, deliberately — a majors freeze means the 15-minute timer is dead, which tier 1's own
`signal-watch` line watches. It scans the **view**, the opposite choice to
`FABRICATED_CVD_SQL`: that one guards history across venues, this one asks whether what
consumers READ is fresh.

**The containment is the point, and it was demonstrated on a bug introduced during the build:** a
mislabelled write now costs VISIBILITY — the rows land beside the real ones, invisible to the
default view, recoverable by re-backfilling — where before it cost DATA.

**Backtest run selection** — the `writer` argument, the `(sweep_id IS NOT NULL, run_at_ms)`
ranking both selection sites must keep mirroring, and `recalibrate_lib.select_rated_run_ids`'s
two scope arguments all ride the `backtest-run-id` card (how a card gets delivered:
`CLAUDE.md`). Two verdicts outlive the mechanism: before 2026-08-12 the live gate silently
replaced swept rows (**415 overwritten, 331 whose stored aggregate disagreed with their own
trades**, and **53% of rated `tue_thu` cells owned by the daemon** rather than the
deliberate sweep); and **every decay review before 2026-08-13 audited a pool frozen at
2026-04-09** — the verdict direction survived, which is why it stood, but every *named cell*
was wrong. The drift began in a gitignored driver → [[scratch-dir-is-for-output-not-code]].

⚠ **A namespacing fix closes the axis it was written for and NOTHING else — and this one
recurred (ST86, 2026-08-25).** `writer` fixed *who* wrote the row and left every engine axis
open: `upsert_backtest_run`, the only path that WRITES, forwarded 11 of the 19 axes
`_backtest_run_id` accepts and knew nothing of `live_parity`, so nine axes were unnamespaced.
All nine come from the TOMLs, so **every `/wfo-sweep` or `/atr-sweep` retune silently
overwrote the rows measured under the previous value.** ⇒ **Any new engine knob must join
the run_id in the same PR that adds it**, and the read path must follow the write path — once
rows stop colliding they COEXIST, so a partition or a rating scope blind to the axis picks
between two different books on recency alone. ⚠ **The collision was UNMEASURABLE from the DB
by construction** (the axis was never stored), so read the 1,301 rows whose aggregate
disagrees with their own trades as *unattributable* — 578 of them are the benign
sliding-window re-run.

⚠ **NEVER insert into `backtest_runs` / `backtest_trades` positionally — `INSERT ... SELECT`
maps by POSITION and this table has no single column order.** `long_total_r` /
`short_total_r` / `volume_suppress` are created inline by `init_schema` while
`adr_suppress_threshold` / `recovery_factor` arrive through the ALTER migration, so a fresh
DB orders them differently from a database predating the CREATE. Measured 2026-08-25: on a
fresh DB an `adr_suppress_threshold` of 0.8 was read back out of `long_total_r`, five columns
wide. **Production was the CORRECT half and every reclone, in-memory test DB and
`make preflight` clone was the wrong one** — which is why no gate ever reddened, and why the
four positional INSERTs in the tests missed it (one carried comments naming the order it does
not have). `_insert_sql` now names the columns, deriving both lists from one dict. **The
general gap outlives the fix: `analytics.db` is gitignored and single-copy, so NO gate has
ever run against production's schema — a clone-based gate can only ever exercise the fresh
shape.**

**The DSR family needs TWO floors, and `MIN_DSR_TRADES` is only the count one.**
`MIN_DSR_SD = 0.05` (ST66, `analytics/recalibrate_lib.py`) gates dispersion beside it: a cell
whose trades all resolved at the same R clears a count floor, then earns a Sharpe in the
hundreds because the denominator is ~0. That number is not a signal — it says every trade hit
the same stop. Both floors exclude a cell from the trial family AND leave it unscored, and
`_sharpe` still rejects `sd == 0.0` under `min_sd=0.0`, so passing `0.0` reproduces the
pre-ST66 result exactly rather than dividing by zero. `tools/decay_review.py` imports
`_sharpe`, so it inherits the floor; `tools/multi_regime_power.py` builds its OWN family and
excludes the cell explicitly on purpose.

⚠ **"A/B'd against a dispersion floor, production DSR did not move" is FALSIFIED — this file
carried that line, and it was the reason the floor was left out.** Measured on the live DB at
the fix (`day_filter=off`): one degenerate cell, `bos/1d` long, n=36, sd **0.00218**, Sharpe
**−461.3**, took the long-scope family variance from **0.0181 to 3937.38 — a factor of
217,405** and **zeroed all 18 scoreable long-scope DSRs**, `fib_golden_zone/4h/long` among
them at a true **0.9164**. The `tue_thu` scope and both non-long scopes hold no degenerate cell
and did not move at all, which is how a small-family A/B reads "no effect": **the defect is
one cell wide and takes a whole direction scope with it.** Read a DSR of exactly 0.0000 in
any pre-ST66 report as *possibly the artifact*, not the cell.

**No rating and no gate flipped.** The recalibrate DSR annotates; the live signal gate never
reads it, `compute_recalibrated_ratings` does not consume it, and every recovered value stays
under `DSR_SUSPECT_THRESHOLD` (0.95), so the suspect list is unchanged. The consumer that did
see it is `card/state.py` → `card/prompt.py`, where **`dsr < 0.95` reads as "weak"** — so the
AI card was told every long cell was weak on the strength of one cell. ⚠ **That cell's numbers
PRE-DATE the 2026-08-18 `bos` causality fix and nothing has re-run them** (no config declares
`bos` on `1d`), which is why the live pool still reproduces the filed −461 to the digit; the
ST63 occurrence dump re-derived the signature on post-fix data at −445 to −506, so the
mechanism is verified in both eras. A stale, un-rerunnable cell poisoning the live family is
an argument for the floor, not against it.

**Detectors must be CAUSAL, and `tests/test_lookahead.py` is the gate.** It feeds each
`DETECTOR_REGISTRY` entry the series truncated at `t` and asserts the signals emitted *at*
`t` are byte-identical to the full-series run. Two controls, and both are load-bearing: an
injected peeking detector that MUST be flagged (teeth) and an injected causal one that must
NOT be (specificity) — a harness with only the first cannot tell "clean" from "blind", which
is how the xsmom causality guard passed under mutation. Truncation points below
`_MIN_HISTORY_BARS = 200` (production's `scan_window`) are reported UNTESTED rather than
passing, because a detector refusing to run inside its own warmup is not a lookahead. **A
cell that skips is untested, not clean** — `eqh_eql` emits ONE signal on the 1d fixture, so
its pass there is worth nothing on its own. Found two leaks on first run: `bos` at **100%**
of signals (it stamped at the swing bar, whose `center=True` window reads `i+1…i+5`) and
`liquidity_sweep` at 23–42% (it referenced pivots from the same window before they were
confirmed, and its own comment called that "acceptable lookahead … consistent with
`detect_eqh_eql`" — the consistency half was false as measured). Both fixed 2026-08-18.
**The cost of the `bos` leak was +0.35R/trade** at fixed data with gates off, and it flipped
the sign at 15m/1h/4h; with live-parity gates on it also cut `bos` trade count ~80%, because
the gates had been reading context five bars stale. `weekdays bos/4h/short` fell **★4
+0.7435 → ★2 +0.0449**, and an isolation run (old code, current data) attributed **−0.6986
of −0.6986 to the fix and −0.0000 to the data** — the best-rated `bos` cell in the book was
almost entirely the leak. ⚠ **Those star ratings are AS OF the 2026-08-18 fix measurement and
have since drifted** — the 2026-08-19 21:37 recalibrate reads that cell at **★1 −0.0658**, and
`weekdays bos/1h/long` at −0.7485 rather than the filed −0.7113. **Do not "correct" the pair
above to today's numbers:** it is a dated before/after attribution of one code change, and
overwriting the *after* with a later, more-data reading destroys the comparison while looking
like an update. Quote it with its date, and re-read `confidence_ratings` for a current value.

**`make regression-update` is NOT data-neutral — it refreshes the fixtures first.** It runs
`scripts/extract_regression_fixture.py`, which re-exports the OHLCV parquets from the LIVE
`analytics.db`, and only then regenerates the goldens. So "regenerate the goldens" silently
bundles however much data has accrued since the last run (measured 2026-08-18: 15m
28,662 → 32,694 rows, ~42 days) and reads the DB while the 15-minute timer owns it. **To
isolate a code change in the golden diff, run `pytest tests/test_regression.py
--update-golden` directly** and leave the parquets pinned; use the full target when a data
refresh is what you actually want.

**Lot-size rounding** — `portfolio/sizing.py::round_down_to_step` snaps before it floors;
why that snap is load-bearing rides the `sizing-round-down` card.

**R:R comes from `analytics.signal._common.realised_rr`, never from the requested `tp_r`.**
Three sites need it — the ledger writer, the outcome resolver and `alert_formatter.py` — so
import it rather than re-deriving. A structural TP is the detector's own level, which `tp_r`
never fed, so storing the request describes a target the row does not carry: that shipped as
ST39 (29 live rows, unanimously over-crediting wins) **while a test asserted the wrong value
as correct**, which is why the gates were green throughout. Deep ref
`.claude/context/analytics.md`; the migration is `tools/restate_rr_ratio.py`.

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
anywhere; **all four coded verdicts delegate to it** (`xs_`, `combine_`, `cvd_`,
`carry_gate_verdict`). ⚠ Naming only two of them is how `carry_gate_verdict` kept an inline
restatement until 2026-08-19: the two forms agreed on every input, so **agreement by
coincidence read exactly like agreement by construction** and no test could fail.
`tests/test_research_guards_gate.py::TestEverySleeveDelegates` now asks all four the same
questions. `min_trl` is
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

- Runtime: `duckdb`, `pandas`, `pyarrow`, `yt-dlp` (**a NIGHTLY pin — see below**), `yt-dlp-ejs`
- Dev: ruff, mypy, pytest, pytest-mock, pre-commit, type stubs, pandas-stubs

⚠ **`yt-dlp` is pinned to a NIGHTLY pre-release on purpose** (`>=2026.8.18.122307.dev0`, with
`allow-prereleases = true`). Stable `2026.7.4` resolves ONLY the `android_vr` player client for
YouTube, and that client's media URLs return **HTTP 403 on every download**; `player_client` =
`web`/`tv`/`web_safari`/`mweb`/`ios` return **no formats at all**, so there is no flag workaround
on stable. The nightly uses `visionos` and works. **One failure, three symptoms:** captions, the
Groq whisper fallback and frame extraction ALL sit downstream of the media leg, so a 403 there
presents as a dead vision pass rather than as a download problem (that is SoT ST41, which cost a
whole ingest round before anyone looked at the dependency). **The pin WILL go stale** — YouTube
breaks yt-dlp every few weeks; re-bump, and return to a stable constraint once one carries the
fix. **The daily check now WATCHES the pin** — `tools/media_probe.py` fetches a 19-second
canary through production's own download call and reds tier 2 when the media leg dies, so the
next break surfaces on the phone rather than as a degraded ingest round found by hand.

⚠ **`--dump-json` already returns `chapters`, `subtitles` and `automatic_captions` on the call
`fetch_meta` ALREADY MAKES, so using them costs PARSING, not quota** (ST46, 2026-08-20). Two
verdicts came out of wiring them up, and the second is the one that mattered:

- **yt-dlp returns `language: null` on a large slice of the follow list — measured null on 28
  of 28 at-risk videos.** `meta.lang` was then `""`, so `fetch_transcript` asked for
  `--sub-langs en` ALONE; yt-dlp answered "There are no subtitles for the requested languages",
  wrote no file, and the video fell through to Groq ASR **while an author-written track sat
  there unrequested**. Measured across the ingested corpus: **17 of 89 notes were built from
  ASR that way**, all on zh channels — exactly where ASR is weakest and `raw_quote` accuracy is
  load-bearing. The other 11 genuinely had no captions, so the ASR fallback itself is sound;
  it was being reached for the wrong reason. ⚠ **The widening then REGRESSED in the opposite
  direction and was re-fixed 2026-08-20**: on a channel whose `meta.lang` is a REGIONAL
  variant (`en-US`) matching no caption code, it requested the whole auto-translate matrix —
  measured **157** codes on `9avrSmPczP4`, which YouTube answers `HTTP 429` partway through,
  so whichever file survived the rate limit decided the language and an Afar machine
  translation was ingested as `auto_captions`. `_sub_langs` now resolves a regional lang to
  its base track (`en-US` → `en`) and **caps** the request, and `_select_caption_track`
  REFUSES a track unrelated to a known `meta.lang` rather than taking the alphabetically
  first one — ASR in the real language beats a machine translation into another. ⚠ **Those 17 notes are a COVERAGE defect and their
  `raw_quote`s are unverified** — a re-ingest is the only repair, per `CLAUDE.md`'s rule.
- **Caption provenance is now recorded** (`transcript_source`: `manual_captions` /
  `auto_captions` / `asr_whisper` / `captions_unknown`). It could not be recovered downstream
  because `--write-subs` and `--write-auto-subs` both land as `sub.<code>.vtt`; the two
  `--dump-json` mappings are the only signal. Before this it was **model-narrated on 2 of 89
  notes**. `captions_unknown` is deliberately not folded into `auto` — "we did not ask" and "we
  asked and it was ASR" are different claims.
- **`recap_window_s` reads the video's own leading recap chapter** and OVERRIDES the per-channel
  `intro_recap_s`, in both directions. Measured on `4Dkw1jz04lY`: @GiantCutie-K's configured
  120s against a recap chapter running to 186s, so 66s of recap read as fresh content. Only a
  LEADING recap counts — a mid-video 回顧 is a different thing — and chapters are absent on
  ~50% of the corpus, so degrading to the constant is the common path, not the edge case.
  ⚠ **Position is only half the test; the TITLE has to say recap, and `_RECAP_TITLE_HINTS` IS
  that half.** A hint meaning merely "the video starts here" makes every such video's opening
  a recap: `intro` was one until 2026-08-25 (ST85) and cost `VC4FdM78hI8` its first 26% — a
  leading `Intro` chapter, 0-295s of 1128s, overrode a configured `intro_recap_s: 0`. An
  introduction OPENS content, a recap REPLAYS prior calls, and the failure is silent and
  one-directional: a false positive DROPS Stream C setups, the only stream carrying dated
  calls, while a miss merely falls back to the channel constant. `review` and `概述` are the
  same shape and unmeasured — read a sighting on either as this defect, not a new one.

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
  which costs `/ingest-video` the whole vision pass. ⚠ **Both halves being PRESENT does
  not rule the 403 out** — on 2026-08-18 `node` v22.23.1 and `yt-dlp-ejs` were installed
  and every media download still 403’d, because the cause was the yt-dlp build itself.
  Check the version before re-checking these two.
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

## Git Conventions

- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Never commit `.env`, `config/coins.json`, or IDE files

### PR titles

**Squash-merge makes the PR title the permanent commit message**, so it is the line every
future `git log`, blame and bisect reads. Name the MECHANISM you changed, not the symptom
you noticed.

| Anti-pattern | Instead |
| --- | --- |
| the symptom (`fix: alerts look wrong`) | the mechanism (`fix: R:R read the requested tp_r, not the realised fill`) |
| the file (`fix: update sizing.py`) | the behaviour (`fix: floor size to LOT_SIZE before restating risk_usd`) |
| a refactor verb on a real fix (`refactor: tidy the detector`) | say it fixed something (`fix: stamp bos at its confirmation bar`) — a `refactor:` title hides a behaviour change from anyone bisecting |
| vague `improve` / `update` / `various` | the one thing that changed; if there are genuinely several, the PR is too big |

A title needing "and" twice is usually two PRs.

### CI quota — the visibility flip

**This is a private repo on the free tier and Actions minutes are a hard budget.** Public
repos get unlimited free standard-runner minutes, which is the only way to get real CI here.
So: **flip the repo public before opening a PR, and back to private once it merges.**

⚠ **Confirm the flip with the user each time.** Standing authorisation covers the
**mechanics**, never the **timing** — the window publishes this repo's whole history for its
duration, and only the operator knows whether now is a good moment. Separating the two is
what keeps the ask useful rather than nagging: never re-ask a settled question, always ask
the unsettled one.

```bash
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-moon-trader-bot \
  --visibility public --accept-visibility-change-consequences
# ...open PR, let CI run, merge...
GH_TOKEN=$(gh auth token --user s10023) gh repo edit s10023/buibui-moon-trader-bot \
  --visibility private --accept-visibility-change-consequences
```

⚠ **THE OPERATOR RUNS BOTH COMMANDS — a session cannot.** `gh repo edit --visibility` is
blocked by the permission classifier in BOTH directions, so hand the command over and WAIT
rather than discovering it mid-chain (#669: it surfaced with the branch pushed and the PR
body already written). Confirm with `gh repo view … --json visibility` — a read, not
blocked. Never assume the flip happened because you printed the command.

- **Pushing a branch costs no CI** — `push:` triggers only on `main` and `pull_request:`
  only on a PR. Commit and push freely; the meter starts at `gh pr create`.
- **Never flip back while ANY run on `main` is `in_progress`** — not "wait until the jobs
  exist". Flipping kills jobs *created after* it, and a chained job is not created until its
  dependency finishes: `lint.yaml:157`'s `regression` job declares `needs:
  lint-typecheck-test`, so a main run sits in_progress with three jobs created and
  "Regression tests" not yet existing. Flip there and main reds for billing. **Run
  `make wait-ci-main`** rather than hand-rolling a waiter — it gates on a job-count floor
  for exactly this reason. ⚠ **But it watches ONE workflow**: the floor is `CI`'s, while
  the flip hits every workflow, so one starting after CI settles is invisible to it — it has
  already gone latent once, with `Dependency Graph` in_progress at the flip and not biting.
  `gh run list --branch main --limit 5 --json workflowName,status` closes it in one call.
  ⚠ **That call is NECESSARY BUT NOT SUFFICIENT, so the rule is check → flip → RE-VERIFY.** A
  listing cannot see a workflow that does not yet EXIST: the pre-flip listing reads clean on
  every workflow, the operator flips, and `Dependency Graph` is created on the merge SHA *after*
  the check. **Same vacuous-check shape at THREE NESTED LAYERS** (a count of layers, not of
  sightings) — a chained job does not exist until its dependency ends · `wait-ci-main` watches
  ONE workflow and cannot see a sibling · a listing of ALL workflows cannot see one not yet
  created. **The pattern to carry: a check is only ever true about the scope it looked at, at
  the moment it looked.** ⚠ **The outermost layer is not a one-off curiosity — it recurs on the
  flip-back, and every sighting so far was caught by the post-flip re-verify and by nothing
  else**, because a pre-flip check cannot see a run that does not yet exist. That makes the
  re-verify the ONLY step in the sequence able to catch this class, never a belt-and-braces
  extra. ⚠ **The running count and its derivation live in ONE place — memory
  `reference_dependency_graph_sighting_count.md`. Quote that file; state no number here.** This
  file enumerated the sightings until 2026-08-24 while the handoff recorded later ones, and the
  split is not a tidiness problem: a session that re-derived the count from the enumeration
  here — the careful move — got the wrong answer, because the missing sightings were not in the
  file it checked. Every sighting has also been benign because `Dependency Graph` runs green on
  a private repo and burns no allowance — read that as luck about WHICH workflow started late,
  never as safety of the check, since `security-scan` (Trivy) in that slot consumes minutes and
  dies.
- **What makes a SHARED window safe is concurrency, not the rule.** `cancel-in-progress` is
  `${{ github.event_name == 'pull_request' }}` — false on push — so main runs QUEUE and a
  second merge's CI cannot start until the first finishes, chained job and all. The group is
  keyed on `${{ github.workflow }}`, so that protection is **per workflow** too, which is the
  same scope gap as the waiter above. ⚠ **A repo without this has none of it — check the
  wifey fork rather than assuming it inherits.**
- **A merge-run failure at ~3s with `steps=0` and `visibility=PRIVATE` is billing.** Verify
  duration, visibility and step count, then merge. Never debug it. ⚠ **`wait_ci.py` reports
  `steps` as EXECUTED/DECLARED, and only the executed half means anything to a reader.** A
  paths-filtered job declares its whole step list on every diff and skips the body, so the
  DECLARED count reads backwards: #670's docs-only PR declared 14 steps in
  `lint-typecheck-test` and executed 5, which as a bare `steps=14` says "the heavy leg ran on
  a docs diff" and contradicts the filter list below — the banner was wrong, not this file.
  The billing test is unaffected, since an exhausted allowance declares nothing.
- **What decides the flip is which jobs execute real steps on THIS diff — state the filter,
  never the file extension.** The heavy leg (`lint-typecheck-test`: ruff + mypy + the suite)
  sits behind `dorny/paths-filter` on `**/*.py`, `pyproject.toml`, `poetry.lock` and
  `lint.yaml`, so a `deploy/*.sh`, `.claude/**` or `docs/` diff skips it entirely;
  `.claude/skills/**` adds the SKILL.md frontmatter validator, `web/ui/**` the frontend
  check, and `Dockerfile` / `docker-compose.yml` the image build. There is no shellcheck
  and no deploy-aware job.
  ⚠ **But nothing rides entirely free, and "docs-only" is the trap**: `security-scan`
  (Trivy) carries no paths filter, and the `markdownlint` job runs `sanity_checks.py` and
  `test_context_guard.py` UNCONDITIONALLY — so skipping the flip on a docs PR skips the
  doc-drift gate, the one check that diff most needs. `make lint-md` reproduces
  markdownlint locally and `make sanity-checks` the doc-drift legs; nothing local
  reproduces Trivy.

⚠ **The flip publishes the ENTIRE HISTORY, not `HEAD`.** Scrubbing a name in a later commit
does NOT unexpose it, and `git grep` on the working tree agrees with every other review
surface while a deleted blob stays reachable. **The pre-flip gate is the `sensitive-terms`
leg of `make post-branch-checks`**, reading a term list from gitignored
`.claude/sensitive-terms.txt` (now in the backup's `LEDGER_DIRS`). A missing list reports
**NOT CONFIGURED as a FINDING, never a SKIP** — before a flip, "did not run" and "passed"
must not look alike. **A secret scan is NOT an exposure scan: only the operator can classify
an employer, client or work-repo name, so ASK rather than clearing one yourself** — that
mistake shipped on 2026-08-19 from a scan that checked keys, tokens, emails and paths, saw
two work-repo names, and cleared them. **Baseline ACCEPTED the same day:** two such names sit
in three deleted spec docs from 2026-04-25/05-07 and every flip since has republished them;
the ruling is accept-and-document, which is why the gate scopes to the tracked tree and the
branch's own commits rather than re-reporting main. → memory `public_repo_exposure_audit.md`

⚠ **That leg does not read the PR title or body — `make post-branch-text FILE=<path>` does**
(`FILE=-` reads stdin, so a title pipes straight in). Its three git legs ask the tracked tree,
this branch's commit content and its commit messages; a composed body is none of the three, so
the sweep reports `clean` on one naming every term — correctly, and uselessly. It is the fourth
surface and **the only INDEXABLE one**: the flip publishes a repo, but a PR body is served,
crawled and cached on its own. Screen it at `/post-branch` Step 7, before `gh pr create`, in
the same breath as the flip decision. Unlike the sweep it **GATES** — ⚠ through `make` the exit
code is make's own 2, never the tool's 1, so read the banner, as with `wait-ci` and `preflight`;
an unreadable `FILE` also exits 2 rather than rendering as a clean one-check run. Findings are
line numbers plus a masked term and never the matching line.

⚠ **Run `make post-branch-checks` from a WORKTREE and this gate reports NOT CONFIGURED every
time.** A worktree is a tracked-files-only checkout and `.claude/sensitive-terms.txt` is
gitignored, so the list simply is not there — the finding is honest, but it fires on the setup
rather than on the branch, and a session that learns to expect it stops reading it. **Copy the
list into the worktree before the pre-flip check** (`git check-ignore` confirms it still cannot
enter a commit). Measured by the peer on `feat/st45-clone-preflight`. This lives here, beside
the gate it defeats, rather than with the worktree notes — the rule that matters is the one
about "did not run" and "passed" not looking alike, and it is stated one paragraph up.

Ported from the wifey fork 2026-08-19, where both halves of the pair are gated in
`/post-branch` (flip-forward before `gh pr create`, flip-back after the merge run).

**Invoke `/post-branch` on every branch, and SPLIT it around `gh pr create`.** Steps 1–5b
and 7 (behaviour gate → changed artifacts → doc walk → surface checks → MEMORY.md → SoT
reconcile → commit and push) are commit-producing and run **before** `gh pr create`, so the
doc fixes ship in the initial push. Steps 6 and 10a/10c (PR body, handoff) need the PR to
exist and run **after**; they produce no commits. Step 10b writes a gitignored file and is
free either way.

Invoking it is neither optional nor conditional — the skill's own Step 1 behaviour gate
decides whether a docs sweep is warranted, so invoking it on a pure refactor costs one cheap
check.

**Step 7 also runs `make preflight`** — clone the branch's committed HEAD with
`--no-hardlinks`, `poetry install --no-root` (6.69s warm), and the suite. It is the third
answer to a gitignored path that exists on the dev box and nowhere else (#586, #666), and the
first one that is a mechanism rather than prose: the standing rule "pass every path
explicitly" would NOT have caught #666, because the defect was **production code loading a
config it never reads**, so a fixture `--config` goes green while the CLI stays broken on a
clean clone. **CI already is this gate — the gap it closes is TIMING**, since detection after
a push costs a metered cycle, a red PR and a visibility flip just to read the failure. ⚠ It
does not catch an *absolute* `$HOME` default (identical in the clone — `EXTERNAL_LEDGERS` in
`deploy/backup-analytics.sh` is that shape), nor any CLI branch no test reaches.

**Why the split is load-bearing:** running the doc walk after PR creation pushes a fix onto
an open PR, and every such push re-runs all CI (`pull_request: synchronize`) — ~3000 tests
plus a 93s regression job for one paragraph.

`.claude/settings.json` backstops this paragraph with a hook pair on `Bash`, and both now
**survive a fresh clone** (they were gitignored until 2026-08-19). The `PreToolUse` leg
fires *before* `gh pr create`, which is the useful one; the `PostToolUse` leg fires after,
as a catch. Both anchor on `head -1` plus `(^|[;&|()]|&&)[[:space:]]*gh[[:space:]]+pr`
`[[:space:]]+create`, so a `grep` or heredoc merely *containing* the string no longer
self-triggers. Both are advisory and neither blocks.
