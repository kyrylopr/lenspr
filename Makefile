.PHONY: install dev test test-cov lint lint-fix format typecheck check clean build serve demo

# Install package in production mode
install:
	pip install -e .

# Install with dev + mcp dependencies
dev:
	pip install -e ".[dev,mcp]"

# Run tests
test:
	pytest tests/ -v

# Run tests with coverage
test-cov:
	pytest tests/ -v --cov=lenspr --cov-report=term-missing

# Lint with ruff
lint:
	ruff check lenspr/ tests/

# Lint and auto-fix
lint-fix:
	ruff check --fix lenspr/ tests/

# Format with ruff
format:
	ruff format lenspr/ tests/

# Type checking
typecheck:
	mypy lenspr/

# Run all checks (lint + typecheck + test)
check: lint typecheck test

# Clean build artifacts and caches
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Clean LensPR graph data
clean-lens:
	rm -rf .lens/

# Build package
build: clean
	python -m build

# Start MCP server on current directory
serve:
	lenspr serve .

# Demo: parse lenspr itself
demo:
	lenspr init --force .
	lenspr status .
	lenspr search . "validate"

# Show project structure
tree:
	@find lenspr -name '*.py' | sort
