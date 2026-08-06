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
- `buibui signal watch | test` — live signal daemon / historical replay. `watch` with no `--config` auto-picks today's config by **UTC weekday** (Mon/Fri→`signal_watch_weekdays.toml`, Tue–Thu→`signal_watch.toml`, Sat/Sun→`signal_watch_all.toml`); the three configs partition the calendar without overlap. UTC (not local) so the picker matches the `day_filter` scope on each candle's UTC `open_time`. `watch --once` runs a single scan cycle and exits (cron / GitHub Actions entry). `watch --catch-up` (SoT N6, off by default) replays every un-alerted **closed** candle since the last run instead of only the newest, so a missed or skipped cycle no longer loses those ledger rows permanently — the live ledger is the OOS evidence base, and gaps in it are a *biased* sample, not merely a thinner one (the hourly GH-Actions cron measured 35% delivery with a session-skewed run-hour distribution, p<0.01). Backfilled candles are persisted + watermarked but **never sent to Telegram** (a signal that old is not tradeable); only the newest closed candle can alert. A cold-start guard (`CooldownStore.last_marked()`) stops a fresh `signal_state.json` bursting the whole window on first contact. Recovery depth is bounded by the 200-candle `_SCAN_WINDOW` (~8 days on 1h, ~33 on 4h, ~200 on 1d), and gating context (regime / HTF-EMA / ADR / DOW / `confidence_ratings`) is computed **as-of-now** and applied to historical candles — a best-effort approximation good for days, not months, so deep backfills past a ratings refresh are look-ahead in the *gating* and should be treated as backtest, not OOS. `CATCH_UP=1` on `make buibui-signal-watch`. `DATA_SOURCE=okx` env selects the keyless OKX adapter (`utils/okx_client.py`) instead of Binance — used by `.github/workflows/signal-watch.yaml`, which seeds an ephemeral `analytics.db` from the committed slim `live_signal.duckdb` (`make export-live-db`); local default `DATA_SOURCE=binance` is unaffected
- `buibui analytics backfill | sync` — OHLCV ingestion; `--universe` (mutually exclusive with `--symbols`) reads the committed 25-perp research set from `config/universe.toml`; `make universe-backfill` wraps the deep 1h/4h/1d/1w run since 2019
- `buibui backtest` — run/save backtests (sweep, combo, cross-TF modes)
- `buibui digest` — pre-canned analytics queries
- `buibui param-audit | param-sweep` — WFO parameter tools
- `buibui recalibrate` — refresh star ratings
- `buibui portfolio replay` — replay the live outcome ledger through the Carver two-layer sizing model into a paper portfolio (read-only); prints Sharpe/Sortino/max-DD/attribution. Wrapped by `make buibui-portfolio-replay` (`CONFIG=`/`CAPITAL=`/`VOL_TARGET=` overrides)
- `buibui brief` — daily market brief (levels/zones/regime/seasonality/pundit board; `make buibui-brief`)
- `buibui card SYMBOL` — AI trade card (F2): composes brief panel (incl. M1 indicators + M2 sessions) + pundit board + XS target + recent fires + live account into a MarketState, sends the card-v3 rubric (M4: external heatmap/liq-map clusters as mapped liquidity w/ trust guards, capped at ONE confluence input; humanizer style block for generated prose; card-v3: each fire carries its live-ledger record alongside the backtest star, and live wins on conflict at n≥10) to `claude -p` (subscription auth, keys stripped, CLAUDE_CONFIG_DIR=~/.claude-personal, bare temp cwd), then a deterministic post-pass sizes the trade (P1 sizing reuse; the quantity is floored to the symbol's exchange LOT_SIZE step via `portfolio.sizing.round_down_to_step` — shared with `trade/routing.py` so card and XS router round identically — and `risk_usd`/`risk_frac` are restated from the ROUNDED size, so the printed risk is the risk actually taken; a sub-lot budget vetoes, and an unreachable exchange degrades to an unrounded quantity **with** a warning) and enforces hard rules in code (VETOED on violation, including a `valid_until_utc` that is unparseable or does not postdate the card's own `generated_at_ms`). Every card appends to gitignored `docs/plans/ai-cards.jsonl`; TRADE cards dual-write a pundit-calls row (author `buibui_card`, horizon `intraday`) so `make buibui-pundit-score` scores the AI with zero scorer changes; the pundit board (`analytics/brief/pundit.py`) excludes these `source:"ai-card"` rows (and drops `buibui_card` from the priors authors list) so the card is never an external pundit to itself. `--dry-run` prints state + prompt with no LLM call. **`--as-of` pins the INPUTS, not the card**: it admits only bars that had CLOSED at the anchor (`recent_fires` is bounded on bar close, and the weekly path drops the still-forming bar) and omits the live account, which is unpinnable because Binance serves only current positions/equity — the state records a health note saying which. The model stays nondeterministic, so two runs on a byte-identical `state_digest` have returned opposite directions; a pinned anchor makes a comparison possible, never conclusive at n=1. Wrapped by `make buibui-card SYMBOL=BTCUSDT [DIRECTION=] [AS_OF=] [DRY=1] [CONFIG=]`.
- `buibui web` — start FastAPI backend

