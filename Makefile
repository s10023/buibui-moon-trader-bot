SORT ?= default
SYMBOL ?= BTCUSDT
STRATEGY ?= fvg
INTERVAL ?= 4h
DAYS ?= 90
SAVE ?=
PORT ?= 8000
# Loopback by default: the dashboard serves live positions and account data, so
# exposing it beyond this machine is an explicit opt-in (WEB_HOST=0.0.0.0). Not
# named HOST, which zsh and some environments already set to the machine name.
WEB_HOST ?= 127.0.0.1
DEV_PORT ?= 5173
# Makefile — Lint Markdown and Python

# ⚠ Windows defaults `sys.stdout` to the ANSI codepage (cp1252 here), so ANY target
# whose tool prints a non-ASCII character dies on `UnicodeEncodeError` -- AFTER doing
# its work. Measured 2026-09-18: `make buibui-xsmom-daily` completed the universe
# sync, then crashed printing `⛔` at tools/xsmom_execute.py:429, so the run reported
# failure having already succeeded. **47 tools under `tools/` and `cli/` print
# non-ASCII**, so this belongs here rather than at 47 call sites.
#
# The scheduled jobs were never exposed: `deploy/windows/job.sh` already exports the
# same variable, for the same reason. This closes the HAND-RUN half, which is the one
# a session and the operator actually use. A no-op on Linux, where UTF-8 is already
# the default. `tests/test_utf8_output.py` pins BOTH surfaces -- coverage is the
# union of the callers.
#
# ⚠ UTF-8 mode also flips the default FILE encoding, so under `make` a bare
# `read_text()` passes and the same call fails under a bare `pytest` or tool run.
# `make test` cannot see that class; `tests/test_explicit_encoding.py` gates it
# statically (SoT ST140).
export PYTHONUTF8 = 1

PYTHON_FILES = $(shell find . -name "*.py" -not -path "./venv/*" -not -path "./.venv/*")
DOCKER_IMAGE = buibui-bot
# ⚠ `status` resolves the memory tree, never hardcodes it. BOTH halves of the path vary
# by host -- the config root (`~/.claude-personal` on the Linux box, `~/.claude` on the
# Windows laptop) and the project slug, which the harness derives from the MAIN
# checkout's absolute path (a worktree shares its owner's tree). `tools/memory_dir.py`
# is the one resolver; it exits 1 with the paths it tried when no tree exists, and the
# recipe then prints NOT FOUND instead of sizing a file that is not there.

.PHONY: status skill-usage wait-ci wait-ci-main post-branch-checks post-branch-text sanity-checks preflight lint lint-md lint-md-fix docs-index docs-index-check lint-py-check lint-py typecheck test test-cov test-regression regression-update poetry-install poetry-update docker-build docker-monitor-price docker-monitor-price-live docker-monitor-position docker-monitor-position-live docker-analytics-backfill docker-analytics-sync docker-backtest docker-signal-watch buibui-monitor-price buibui-monitor-price-live buibui-monitor-price-telegram buibui-monitor-position buibui-monitor-position-live buibui-monitor-position-telegram buibui-analytics-backfill buibui-analytics-sync universe-backfill oi-archive-backfill buibui-backtest buibui-combo-backtest buibui-cross-tf-backtest buibui-signal-watch buibui-param-audit buibui-param-sweep buibui-recalibrate buibui-digest buibui-web buibui-card-place buibui-card-orders buibui-exits-watch buibui-exits-status web-install web-dev web-build web-preview web-full clean-db clean export-live-db buibui-portfolio-replay buibui-forecast-audit buibui-forecast-weight-study buibui-forecast-regime buibui-xsmom-audit buibui-combine-audit buibui-carry-audit buibui-xsmom-capacity-audit buibui-xsmom-targets buibui-xsmom-execute buibui-universe-sync buibui-xsmom-daily buibui-structural-touch-audit buibui-structural-entry-sim-audit buibui-warning-value-audit buibui-sl-horizon-audit buibui-weekly-path-audit buibui-indicator-condition-audit buibui-xsrev-audit buibui-decay-review buibui-dead-surface-check buibui-giveback-study buibui-occurrence-dump

