# LensPR Architecture

## Data Flow

```
Source files (.py, .ts, .tsx, .js, .jsx)
+ Infrastructure (.sql, Dockerfile, docker-compose.yml, .github/workflows/*.yml, .env)
     │
     ▼
Multi-pass pipeline:
  1. AST parsing         Python ast module + tree-sitter (JS/TS)
  2. Name resolution     Jedi / Pyright (Python), TS Compiler API (TypeScript)
  3. Edge normalization  Cross-file ID matching, deduplication
  4. API mapping         Frontend HTTP calls → backend route handlers
  5. SQL mapping         Function → database table (SQLAlchemy/Django/raw SQL)
  6. Infra mapping       Dockerfiles, compose services, env vars
  7. FFI mapping         NAPI, koffi, ffi-napi, WASM bridges
  8. CI/CD mapping       GitHub Actions workflows, jobs, secrets
     │
     ▼
SQLite graph database (.lens/graph.db)
     │
     ▼
NetworkX (lazy cache for graph algorithms)
     │
     ▼
60+ MCP tools (via mcp_server.py, stdio transport)
     │
     ▼
File watcher (watchdog) auto-syncs on every save
```

Everything runs locally. Code never leaves the machine.

---

## Key Design Decisions

### Patcher, not generator
Files are patched in place, never regenerated. Only the changed lines are touched. Bottom-to-top patching avoids line number corruption when applying multiple patches to one file.

### SQLite is the single source of truth
NetworkX graph is a read-only cache rebuilt on demand. This ensures consistency and allows efficient queries.

### 3-level validation
Every code change goes through:
1. **Syntax** — valid Python/TS AST
2. **Structure** — function stays function, class stays class
3. **Signature** — parameter compatibility with callers

### Pluggable parsers
`BaseParser` interface supports multiple languages. Currently implemented: Python (AST + Jedi/Pyright) and TypeScript/JavaScript (tree-sitter + TS Compiler API). Go/Rust/Java ready for contributors.

### Confidence scoring
Edges are marked as:
- `resolved` — Jedi/Pyright/TS confirmed the target exists in project
- `inferred` — AST-based, likely correct but unconfirmed
- `external` — target is stdlib or pip/npm package
- `unresolved` — dynamic dispatch (`getattr`, `eval`), can't determine statically

### Cross-language edges
Frontend and backend are connected into a single graph via specialized mappers (API routes, database tables, env vars, Docker services, FFI bridges, CI/CD workflows).

### Auto-sync file watcher
The MCP server watches for file changes via watchdog and re-parses modified files before every tool call. The graph is always up to date.

### Change tracking
Every modification made through `lens_update_node` / `lens_patch_node` is logged with reasoning, old/new source, and impact summary. Used by `lens_resume` to restore session context.

---

## Project Structure

```
lenspr/
├── __init__.py              Public API (init, sync, handle_tool, get_system_prompt)
├── models.py                Data classes (Node, Edge, Change, Patch, 23 classes total)
├── context.py               LensContext — central state manager (graph, db, config)
├── database.py              SQLite operations (save/load graph, annotations, sessions)
├── graph.py                 NetworkX algorithms (impact zone, dead code, cycles, structure)
├── patcher.py               File patching (PatchBuffer, bottom-to-top apply)
├── architecture.py          Component detection, class metrics computation
├── mcp_server.py            MCP server — 60+ tool handlers, file watcher, auto-sync
├── cli.py                   CLI entry point (init, setup, serve, doctor, annotate, tools)
├── claude_tools.py          Tool definitions for Claude API integration
├── tool_groups.py           12 tool groups — enable/disable to save context window
├── stats.py                 Parse statistics (language breakdown, timing)
├── monorepo.py              Monorepo detection and npm dependency installation
├── doctor.py                Diagnostic checks (Python, Node, tree-sitter, graph health)
├── tracer.py                Runtime call tracer (sys.monitoring, Python 3.12+)
├── pytest_tracer.py         pytest plugin — auto-traces during test execution
│
├── parsers/
│   ├── base.py              BaseParser interface (parse_file, extract_edges)
│   ├── multi.py             MultiParser — orchestrates Python + TypeScript parsers
│   ├── python_parser.py     Python AST + Jedi parser (CodeGraphVisitor, ImportTable)
│   ├── typescript_parser.py TypeScript/JS parser (tree-sitter, 4 mixins, 48 methods)
│   ├── ts_resolver.py       TypeScript path resolution (tsconfig paths, aliases)
│   └── node_resolver.py     Node.js-based TS Compiler API resolver
│
├── resolvers/
│   ├── api_mapper.py        Frontend fetch/axios → backend route handlers (CALLS_API)
│   ├── sql_mapper.py        Function → database table (READS_TABLE / WRITES_TABLE)
│   ├── infra_mapper.py      Dockerfiles, compose, env vars (DEPENDS_ON, USES_ENV)
│   ├── ci_mapper.py         GitHub Actions workflows, jobs, secrets
│   ├── ffi_mapper.py        NAPI, koffi, ffi-napi, WASM bridges (CALLS_NATIVE)
│   ├── lsp_client.py        LSP protocol client (used by Pyright/tsserver resolvers)
│   ├── pyright_resolver.py  Pyright-based Python resolution (alternative to Jedi)
│   ├── tsserver_resolver.py TypeScript language server resolver
│   └── config.py            LSP server configuration
│
├── tools/
│   ├── __init__.py          Tool dispatch (handle_tool_call, hot reload support)
│   ├── schemas.py           JSON schemas for all 60+ tools
│   ├── navigation.py        list_nodes, get_node, connections, search, structure, context, grep
│   ├── modification.py      update_node, patch_node, add_node, delete_node, rename, batch
│   ├── analysis.py          check_impact, validate_change, health, dead_code, find_usages
│   ├── safety.py            vibecheck, nfr_check, test_coverage, security_scan, arch_rules
│   ├── arch.py              class_metrics, project_metrics, largest_classes, components
│   ├── annotation.py        annotate, save_annotation, batch_save, annotation_stats
│   ├── git.py               blame, node_history, commit_scope, recent_changes
│   ├── explain.py           Human-readable function explanation with usage examples
│   ├── session.py           session_write, session_read, handoff, resume
│   ├── resolvers.py         api_map, db_map, env_map, ffi_map, infra_map
│   ├── entry_points.py      Entry point detection (Django, FastAPI, Celery, CLI, tests)
│   ├── patterns.py          Role/side-effect auto-detection from code patterns
│   └── helpers.py           Shared utilities (find_containing_node, resolve_or_fail)
│
├── helpers/
│   └── ts_resolver.js       Node.js script for TS Compiler API resolution
│
└── plugins/                 Future: additional runtime plugins
```

