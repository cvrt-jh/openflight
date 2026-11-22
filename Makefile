.PHONY: test lint format dev build-ui start docs docs-build

## Run Python tests
test:
	uv run pytest tests/ -v

## Run all linters (Python + UI)
lint:
	uv run ruff check src/openflight/
	uv run pylint src/openflight/ --fail-under=9
	cd ui && pnpm run lint

## Auto-format Python code
format:
	uv run ruff format src/openflight/
	uv run ruff check --fix src/openflight/

## Start server in mock mode (no hardware needed)
dev:
	scripts/start-kiosk.sh --mock

## Build the React UI
build-ui:
	cd ui && pnpm install && pnpm run build

## Start the full application (requires hardware)
start:
	scripts/start-kiosk.sh

## Install all dependencies (Python + UI)
install:
	uv sync --group dev
	cd ui && pnpm install

## Install pre-commit hooks
hooks:
	uv run pre-commit install

## Serve the docs site with live reload at http://localhost:8000
docs:
	uv run --group docs zensical serve

## Build the docs site; fails on broken internal links
docs-build:
	uv run --group docs zensical build --clean --strict