Each Makefile `buibui-*` target wraps the equivalent CLI invocation.

## Project Structure

Package-level map. **The deep reference lives in `.claude/context/`, and the pointers
below are the only path a session has to it — follow them before working in an area.**
Verdicts and footguns stay HERE on purpose: they are the guard rail against
re-litigating settled research, and a guard rail behind a pointer is not a guard rail.

| Package | What it is | Deep reference |
| --- | --- | --- |
| `buibui.py` · `cli/` | Thin CLI entry shim delegating to `cli.main:main`; argparse subcommand package (`monitor` / `signal` / `analytics` / `backtest` / `digest` / `param` / `recalibrate` / `web`) | — |
| `analytics/` | Analytics data layer (DuckDB): `store/`, `strategies/`, `backtest/`, `signal/`, `stats/`, `brief/`, `exits/`, `research_guards/` + the audit libs | `context/analytics.md` |
| `analytics/{forecast,xsmom,combine,carry,xsrev}/` | The P2/P3 research sleeves — **verdicts below** | `context/research-sleeves.md` |
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
| `forecast/` EWMAC trend | +0.36 — structurally real but FAILS the gate (CI includes 0). **SHELVED** as a diversifier candidate |
| `combine/` trend×XS | +1.145, clears the gate but is **Sharpe-dominated by XS-solo**. A validated socket awaiting a comparably strong second edge |
| `carry/` funding carry | +0.03, FAILS, not cost-robust past 8bps. **SHELVED** |
| `xsrev/` XS reversal | **−2.9, negative even at ZERO cost. SHELVED** — the additivity thesis is falsified, momentum continues down to 2–7d |

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
each spec against its implementation can. **5 of 46 spec docs have been
reconciled** (H8, xsmom, the ingest-video design doc, + P2-EWMAC and
P3-trend×XS-combine on 2026-08-06 — verdict
`docs/audits/2026-08-06-spec-reconcile-p2-ewmac-p3-combine.md`).
**The denominator is the FULL corpus of 46**, decided 2026-08-06: a spec with
nothing to reconcile still costs someone a look to confirm that, and an
"implemented-only" denominator needs a per-spec judgement call nothing records.
`p3-cross-sectional-momentum-sleeve-design.md` (the deploy core) is the
highest-stakes one still unchecked.

**The published gate is THREE legs, not four — `DSR ≥ 0.95 ∧ PBO ≤ 0.5 ∧
boot_lo > 0`.** All five sleeves implement exactly this; `min_trl` is computed
and printed as a stamp but gates nothing. **P2's spec §1/§6 say "MinTRL gated"
and are wrong** — and the deploy core would not clear a MinTRL leg (needs ~7035
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

## Git Conventions

- Commit messages use conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `build:`, `chore:`
- Branch naming: `feat/`, `fix/`, `docs/`, `chore/`
- Do not commit `.env`, `config/coins.json`, or IDE-specific files
- **After `gh pr create` succeeds, invoke `/post-branch` before reporting the PR
  URL back to the user.** Not optional and not conditional — the skill's own Step 1
  behaviour gate decides whether a docs sweep is warranted, so invoking it on a pure
  refactor costs one cheap check. This line exists because the skills table's generic
  "use them proactively" demonstrably was not enough: on 2026-08-03 `/post-branch`
  fired **zero times across three PRs** (#524, #525, #526) on the main thread, by the
  same agent that had spent that session repairing the skill. A `PostToolUse` hook on
  `Bash` in `.claude/settings.json` backs this up with a harness-emitted reminder — but
  that file is gitignored (like `guard-destructive.py`), so on a fresh clone **this
  paragraph is the only enforcement that survives**. Re-add the hook if you reclone.