---

## Database

### graph.db (single file)
Contains all graph data:
- **nodes** — id, type, name, file_path, source, hash, signature, docstring, start/end line, annotations
- **edges** — from_node, to_node, type, confidence, source (static/runtime/both)
- **session notes** — persistent key-value storage surviving context resets
- **action log** — every modification with reasoning (used by `lens_resume`)
- **project metrics** — cached class/component metrics

### resolve_cache.db
Jedi resolution cache — speeds up repeated parsing by caching symbol lookups.

---

## Node & Edge Model

### 5 Node Types

| Type | What it represents |
|------|-------------------|
| `module` | A file (one per parsed file) |
| `class` | Class definition |
| `function` | Module-level function |
| `method` | Class method |
| `block` | Module-level code outside functions/classes |

### 16 Edge Types

| Category | Edge Type | Meaning |
|----------|-----------|---------|
| **Code** | `calls` | A calls B |
| | `imports` | A imports B |
| | `uses` | A references B (attribute access) |
| | `inherits` | Class A extends B |
| | `decorates` | Decorator applied to function |
| | `contains` | Nested function/class inside another |
| | `mocks` | Test `@patch("B")` mocks B |
| **Cross-language** | `calls_api` | Frontend HTTP → backend route handler |
| | `calls_native` | JS/TS calls native code via NAPI/koffi/WASM |
| **Database** | `reads_table` | Function SELECTs from table |
| | `writes_table` | Function INSERTs/UPDATEs/DELETEs table |
| | `migrates` | SQL migration creates/alters table |
| **Infrastructure** | `depends_on` | Docker/CI service dependency |
| | `exposes_port` | Service exposes a network port |
| | `uses_env` | Code reads environment variable |
| | `handles_route` | Route decorator → handler function |

### Edge Confidence

| Level | Meaning |
|-------|---------|
| `resolved` | Confirmed by Jedi/Pyright/TS Compiler API |
| `inferred` | AST found the call, resolver didn't confirm |
| `external` | Target is stdlib or pip/npm package |
| `unresolved` | Dynamic dispatch (`getattr`, `eval`) |

### Edge Source (runtime tracing)

| Source | Meaning |
|--------|---------|
| `static` | Found by static analysis only |
| `runtime` | Discovered during test execution (sys.monitoring) |
| `both` | Confirmed by both static analysis and runtime |

---

## Adding a New Parser

1. Create `lenspr/parsers/my_language_parser.py`
2. Implement `BaseParser` interface:
   - `parse_file(path, root_path) -> FileAnalysis` (nodes + edges)
   - `get_file_extensions() -> list[str]`
3. Register in `lenspr/parsers/__init__.py`
4. Add to `MultiParser` in `lenspr/parsers/multi.py`

See `python_parser.py` and `typescript_parser.py` for reference implementations.