# ⚠ The always-loaded gauge sums BOTH files. Until the 2026-08-19 AGENTS.md split
# it printed `CLAUDE.md` alone, which was the whole tier; afterwards that same
# number read ~4KB against ~40KB actually loaded, because `CLAUDE.md` is now a
# thin harness residue that imports `AGENTS.md`. Same failure class as the two
# below -- a reporting surface drifting from what it claims to measure -- except
# this one erred LOW, which reads as headroom nobody has.
#
# ⚠ The bullet count CALLS the gate's own `current_state_bullets`, and that is
# dedup rather than a correction. The ported original re-derived it as an awk
# range to EOF, which over-counted wherever a section follows Current State
# (10 against a true 6 here; correct in wifey only because Current State is its
# last section). Two implementations of one quantity, and the wrong one was the
# half a HUMAN reads when deciding whether to roll.
#
# Both defects found in this path so far — that awk range, and a `du -k` byte
# count wifey fixed to `wc -c` — were in the REPORTING surface, and both erred
# HIGH. The gate (`post_branch_checks._check_memory_cap`) has been correct both
# times. An over-measuring reporter in front of an under-demanding gate produces
# premature rolling the gate never asked for, and rolling early keeps you under
# cap, so it renders as good hygiene. Nothing is positioned to notice
# over-compliance — which is why the report must call the gate's code, not
# mirror it. (Diagnosis: wifey session, 2026-08-19.)
# #885: invocations per skill over WINDOW days (default 30; not DAYS, which line 5 sets to 90), zero-use named. The ledger
# is written by .claude/hooks/log-skill-usage.py; until it covers the window the
# zero-use list is PROVISIONAL, and the report says so before anything else.
skill-usage:
	@poetry run python tools/skill_usage.py --days $(or $(WINDOW),30)

status:
	@echo "📊 Repo shape ($$(date -u +%Y-%m-%d))"
	@printf '  tests collected   %s  (incl. regression that `make test` ignores)\n' "$$(poetry run pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oE '^[0-9]+' || echo '?')"
	@printf '  python files      %s\n' "$$(git ls-files '*.py' | wc -l)"
	@printf '  markdown files    %s\n' "$$(npx markdownlint-cli2 2>&1 | grep -oE 'Linting: [0-9]+' | grep -oE '[0-9]+' || echo '?')"
	@printf '  always-loaded     %s KB  (AGENTS.md %s + CLAUDE.md %s -- CLAUDE.md imports AGENTS.md, so BOTH load every session)\n' "$$(cat AGENTS.md CLAUDE.md | wc -c | awk '{printf "%.1f", $$1/1024}')" "$$(wc -c < AGENTS.md | awk '{printf "%.1f", $$1/1024}')" "$$(wc -c < CLAUDE.md | awk '{printf "%.1f", $$1/1024}')"
	@printf '  handoff           %s lines\n' "$$(wc -l < docs/plans/next-conversation-prompt.md 2>/dev/null || echo 0)"
	@if mem="$$(PYTHONPATH=. poetry run python tools/memory_dir.py)/MEMORY.md"; then \
		printf '  MEMORY.md         %s KB, %s Current State bullets\n' "$$(wc -c < "$$mem" | awk '{printf "%.1f", $$1/1024}')" "$$(PYTHONPATH=. poetry run python -c 'import pathlib,sys; from tools.post_branch_checks import current_state_bullets; print(current_state_bullets(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")))' "$$mem")"; \
	else printf '  MEMORY.md         NOT FOUND (resolver output above)\n'; fi
	@printf '  audits            %s\n' "$$(ls docs/audits/*.md | grep -vc INDEX)"
	@printf '  skills            %s\n' "$$(ls -d .claude/skills/*/ | wc -l)"
	@printf '  context docs      %s\n' "$$(ls .claude/context/*.md | wc -l)"
	@printf '  tools             %s\n' "$$(ls tools/*.py | wc -l)"

# The mechanical half of /post-branch. Advisory: --exit-zero so a finding is
# triaged by a human rather than blocking a commit.
post-branch-checks:
	@echo "🔍 Running the mechanical /post-branch sweep..."
	@PYTHONPATH=. poetry run python tools/post_branch_checks.py --exit-zero

