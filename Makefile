SORT ?= default
# Makefile — Lint Markdown and Python

PYTHON_FILES = $(shell find . -name "*.py" -not -path "./venv/*" -not -path "./.venv/*")
DOCKER_IMAGE = buibui-bot

.PHONY: lint lint-md lint-py format format-py test docker-build docker-run-price docker-run-position

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
	docker run --env-file .env $(DOCKER_IMAGE) poetry run python buibui.py monitor position

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

buibui-open-trades:
	@echo "🚀 Opening multiple trades..."
	poetry run python trade/open_trades.py
