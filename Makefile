.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test cov check build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install package with dev dependencies
	uv venv
	uv pip install -e ".[dev]"

lint: ## Run ruff linter
	uv run ruff check .

format: ## Auto-format and fix with ruff
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Run mypy (strict)
	uv run mypy

test: ## Run the test suite
	uv run pytest -q

cov: ## Run tests with coverage report
	uv run pytest --cov=sailor --cov-report=term-missing

check: lint typecheck test ## Run lint + typecheck + tests

build: ## Build wheel and sdist
	uv build

clean: ## Remove build artifacts and caches
	rm -rf dist build *.egg-info src/*.egg-info
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