# Screen a composed PR title/body for sensitive terms BEFORE `gh pr create`.
# FILE=- reads stdin, so a title pipes straight in. Unlike the sweep above this
# one GATES (exit 1 on a hit): a posted body is PUBLIC the moment it lands, and
# editing it later does not unpublish it. The sweep's three git legs ask the
# tracked tree and this branch's commits — a PR body is neither, so they report
# clean on one naming every term. Run it at /post-branch Step 7, beside the flip.
# ⚠ Through make the exit code is make's own 2, never the tool's 1 — read the
# banner, as with wait-ci and preflight. An unreadable FILE also exits 2, which
# is the point: it must not render as a clean single-check run.
post-branch-text:
	@PYTHONPATH=. poetry run python tools/post_branch_checks.py --text $(FILE)

# The mechanical half of /sanity-check. GATES, unlike post-branch-checks, and
# runs in CI's markdown job so a docs-only PR is covered.
sanity-checks:
	@echo "🔍 Running the mechanical /sanity-check sweep..."
	@PYTHONPATH=. poetry run python tools/sanity_checks.py

# The clean-clone pre-flight (ST45). Run it in /post-branch phase 4, AFTER the
# doc commits and BEFORE `gh pr create` — a clone sees COMMITTED state only, so
# running it earlier tests stale HEAD and reports green. It REPLACES that
# branch's `make test`: 300.1s against its 294.9s (+1.8%), and only this is hermetic.
# ⚠ make collapses the recipe's exit code, so read the printed banner: REFUSED
# (dirty tree) and INFRA (clone/install died) are NOT suite failures.
# ⚠ The interpreter is RESOLVED, not assumed. A bare `python3` on the Windows host is
# the Microsoft Store stub, which exits "Permission denied" without running anything --
# so this gate, the one /post-branch Step 7 depends on, could not start at all there.
# Same split as `deploy/backup-analytics.sh`; `python3` stays the last resort so the
# Linux box and CI are unaffected.
preflight:
	@echo "🧪 Running the clean-clone pre-flight..."
	@PY=./.venv/bin/python; [ -x "$$PY" ] || PY=./.venv/Scripts/python.exe; \
		[ -x "$$PY" ] || PY=python3; "$$PY" tools/clone_preflight.py

# ⚠ GNU make collapses any recipe failure to exit 2, so wait_ci.py's exit-code
# taxonomy (3 = Actions-allowance steps=0, 1 = real failure, 4 = unreadable
# counts) is INVISIBLE through make. Branch on the printed banner, or call the
# script directly when you need the code.
wait-ci:
	@PYTHONPATH=. poetry run python tools/wait_ci.py --pr $(PR)

wait-ci-main:
	@PYTHONPATH=. poetry run python tools/wait_ci.py --branch main --min-jobs 5

lint: lint-md lint-py

lint-md:
	@echo "🔍 Running markdownlint on all Markdown files..."
	npx markdownlint-cli2

docs-index:
	@echo "📚 Regenerating the audit + spec indexes..."
	poetry run python tools/docs_index.py

docs-index-check:
	@echo "📚 Checking the audit + spec indexes are current..."
	poetry run python tools/docs_index.py --check

lint-md-fix:
	@echo "🔍 Running markdownlint on all Markdown files..."
	npx markdownlint-cli2 --fix

lint-py-check:
	@echo "🧹 Checking Python formatting and linting with ruff..."
	poetry run ruff check .
	poetry run ruff format --check .

lint-py:
	@echo "🎨 Formatting and linting Python code with ruff..."
	poetry run ruff check --fix .
	poetry run ruff format .

typecheck:
	@echo "🔎 Type checking with mypy..."
	poetry run mypy . .claude/hooks

test:
	@echo "🧪 Running tests..."
	poetry run pytest tests/ -q --durations=10 --ignore=tests/test_regression.py

# Coverage on demand. It is not in `make test` because nothing gates on it —
# the tracer cost was being paid on every local run and every CI run to produce
# a report no one read. Run this when you actually want to read it.
test-cov:
	@echo "🧪 Running tests with coverage..."
	poetry run pytest tests/ --cov --cov-report=term-missing --ignore=tests/test_regression.py

test-regression:
	@echo "🔍 Running regression tests..."
	poetry run pytest tests/test_regression.py -v --timeout=300

regression-update:
	@echo "🔄 Regenerating golden files..."
	poetry run python scripts/extract_regression_fixture.py
	poetry run pytest tests/test_regression.py --update-golden -v --timeout=300
	@echo ""
	@echo "Review: git diff tests/fixtures/golden_*.json"
	@echo "Commit golden updates alongside your TOML/code changes."

poetry-install:
	@echo "📦 Installing dependencies with Poetry..."
	poetry install --no-root

