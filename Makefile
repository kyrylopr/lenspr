.PHONY: install install-all dev test test-cov lint lint-fix format typecheck check clean build setup serve demo health doctor annotate annotate-all annotate-node annotate-file benchmark check-deps architecture metrics components largest class-metrics dead-code

# ============================================================================
# INSTALLATION
# ============================================================================

# Install package with Python support only
install:
	pip install -e .

# Install with ALL features (Python + TypeScript + MCP + Watch) - RECOMMENDED
install-all:
	pip install -e ".[all]"
	@echo ""
	@echo "✓ LensPR installed with all features"
	@echo ""
	@$(MAKE) check-deps --no-print-directory

# Install for development (all features + dev tools)
dev:
	pip install -e ".[all-dev]"
	@echo ""
	@echo "✓ Development environment ready"
	@echo ""
	@$(MAKE) check-deps --no-print-directory

# Check all dependencies and features
check-deps:
	@echo "Feature Status:"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@python3 -c "import lenspr; print('  Python parser:     ✓')" 2>/dev/null || echo "  Python parser:     ✗"
	@python3 -c "from lenspr.parsers import TYPESCRIPT_AVAILABLE; print('  TypeScript parser: ✓' if TYPESCRIPT_AVAILABLE else '  TypeScript parser: ✗ (pip install lenspr[typescript])')"
	@python3 -c "import mcp; print('  MCP server:        ✓')" 2>/dev/null || echo "  MCP server:        ✗ (pip install lenspr[mcp])"
	@python3 -c "import watchdog; print('  File watcher:      ✓')" 2>/dev/null || echo "  File watcher:      ✗ (pip install lenspr[watch])"
	@echo ""
	@echo "Node.js (for TypeScript type inference):"
	@node --version 2>/dev/null && echo "  ✓ Node.js installed" || echo "  ✗ Node.js not found (install Node.js 18+ for full TypeScript support)"
	@echo ""
	@echo "JS/TS Projects:"
	@echo "  For 80%+ resolution, ensure jsconfig.json or tsconfig.json exists"
	@echo "  Create minimal: echo '{\"compilerOptions\":{\"baseUrl\":\".\"},\"include\":[\"src/**/*\"]}' > jsconfig.json"
	@echo ""

# Install TypeScript dependencies for Node.js resolver
install-ts-deps:
	@echo "Installing TypeScript dependencies..."
	cd lenspr/helpers && npm install
	@echo "✓ TypeScript dependencies installed"

# ============================================================================
# TESTING
# ============================================================================

# Run tests
test:
	pytest tests/ -v

# Run tests with coverage
test-cov:
	pytest tests/ -v --cov=lenspr --cov-report=term-missing

# Run TypeScript parser tests specifically
test-ts:
	pytest tests/test_typescript_parser.py tests/test_ts_resolver.py -v

# ============================================================================
# LINTING & FORMATTING
# ============================================================================

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

# ============================================================================
# BUILD & PUBLISH
# ============================================================================

# Clean build artifacts and caches
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Clean LensPR graph data
clean-lens:
	rm -rf .lens/

# Clean TypeScript resolver cache
clean-ts-cache:
	rm -f .lens/resolve_cache.db

# Build package
build: clean
	python -m build

# Publish to PyPI
publish: build
	twine upload dist/*

# Publish to TestPyPI first
publish-test: build
	twine upload --repository testpypi dist/*

# ============================================================================
# LENSPR COMMANDS
# ============================================================================

# Configure MCP for Claude Code (creates .mcp.json)
setup:
	lenspr setup .

# Start MCP server on current directory
serve:
	lenspr serve .

# Initialize graph (auto-detects Python + TypeScript/JS files)
init:
	lenspr init .

# Force re-initialize
init-force:
	lenspr init --force .

# Sync graph with file changes
sync:
	lenspr sync .

# Watch for file changes and auto-sync
watch:
	lenspr watch .

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

# Run project diagnostics
doctor:
	lenspr doctor .

# Show supported languages
languages:
	@python3 -c "from lenspr.parsers import MultiParser; p=MultiParser(); print('Supported languages:', ', '.join(p.supported_languages)); print('Extensions:', ', '.join(p.get_file_extensions()))"

# ============================================================================
# ANNOTATIONS
# ============================================================================

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

# ============================================================================
# ARCHITECTURE METRICS
# ============================================================================

# Show project metrics + largest classes
architecture:
	@lenspr architecture .

# Show project-wide metrics only
metrics:
	@lenspr architecture . --metrics

# Show component cohesion metrics
components:
	@lenspr architecture . --components

# Show largest classes: make largest N=20
largest:
	@lenspr architecture . --largest $(or $(N),10)

# Show class metrics: make class-metrics NODE=app.MyClass
class-metrics:
	@lenspr architecture . --explain $(NODE)

# Find potentially dead code
dead-code:
	@python3 -c "import lenspr; lenspr.init('.'); r=lenspr.handle_tool('lens_dead_code',{}); d=r['data']; print(f'Dead code found: {d[\"count\"]} nodes'); [print(f'  {f}: {len(nodes)} items') for f, nodes in sorted(d['by_file'].items())[:10]]"

# ============================================================================
# BENCHMARKS
# ============================================================================

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

# ============================================================================
# HELP
# ============================================================================

help:
	@echo "LensPR Makefile Commands"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "Installation:"
	@echo "  make install      Install with Python support only"
	@echo "  make install-all  Install with ALL features (recommended)"
	@echo "  make dev          Install for development"
	@echo "  make check-deps   Check installed features"
	@echo ""
	@echo "Graph Operations:"
	@echo "  make init         Initialize graph (auto-detects languages)"
	@echo "  make sync         Sync graph with file changes"
	@echo "  make watch        Auto-sync on file changes"
	@echo "  make setup        Configure MCP for Claude Code"
	@echo "  make serve        Start MCP server"
	@echo ""
	@echo "Development:"
	@echo "  make test         Run tests"
	@echo "  make test-ts      Run TypeScript parser tests"
	@echo "  make lint         Run linter"
	@echo "  make check        Run all checks"
	@echo ""
	@echo "Diagnostics:"
	@echo "  make health       Show graph health"
	@echo "  make doctor       Run project diagnostics"
	@echo "  make languages    Show supported languages"
	@echo "  make annotations  Show annotation coverage"
	@echo ""
	@echo "Architecture & Analysis:"
	@echo "  make architecture   Show project metrics + largest classes"
	@echo "  make metrics        Show project-wide statistics"
	@echo "  make components     Show component cohesion"
	@echo "  make largest        Show largest classes (N=...)"
	@echo "  make class-metrics  Show class metrics (NODE=...)"
	@echo "  make dead-code      Find potentially dead code"
