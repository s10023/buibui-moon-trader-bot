SORT ?= default
SYMBOL ?= BTCUSDT
STRATEGY ?= fvg
INTERVAL ?= 4h
DAYS ?= 90
# Makefile — Lint Markdown and Python

PYTHON_FILES = $(shell find . -name "*.py" -not -path "./venv/*" -not -path "./.venv/*")
DOCKER_IMAGE = buibui-bot

.PHONY: lint lint-md lint-md-fix lint-py-check lint-py typecheck test poetry-install poetry-update docker-build docker-monitor-price docker-monitor-position docker-analytics-backfill docker-analytics-sync docker-backtest buibui-monitor-price buibui-monitor-price-live buibui-monitor-price-telegram buibui-monitor-position buibui-monitor-position-telegram buibui-open-trades buibui-analytics-backfill buibui-analytics-sync buibui-backtest clean-db clean

lint: lint-md lint-py

lint-md:
	@echo "🔍 Running markdownlint on all Markdown files..."
	npx markdownlint-cli2

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
	poetry run pytest tests/ -v --cov --cov-report=term-missing

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
	docker run --env-file .env $(DOCKER_IMAGE) poetry run python buibui.py monitor price

docker-monitor-position:
	@echo "🐳 Running position monitor in Docker..."
	docker run --env-file .env \
		-v $(PWD)/config/coins.json:/app/config/coins.json:ro \
		$(DOCKER_IMAGE) poetry run python buibui.py monitor position

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
		$(if $(SECONDARY),--secondary-symbol $(SECONDARY),)

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

buibui-monitor-position-telegram:
	@echo "📊 Running position monitor and sending to Telegram..."
	poetry run python buibui.py monitor position --telegram

buibui-analytics-backfill:
	@echo "📥 Running analytics backfill..."
	poetry run python buibui.py analytics backfill --since $(or $(SINCE),2023-01-01) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

buibui-analytics-sync:
	@echo "🔄 Syncing analytics data..."
	poetry run python buibui.py analytics sync \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

buibui-backtest:
	@echo "📊 Running backtest: $(SYMBOL) $(STRATEGY) $(INTERVAL) $(DAYS)d..."
	poetry run python buibui.py backtest \
		--symbol $(SYMBOL) \
		--strategy $(STRATEGY) \
		--interval $(INTERVAL) \
		--days $(DAYS) \
		$(if $(SL_PCT),--sl-pct $(SL_PCT),) \
		$(if $(TP_R),--tp-r $(TP_R),) \
		$(if $(SECONDARY),--secondary-symbol $(SECONDARY),)

buibui-open-trades:
	@echo "🚀 Opening multiple trades..."
	poetry run python trade/open_trades.py

clean-db:
	@echo "🗑️  Removing analytics DB and WAL files..."
	rm -f analytics.db analytics.db.wal

clean:
	@echo "🧹 Cleaning cache and build artifacts..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov/ dist/