poetry-update:
	@echo "🔄 Updating dependencies with Poetry..."
	poetry update

docker-build:
	@echo "🐳 Building Docker image..."
	docker build -t $(DOCKER_IMAGE) .

docker-monitor-price:
	@echo "🐳 Running price monitor in Docker..."
	docker run -t --env-file .env \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py monitor price

docker-monitor-price-live:
	@echo "🐳 Running price monitor (live) in Docker..."
	docker run -it --env-file .env \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py monitor price --live

docker-monitor-position:
	@echo "🐳 Running position monitor in Docker..."
	docker run -t --env-file .env \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py monitor position

docker-monitor-position-live:
	@echo "🐳 Running position monitor (live) in Docker..."
	docker run -it --env-file .env \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py monitor position --live --sort $(SORT)

docker-analytics-backfill:
	@echo "📥 Running analytics backfill in Docker..."
	@touch analytics.db
	docker run --rm --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py analytics backfill --since $(or $(SINCE),2023-01-01) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

docker-analytics-sync:
	@echo "🔄 Syncing analytics data in Docker..."
	@touch analytics.db
	docker run --rm --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py analytics sync \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

docker-backtest:
	@echo "📊 Running backtest in Docker..."
	@touch analytics.db
	docker run --rm --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py backtest \
		--symbol $(SYMBOL) \
		--strategy $(STRATEGY) \
		--interval $(INTERVAL) \
		--days $(DAYS) \
		$(if $(SL_PCT),--sl-pct $(SL_PCT),) \
		$(if $(TP_R),--tp-r $(TP_R),) \
		$(if $(SECONDARY),--secondary-symbol $(SECONDARY),) \
		$(if $(SAVE),--save,)

buibui-monitor-price:
	@echo "📈 Running price monitor..."
	poetry run python buibui.py monitor price

buibui-monitor-price-live:
	@echo "📈 Running price monitor in live mode..."
	poetry run python buibui.py monitor price --live

buibui-monitor-price-telegram:
	@echo "📈 Running price monitor and sending to Telegram..."
	poetry run python buibui.py monitor price --telegram

buibui-monitor-position:
	@echo "📊 Running position monitor..."
	poetry run python buibui.py monitor position --sort $(SORT)

buibui-monitor-position-live:
	@echo "📊 Running position monitor in live mode..."
	poetry run python buibui.py monitor position --live --sort $(SORT)

buibui-monitor-position-telegram:
	@echo "📊 Running position monitor and sending to Telegram..."
	poetry run python buibui.py monitor position --telegram

buibui-analytics-backfill:
	@echo "📥 Running analytics backfill..."
	@poetry run python buibui.py analytics backfill --since $(or $(SINCE),2023-01-01) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

buibui-analytics-sync:
	@echo "🔄 Syncing analytics data..."
	@poetry run python buibui.py analytics sync \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

universe-backfill:  ## Deep universe backfill — config/universe.toml, 1h/4h/1d/1w since 2019 (N3)
	@echo "🌌 Running universe deep-history backfill..."
	@poetry run python buibui.py analytics backfill --universe \
		--timeframes 1h 4h 1d 1w --since $(or $(SINCE),2019-01-01)

oi-archive-backfill:  ## Open-interest archive (data.binance.vision metrics) for the universe (#936); DB= SINCE= UNTIL= WORKERS= REPORT=1
	@echo "📈 Loading the open-interest archive for the universe..."
	@poetry run python buibui.py analytics oi-archive --universe \
		$(if $(DB),--db $(DB),) $(if $(SINCE),--since $(SINCE),) \
		$(if $(UNTIL),--until $(UNTIL),) $(if $(WORKERS),--workers $(WORKERS),) \
		$(if $(REPORT),--report-only,)

buibui-backtest:
	@echo "📊 Running backtest..."
	@poetry run python buibui.py backtest \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOL),--symbol $(SYMBOL),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(STRATEGY),--strategy $(STRATEGY),) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(INTERVAL),--interval $(INTERVAL),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(SL_PCT),--sl-pct $(SL_PCT),) \
		$(if $(TP_R),--tp-r $(TP_R),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(SECONDARY),--secondary-symbol $(SECONDARY),) \
		$(if $(COMBO),--combo,) \
		$(if $(WINDOW),--window $(WINDOW),) \
		$(if $(SAVE),--save,)

