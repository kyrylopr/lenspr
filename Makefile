.PHONY: install dev test lint format typecheck clean build publish

# Install package in production mode
install:
	pip install -e .

# Install with dev dependencies
dev:
	pip install -e ".[dev]"

# Run tests
test:
	pytest tests/ -v

# Run tests with coverage
test-cov:
	pytest tests/ -v --cov=lenspr --cov-report=term-missing

# Lint with ruff
lint:
	ruff check lenspr/ tests/

# Format with black
format:
	black lenspr/ tests/

# Type checking
typecheck:
	mypy lenspr/

# Run all checks (lint + typecheck + test)
check: lint typecheck test

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Build package
build: clean
	python -m build

# Show project structure
tree:
	@find lenspr -name '*.py' | sort | head -50

# Quick demo: init lenspr on itself
demo:
	python -c "import lenspr; ctx = lenspr.init('.', force=True); print(f'Parsed {len(lenspr.list_nodes())} nodes')"
