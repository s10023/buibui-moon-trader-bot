SORT ?= default
SYMBOL ?= BTCUSDT
STRATEGY ?= fvg
INTERVAL ?= 4h
DAYS ?= 90
SAVE ?=
PORT ?= 8000
DEV_PORT ?= 5173
# Makefile — Lint Markdown and Python

PYTHON_FILES = $(shell find . -name "*.py" -not -path "./venv/*" -not -path "./.venv/*")
DOCKER_IMAGE = buibui-bot

.PHONY: lint lint-md lint-md-fix docs-index docs-index-check lint-py-check lint-py typecheck test test-cov test-regression regression-update poetry-install poetry-update docker-build docker-monitor-price docker-monitor-price-live docker-monitor-position docker-monitor-position-live docker-analytics-backfill docker-analytics-sync docker-backtest docker-signal-watch buibui-monitor-price buibui-monitor-price-live buibui-monitor-price-telegram buibui-monitor-position buibui-monitor-position-live buibui-monitor-position-telegram buibui-analytics-backfill buibui-analytics-sync universe-backfill buibui-backtest buibui-combo-backtest buibui-cross-tf-backtest buibui-signal-watch buibui-param-audit buibui-param-sweep buibui-recalibrate buibui-digest buibui-web web-install web-dev web-build web-preview web-full clean-db clean export-live-db buibui-portfolio-replay buibui-forecast-audit buibui-forecast-weight-study buibui-forecast-regime buibui-xsmom-audit buibui-combine-audit buibui-carry-audit buibui-xsmom-capacity-audit buibui-xsmom-targets buibui-xsmom-execute buibui-universe-sync buibui-xsmom-daily buibui-structural-touch-audit buibui-structural-entry-sim-audit buibui-warning-value-audit buibui-sl-horizon-audit buibui-weekly-path-audit buibui-indicator-condition-audit buibui-xsrev-audit buibui-decay-review buibui-dead-surface-check buibui-giveback-study

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
	poetry run mypy .

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
		$(if $(VOL_TARGET),--vol-target $(VOL_TARGET),)

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
buibui-xsmom-execute:  ## P3: XS-solo order-routing executor (dry-run by default; MODE=testnet to submit)
	PYTHONPATH=. poetry run python tools/xsmom_execute.py $(if $(MODE),--mode $(MODE),)

.PHONY: buibui-universe-sync
buibui-universe-sync:  ## P3: incremental 1d sync of the full research universe (XS book input)
	PYTHONPATH=. poetry run python buibui.py analytics sync --universe --timeframes 1d

.PHONY: buibui-xsmom-daily
buibui-xsmom-daily:  ## P3: daily XS workflow — sync universe 1d, then executor dry-run
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
	poetry run python buibui.py web --host 0.0.0.0 --port $(PORT) \
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