buibui-combo-backtest:
	@echo "📊 Running co-firing confluence backtest..."
	@poetry run python buibui.py backtest \
		--combo \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(WINDOW),--window $(WINDOW),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter,) \
		$(if $(SAVE),--save,) \
		$(if $(WORKERS),--workers $(WORKERS),)

buibui-cross-tf-backtest:
	@echo "📊 Running cross-TF co-firing backtest..."
	@poetry run python buibui.py backtest \
		--cross-tf \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(HTF_LTF),--htf-ltf $(HTF_LTF),) \
		$(if $(WINDOW_HOURS),--window-hours $(WINDOW_HOURS),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter,) \
		$(if $(SAVE),--save,) \
		$(if $(WORKERS),--workers $(WORKERS),)

buibui-param-audit:
	@echo "🔬 Running strategy audit..."
	@poetry run python buibui.py param-audit \
		$(if $(SYMBOL),--symbol $(SYMBOL),$(error SYMBOL is required)) \
		$(if $(TIMEFRAME),--timeframe $(TIMEFRAME),$(error TIMEFRAME is required)) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(WFO_SPLIT),--wfo-split $(WFO_SPLIT),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),)

buibui-param-sweep:
	@echo "🔬 Running WFO parameter sweep..."
	@poetry run python buibui.py param-sweep \
		$(if $(STRATEGY),--strategy $(STRATEGY),$(error STRATEGY is required)) \
		$(if $(SYMBOL),--symbol $(SYMBOL),$(error SYMBOL is required)) \
		$(if $(TIMEFRAME),--timeframe $(TIMEFRAME),$(error TIMEFRAME is required)) \
		$(if $(PARAM),--param $(PARAM),) \
		$(if $(WFO_SPLIT),--wfo-split $(WFO_SPLIT),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(TOP_N),--top-n $(TOP_N),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),)

buibui-recalibrate:
	@echo "⭐ Recalibrating confidence star ratings from backtest DB..."
	@poetry run python buibui.py recalibrate \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),) \
		$(if $(APPLY),--apply,)

buibui-portfolio-replay:
	@poetry run python buibui.py portfolio replay \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(CAPITAL),--capital $(CAPITAL),) \
		$(if $(VOL_TARGET),--vol-target $(VOL_TARGET),) \
		$(if $(DB),--db $(DB),)

.PHONY: buibui-brief
buibui-brief:  ## Daily market brief (read-only; SYMBOLS=/AS_OF= optional)
	@poetry run python buibui.py brief \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(AS_OF),--as-of $(AS_OF),)

.PHONY: buibui-card
buibui-card:  ## AI trade card (SYMBOL= required; DIRECTION=/HORIZON=/AS_OF=/DRY=1/TG=1/CONFIG= optional)
	@poetry run python buibui.py card $(SYMBOL) \
		$(if $(DIRECTION),--direction $(DIRECTION),) \
		$(if $(HORIZON),--horizon $(HORIZON),) \
		$(if $(AS_OF),--as-of $(AS_OF),) \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(TG),--telegram,) \
		$(if $(DRY),--dry-run,)

.PHONY: buibui-card-place
buibui-card-place:  ## interactive GTX picklist over unexpired TRADE cards (DRY=1 previews)
	@poetry run python buibui.py card-place $(if $(DRY),--dry-run,)

.PHONY: buibui-card-orders
buibui-card-orders:  ## list card order placements (REFRESH=1 polls terminal states)
	@poetry run python buibui.py card-orders $(if $(REFRESH),--refresh,)

.PHONY: buibui-exits-watch
buibui-exits-watch:  ## exit manager poll loop (LIVE=1 places real orders, ONCE=1 polls once)
	@poetry run python buibui.py exits watch $(if $(LIVE),--live,) $(if $(ONCE),--once,) $(if $(CONFIG),--config $(CONFIG),)

.PHONY: buibui-exits-status
buibui-exits-status:  ## armed exit-manager episodes, then the #981 success metric
	@poetry run python buibui.py exits status && poetry run python buibui.py exits report

.PHONY: buibui-forecast-audit
buibui-forecast-audit:  ## P2: read-only EWMAC trend-sleeve G2 audit over the N3 universe
	PYTHONPATH=. poetry run python tools/forecast_audit.py

