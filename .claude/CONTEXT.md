# LensPR Development Context

**Last updated:** 2026-02-03
**Status:** CI green on Python 3.11, 3.12, 3.13

---

## Current Project Stats

```
Nodes: 562 (162 functions, 225 methods, 82 classes, 37 modules)
Edges: 2186 (1468 calls, 314 imports, 286 uses)
Confidence: 76.5% resolved
Docstrings: 53.9% coverage
Annotations: 0% (not started)
Unresolved edges: 5 (all dynamic: getattr, globals)
Circular imports: 0
```

---

## What Was Done (2026-02-03)

### 1. Fixed CI failures
- **Problem:** Tests failed on Python 3.11/3.13, passed on 3.12
- **Root cause:** Enum comparison in `validator.py` behaved differently across Python versions
- **Fix:** Changed from `NodeType` enum keys to string keys (`"function"`, `"method"`, `"class"`)
- **Files changed:** `lenspr/validator.py`

### 2. Aggressive impact enforcement
- **Problem:** Claude bypasses lenspr tools after using them once
- **Solution:**
  - `lens_update_node` now computes impact BEFORE applying changes
  - `lens_update_node` includes impact data in ALL responses (success and failure)
  - `lens_context` now includes `modification_warning` and `test_warning`
  - `CLAUDE.md` updated with BLOCKING REQUIREMENTS
- **Files changed:**
  - `lenspr/tools/modification.py`
  - `lenspr/tools/navigation.py`
  - `.claude/CLAUDE.md`

---

## Project Architecture

```
Source Files (always source of truth)
     │
     ▼
Parser (ast + jedi) ──→ SQLite (graph.db, history.db, resolve_cache.db)
                              │
                              ▼
                        NetworkX (lazy cache for graph algorithms)
                              │
                              ▼
                        Tools (CLI / MCP Server / Python API / Claude API)
                              │
                              ▼
                        Patcher (line-based replace, bottom-to-top)
                              │
                              ▼
                        Validator (syntax → structure → signature)
```

### Key Files

| File | Purpose |
|------|---------|
| `lenspr/__init__.py` | Public API |
| `lenspr/models.py` | Data classes (Node, Edge, NodeType, etc.) |
| `lenspr/database.py` | SQLite operations |
| `lenspr/graph.py` | NetworkX algorithms (impact, dead_code, cycles) |
| `lenspr/validator.py` | 3-level validation |
| `lenspr/patcher.py` | File patching |
| `lenspr/tools/` | Tool handlers (27 tools) |
| `lenspr/mcp_server.py` | MCP server with watchdog |
| `lenspr/parsers/python_parser.py` | AST + jedi parser |

### Tool Categories (27 total)

- **Navigation:** `list_nodes`, `get_node`, `search`, `grep`, `context`, `get_structure`, `find_usages`
- **Analysis:** `check_impact`, `validate_change`, `health`, `diff`, `dead_code`, `dependencies`
- **Modification:** `update_node`, `add_node`, `delete_node`, `rename`, `batch`
- **Annotations:** `annotate`, `save_annotation`, `annotate_batch`, `annotation_stats`
- **Git:** `blame`, `node_history`, `commit_scope`, `recent_changes`

---

## Known Limitations

| Limitation | Impact | Workaround |
|------------|--------|------------|
| Only Python | No JS/TS/Go | BaseParser interface ready |
| Dynamic code (getattr, eval) | 5 unresolved edges | Marked as "unresolved" |
| No runtime coverage | Don't know real test coverage | Use naming convention |
| Text-based rename | May miss string references | `needs_review` in response |
| Rule-based annotations | Not LLM-powered | Manual annotation |
| Large projects slow | Initial parse can take time | Pagination, incremental sync |

---

## Proposed Improvements (Priority Order)

### HIGH Priority

#### 1. `lens_explain` — LLM-powered function explanation
```python
# Current: rule-based _detect_role
# Proposed: LLM generates explanation
lens_explain("app.utils.validate_email")
→ "Validates email format using regex. Returns True if valid, False otherwise."
```

**Implementation:**
- New file: `lenspr/tools/explain.py`
- Requires: anthropic SDK or configurable LLM backend
- Add to: `mcp_server.py`, `tools/__init__.py`, `tools/schemas.py`

#### 2. `lens_test_coverage` — Real test coverage
```python
lens_test_coverage("app.models.User")
→ {"covered_by": ["test_user_create"], "line_coverage": 85}
```

**Implementation:**
- Integrate with pytest-cov or coverage.py
- Store coverage data in `coverage_cache.db`
- New tool handler in `tools/analysis.py`

#### 3. JS/TS Parser
**Implementation:**
- New file: `lenspr/parsers/js_parser.py`
- Use tree-sitter or typescript compiler API
- Register in parser factory

### MEDIUM Priority

#### 4. `lens_complexity` — Code metrics
```python
lens_complexity("app.services.payment")
→ {"cyclomatic": 12, "lines": 45, "cognitive": 8}
```

#### 5. `lens_similar` — Find similar functions
```python
lens_similar("app.utils.format_date")
→ [{"id": "app.helpers.date_str", "similarity": 0.85}]
```

#### 6. Pre-modify hook enforcement
- Create Claude Code hook that blocks Edit on .py without lens_check_impact
- Technical enforcement instead of prompt-based

### LOW Priority

#### 7. Makefile additions
```makefile
watch:
    lenspr serve . --dev

test-quick:
    pytest tests/ -q --tb=short
```

#### 8. README improvements
- Add "Limitations" section explicitly
- More examples for lens_context, lens_grep
- Troubleshooting for slow initial parse

---

## Development Commands

```bash
make dev          # Install with dev dependencies
make test         # Run tests
make check        # Lint + typecheck + test
make health       # Show graph stats
make annotations  # Show annotation coverage
make annotate-all # Auto-annotate all nodes
make serve        # Start MCP server
make demo         # Parse lenspr itself
```

---

## Testing

```bash
# Full test suite
pytest tests/ -v

# Specific test file
pytest tests/test_tool_operations.py -v

# Single test
pytest tests/test_tool_operations.py::TestUpdateNode::test_update_rejects_structure_change -v

# With coverage
pytest tests/ --cov=lenspr --cov-report=term-missing
```

---

## CI/CD

- **GitHub Actions:** `.github/workflows/ci.yml`
- **Matrix:** Python 3.11, 3.12, 3.13
- **Steps:** Install → Lint (ruff) → Type check (mypy) → Test (pytest)
- **Publish:** On push to main with `release:` commit message prefix

---

## How to Use This Context

1. **Before modifying Python code:**
   ```
   lens_check_impact("node_id")
   ```

2. **Before reading Python code:**
   ```
   lens_context("node_id")  # Better than Read tool
   ```

3. **After making changes:**
   ```bash
   make test  # Verify tests pass
   make check # Full lint + typecheck + test
   ```

4. **To understand a function:**
   ```
   lens_context("module.function")  # Source + callers + callees + tests
   ```
