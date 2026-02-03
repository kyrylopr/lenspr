.PHONY: install dev test test-cov lint lint-fix format typecheck check clean build setup serve demo health annotate annotate-all annotate-node annotate-file benchmark

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

# Publish to PyPI
publish: build
	twine upload dist/*

# Publish to TestPyPI first
publish-test: build
	twine upload --repository testpypi dist/*

# Configure MCP for Claude Code (creates .mcp.json)
setup:
	lenspr setup .

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

# Show graph health report
health:
	@python3 -c "import lenspr; lenspr.init('.'); r=lenspr.handle_tool('lens_health',{}); d=r['data']; print(f'Nodes: {d[\"total_nodes\"]} | Edges: {d[\"total_edges\"]} | Confidence: {d[\"confidence_pct\"]}% | Docstrings: {d[\"docstring_pct\"]}%')"

# Show annotation coverage
annotations:
	@python3 -c "import lenspr; lenspr.init('.'); r=lenspr.handle_tool('lens_annotation_stats',{}); d=r['data']; print(f'Annotated: {d[\"annotated\"]}/{d[\"total_annotatable\"]} ({d[\"coverage_pct\"]}%) | Stale: {d[\"stale_annotations\"]}')"

# Show annotation coverage and instructions
annotate:
	@lenspr annotate .

# Auto-annotate all unannotated nodes (role/side_effects only, no summary)
annotate-all:
	@lenspr annotate . --auto

# Auto-annotate all nodes including already annotated (rewrite)
annotate-all-force:
	@lenspr annotate . --auto --force

# Annotate specific node: make annotate-node NODE=app.models.User
annotate-node:
	@lenspr annotate . --node $(NODE)

# Annotate multiple nodes: make annotate-nodes NODES="app.foo app.bar"
annotate-nodes:
	@lenspr annotate . --nodes $(NODES)

# Annotate all nodes in a file: make annotate-file FILE=lenspr/cli.py
annotate-file:
	@lenspr annotate . --file $(FILE)

# Run benchmark (requires ANTHROPIC_API_KEY in eval/.env)
benchmark:
	@echo "Running LensPR benchmark..."
	@echo "Note: Set ANTHROPIC_API_KEY in eval/.env first"
	@cd eval && jupyter nbconvert --to notebook --execute benchmark.ipynb --output benchmark_results.ipynb 2>/dev/null || \
		echo "Run manually: cd eval && jupyter notebook benchmark.ipynb"

# Generate benchmark charts from existing results
benchmark-charts:
	@cd eval && python3 generate_charts.py

# Quick benchmark summary
benchmark-summary:
	@echo "==============================================="
	@echo "LensPR Benchmark Summary"
	@echo "==============================================="
	@echo ""
	@echo "WITHOUT LensPR:"
	@echo "  - Tokens: 1.27M"
	@echo "  - Iterations: 84"
	@echo "  - Success: 1/3 (33%)"
	@echo ""
	@echo "WITH LensPR:"
	@echo "  - Tokens: 388K"
	@echo "  - Iterations: 38"
	@echo "  - Success: 3/3 (100%)"
	@echo ""
	@echo "IMPROVEMENT:"
	@echo "  - Token savings: 70%"
	@echo "  - Iteration savings: 55%"
	@echo "  - Success rate: +200%"
	@echo "==============================================="