.PHONY: buibui-forecast-weight-study
buibui-forecast-weight-study:  ## P2: read-only forecast-weight study (DSR/PBO-gated)
	PYTHONPATH=. poetry run python tools/forecast_audit.py --weight-study

.PHONY: buibui-forecast-regime
buibui-forecast-regime:  ## P2 §6: read-only per-regime attribution (read t_corr, not t_naive)
	PYTHONPATH=. poetry run python tools/forecast_audit.py --regime

.PHONY: buibui-xsmom-audit
buibui-xsmom-audit:  ## P3: read-only cross-sectional momentum sleeve audit over the N3 universe
	PYTHONPATH=. poetry run python tools/xsmom_audit.py

.PHONY: buibui-cvd-backfill
buibui-cvd-backfill:  ## D1: fetch Binance SPOT daily bars into spot_ohlcv (WRITES; run OFF the quarter-hour)
	PYTHONPATH=. poetry run python -m tools.cvd_audit backfill $(ARGS)

.PHONY: buibui-cvd-audit
buibui-cvd-audit:  ## D1: read-only spot-perp CVD sleeve audit — both book shapes + the three-leg gate
	PYTHONPATH=. poetry run python -m tools.cvd_audit run $(ARGS)

.PHONY: buibui-xsrev-audit
buibui-xsrev-audit:  ## P3: read-only cross-sectional reversal sleeve audit over the N3 universe
	PYTHONPATH=. poetry run python tools/xsrev_audit.py

.PHONY: buibui-xsmom-capacity-audit
buibui-xsmom-capacity-audit:  ## P3: read-only XS execution-realism capacity stress test
	PYTHONPATH=. poetry run python tools/xsmom_capacity_audit.py

.PHONY: buibui-xsmom-targets
buibui-xsmom-targets:  ## P3: read-only daily XS target positions (run buibui-analytics-sync first)
	PYTHONPATH=. poetry run python tools/xsmom_targets.py

.PHONY: buibui-xsmom-execute
buibui-xsmom-execute:  ## P3: XS-solo executor (dry-run; MODE=testnet to submit; SET_PEAK=+PEAK_REASON= corrects the drawdown mark)
	PYTHONPATH=. poetry run python tools/xsmom_execute.py $(if $(MODE),--mode $(MODE),) \
		$(if $(SET_PEAK),--set-peak $(SET_PEAK),) \
		$(if $(PEAK_REASON),--peak-reason "$(PEAK_REASON)",)

# ALL FOUR timeframes, not just the 1d the XS book reads (ST61a, 2026-08-23).
# The routine refresh is split between this target and the signal-watch timer,
# and until now the union had holes: signal-watch covers coins.json majors on
# 15m/1h/4h, this covered the 25-symbol universe on 1d, and NOTHING covered the
# universe on 1h/4h/1w. Measured before the fix: 22 of 25 symbols frozen 17.9d
# on 1h, 61.2d on 4h and 76.2d on 1w, every one of them TRADING. `run_sync`
# warns and continues on a (symbol, timeframe) with no rows, so widening this
# cannot fail a symbol that legitimately lacks a series.
.PHONY: buibui-universe-sync
buibui-universe-sync:  ## P3: incremental sync of the research universe, all timeframes (XS book input)
	PYTHONPATH=. poetry run python buibui.py analytics sync --universe \
		--timeframes $(or $(TIMEFRAMES),1h 4h 1d 1w)

.PHONY: buibui-xsmom-daily
buibui-xsmom-daily:  ## P3: daily XS workflow — sync the universe, then executor dry-run
	$(MAKE) buibui-universe-sync
	$(MAKE) buibui-xsmom-execute

# Both backup targets LOAD .env, mirroring the systemd units' `EnvironmentFile=`.
# Without it the hand-run and the scheduled run diverge, in two different ways:
# `backup-offsite` hard-fails on an unset BUIBUI_BACKUP_REMOTE, and `backup`
# SILENTLY applies default retention while the timer honours BUIBUI_KEEP_DAILY /
# BUIBUI_KEEP_WEEKLY from .env -- the quieter and worse of the two. Found by the
# wifey fork 2026-08-15 after porting this target; it went unnoticed here only
# because every hand-run so far passed the vars inline.
.PHONY: buibui-backup
buibui-backup:  ## Verified local snapshot of analytics.db + ledgers (WEEKLY=1 also exports parquet; DRY=1 reports only)
	@set -a; if [ -f .env ]; then . ./.env; fi; set +a; \
	./deploy/backup-analytics.sh $(if $(DRY),--dry-run,) $(if $(WEEKLY),--weekly,)

