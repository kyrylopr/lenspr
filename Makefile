.PHONY: install dev test test-cov lint lint-fix format typecheck check clean build setup serve demo health

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

# Auto-annotate all nodes
annotate-all:
	@python3 -c "\
import lenspr; \
lenspr.init('.'); \
batch = lenspr.handle_tool('lens_annotate_batch', {'unannotated_only': True, 'limit': 1000}); \
count = 0; \
for n in batch['data']['nodes']: \
    s = lenspr.handle_tool('lens_annotate', {'node_id': n['id']}); \
    if s['success']: \
        d = s['data']; \
        lenspr.handle_tool('lens_save_annotation', {'node_id': n['id'], 'role': d['suggested_role'], 'side_effects': d['detected_side_effects'], 'semantic_inputs': d['detected_inputs'], 'semantic_outputs': d['detected_outputs']}); \
        count += 1; \
print(f'Annotated {count} nodes')"
