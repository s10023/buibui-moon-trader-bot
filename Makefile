SORT ?= default
# Makefile — Lint Markdown and Python

MARKDOWN_FILES = $(shell find . -name "*.md" -not -path "./venv/*")
PYTHON_FILES = $(shell find . -name "*.py" -not -path "./venv/*")
DOCKER_IMAGE = buibui-bot

.PHONY: lint lint-md lint-py format format-py docker-build docker-run-price docker-run-position

lint: lint-md lint-py

lint-md:
	@echo "🔍 Running markdownlint on all Markdown files..."
	markdownlint $(MARKDOWN_FILES)

lint-py-check:
	@echo "🧹 Checking Python formatting with black..."
	poetry run black --check $(PYTHON_FILES)

lint-py:
	@echo "🎨 Formatting Python code with black..."
	poetry run black $(PYTHON_FILES)

typecheck:
	@echo "🔎 Type checking with mypy..."
	poetry run mypy .

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