.PHONY: buibui-backup-offsite
buibui-backup-offsite:  ## Off-machine leg: rclone sync of BUIBUI_BACKUP_ROOT to BUIBUI_BACKUP_REMOTE (DRY=1 reports only)
	@set -a; if [ -f .env ]; then . ./.env; fi; set +a; \
	./deploy/backup-offsite.sh $(if $(DRY),--dry-run,)

.PHONY: buibui-combine-audit
buibui-combine-audit:  ## P3: read-only trend×XS IDM combine-layer audit over the N3 universe
	PYTHONPATH=. poetry run python tools/combine_audit.py

.PHONY: buibui-carry-audit
buibui-carry-audit:  ## P3: read-only funding-carry sleeve audit over the N3 universe
	PYTHONPATH=. poetry run python tools/carry_audit.py

.PHONY: buibui-structural-touch-audit
buibui-structural-touch-audit:  ## read-only structural level-hold touch-decay kill-test (H3)
	PYTHONPATH=. poetry run python tools/structural_touch_decay_audit.py

.PHONY: buibui-structural-entry-sim-audit
buibui-structural-entry-sim-audit:  ## read-only faithful per-strategy structural entry-sim harness (realized R)
	PYTHONPATH=. poetry run python tools/structural_entry_sim_audit.py

.PHONY: buibui-pundit-score
buibui-pundit-score:  ## score the pundit-call ledger vs OHLCV -> priors (sync universe 1h/1d first)
	PYTHONPATH=. poetry run python tools/pundit_score.py

.PHONY: buibui-decay-review
buibui-decay-review:  ## Weekly: read-only DSR-suspect list + gate reachability over rated cells
	PYTHONPATH=. poetry run python tools/decay_review.py \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),) \
		$(if $(ADR),--adr-suppress-threshold $(ADR),) \
		$(if $(DB),--db $(DB),)

.PHONY: buibui-dead-surface-check
buibui-dead-surface-check:  ## Read-only: declared-but-silent cells + rated-but-undeclared ratings
	PYTHONPATH=. poetry run python tools/dead_surface_check.py \
		$(if $(STRICT),--strict,) \
		$(if $(DB),--db $(DB),)

.PHONY: buibui-warning-value-audit
buibui-warning-value-audit:  ## H9: read-only W1-W8 warning-value audit (backtest primary, live corroboration)
	PYTHONPATH=. poetry run python tools/warning_value_audit.py

.PHONY: buibui-giveback-study
buibui-giveback-study:  ## ST17: read-only give-back / heat-and-run study over the live ledger
	PYTHONPATH=. poetry run python tools/giveback_study.py \
		$(if $(X),--x $(X),) \
		$(if $(BOOT),--boot $(BOOT),) \
		$(if $(DB),--db $(DB),)

.PHONY: buibui-sl-horizon-audit
buibui-sl-horizon-audit:  ## ST9/H11: read-only SL-horizon audit (flat 2% vs ATR-scaled stops)
	PYTHONPATH=. poetry run python tools/sl_horizon_audit.py

.PHONY: buibui-weekly-path-audit
buibui-weekly-path-audit:  ## H10: read-only partial-path predictiveness audit (gates ST6/ST7)
	PYTHONPATH=. poetry run python tools/weekly_path_audit.py

.PHONY: buibui-indicator-condition-audit
buibui-indicator-condition-audit:  ## H8: read-only M1 indicator-state conditioning audit (SOURCE=/MIN_N= optional)
	PYTHONPATH=. poetry run python tools/indicator_condition_audit.py $(if $(SOURCE),--source $(SOURCE),) $(if $(MIN_N),--min-n $(MIN_N),)

.PHONY: buibui-occurrence-dump
buibui-occurrence-dump:  ## ST63: read-only per-strategy occurrence dump, NO verdict (TF=/MIN_N=/OUT= optional)
	PYTHONPATH=. poetry run python tools/occurrence_dump.py $(if $(TF),--timeframes $(TF),) $(if $(MIN_N),--min-n $(MIN_N),) $(if $(OUT),--out $(OUT),)

.PHONY: buibui-premium-state-audit
buibui-premium-state-audit:  ## H14: read-only Coinbase-premium market-state audit
	PYTHONPATH=. poetry run python tools/premium_state_audit.py $(ARGS)

.PHONY: buibui-carry-unwind-audit
buibui-carry-unwind-audit:  ## H15: read-only USD/JPY carry-unwind state audit
	PYTHONPATH=. poetry run python tools/carry_unwind_audit.py $(ARGS)

## Routine DB update: run all-config backtests + recalibrate + regression update
db-update-backtest:
	@echo "📊 Running backtest for all 3 signal_watch configs (SINCE=2025-09-12)..."
	$(MAKE) buibui-backtest CONFIG=config/signal_watch.toml SINCE=2025-09-12 SAVE=1
	$(MAKE) buibui-backtest CONFIG=config/signal_watch_weekdays.toml SINCE=2025-09-12 SAVE=1
	$(MAKE) buibui-backtest CONFIG=config/signal_watch_all.toml SINCE=2025-09-12 SAVE=1

db-update-recalibrate:
	@echo "⭐ Recalibrating all 3 signal_watch configs..."
	$(MAKE) buibui-recalibrate CONFIG=config/signal_watch.toml APPLY=1
	$(MAKE) buibui-recalibrate CONFIG=config/signal_watch_weekdays.toml APPLY=1
	$(MAKE) buibui-recalibrate CONFIG=config/signal_watch_all.toml APPLY=1

db-update: db-update-backtest db-update-recalibrate regression-update
	@echo "✅ Routine DB update complete. Review: git diff tests/fixtures/golden_*.json"

export-live-db:  ## Export slim live_signal.duckdb (calibration + OHLCV) for GH Actions
	@PYTHONPATH=. poetry run python tools/export_live_db.py

buibui-digest:
	@echo "📊 Running backtest analysis digest..."
	@poetry run python buibui.py digest \
		$(if $(QUERY),--query $(QUERY),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(TOP_N),--top-n $(TOP_N),)

buibui-signal-watch:
	@echo "🔍 Running signal detection daemon..."
	@poetry run python buibui.py signal watch \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(TELEGRAM),--telegram,) \
		$(if $(SECONDARY),--secondary-symbol $(SECONDARY),) \
		$(if $(MIN_SL_PCT),--min-sl-pct $(MIN_SL_PCT),) \
		$(if $(CATCH_UP),--catch-up,)

docker-signal-watch:
	@echo "🔍 Running signal detection daemon in Docker..."
	@touch analytics.db signal_state.json
	docker run -it --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		-v $(PWD)/signal_state.json:/app/signal_state.json \
		$(DOCKER_IMAGE) poetry run python buibui.py signal watch \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(TELEGRAM),--telegram,) \
		$(if $(SECONDARY),--secondary-symbol $(SECONDARY),) \
		$(if $(MIN_SL_PCT),--min-sl-pct $(MIN_SL_PCT),)

buibui-signal-test:
	@echo "🧪 Firing test alert from historical data..."
	@poetry run python buibui.py signal test \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOL),--symbol $(SYMBOL),) \
		$(if $(TIMEFRAME),--timeframe $(TIMEFRAME),) \
		$(if $(STRATEGY),--strategy $(STRATEGY),) \
		$(if $(AT),--at $(AT),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(LOOKBACK),--lookback $(LOOKBACK),) \
		$(if $(DIRECTION),--direction $(DIRECTION),) \
		$(if $(TELEGRAM),--telegram,)

buibui-web:
	@echo "Starting web backend..."
	poetry run python buibui.py web --host $(WEB_HOST) --port $(PORT) \
		$(if $(CONFIG),--config $(CONFIG),)

web-install:
	cd web/ui && npm install

web-dev:
	cd web/ui && npm run dev -- --port $(DEV_PORT)

web-build:
	cd web/ui && npm run build

web-check:
	cd web/ui && npx svelte-check

web-preview:
	cd web/ui && npm run preview -- --port $(DEV_PORT)

web-full: web-build buibui-web

db-prune-backtests:
	@echo "🧹 Pruning old backtest runs (hard cutoff 30d; soft cutoff 7d+top-10 per strategy×symbol×tf×day_filter×adr_threshold)..."
	@poetry run python scripts/db_prune_backtests.py

clean-db:
	@echo "🗑️  Removing analytics DB and WAL files..."
	rm -f analytics.db analytics.db.wal

clean:
	@echo "🧹 Cleaning cache and build artifacts..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov/ dist/
