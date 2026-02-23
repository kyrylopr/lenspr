# LensPR

> **Status: Alpha (0.1.x)** — works on real projects, used daily by the author. Expect rough edges.

AI coding assistants work with code as text — they grep, read, and guess at dependencies. This works for simple changes but breaks down when a function has callers across multiple files.

LensPR parses your codebase into a dependency graph and gives your AI the tools to see what depends on what — before making changes.

<!-- TODO: Add demo GIF here -->
<!-- ![Demo](assets/demo.gif) -->

---

## The Problem

To understand one function, your AI makes 5-7 calls:

```
Read file → Grep for callers → Read those files → Grep for tests → Read test files → Piece it together
```

It burns tokens, context window, and often misses things. When it finally makes a change — something breaks three files away.

## What LensPR Does

### 1. One call to understand any function

Instead of 5-7 grep/read calls, one `lens_context` call returns everything: source code, who calls it, what it calls, and related tests.

```
AI without LensPR:          AI with LensPR:
─────────────────           ────────────────
Read file           ─┐      lens_context      → done
Grep "function_name" │
Read caller file     │      (source + 8 callers + 3 callees + 2 tests)
Read another caller  ├→
Grep for tests       │
Read test file       │
Piece it together   ─┘
```

### 2. Impact analysis before every change

Before any modification, LensPR tells the AI:

```
severity: CRITICAL
direct callers: 15
indirect callers: 23
inheritors: 2
tests: 0
⚠️ Affects auth and payments modules
```

The AI sees this and either warns you, changes its approach, or asks for confirmation. No more blind edits.

### 3. Cross-language visibility

LensPR connects your frontend and backend into a single graph:

```
Frontend:  LoginModal → fetch("/api/auth/login")
                              ↓ CALLS_API
Backend:   @router.post("/login") → def login()       # FastAPI/Flask
           app.post("/login", handler)                 # Express/Fastify/Hono
```

It also tracks database operations, Docker services, environment variables, FFI bridges (NAPI/koffi/WASM), CI/CD workflows, and raw SQL migrations — so the AI sees the full picture, not just one language at a time.

### 4. Search with context

Normal grep: `utils.py:42: # TODO fix this`

LensPR grep: `utils.py:42: # TODO fix this → inside validate_payment()`

Every search result shows which function contains the match. The AI immediately knows the context without opening the file.

---

## Quick Start

Requires **Python 3.10+**, **macOS or Linux**. For TypeScript/JS projects, also **Node.js 18+**.

```bash
pip install 'lenspr[all]'
cd ./my-project
lenspr init .
lenspr setup .
```

Restart your IDE. Your AI now has access to `lens_*` tools.

| Step | What happens |
|------|-------------|
| `lenspr init .` | Parses your code into a dependency graph (`.lens/graph.db`) |
| `lenspr setup .` | Registers MCP server for your IDE (`.mcp.json`) |

> **Add `.lens/` to your `.gitignore`** — the graph is local and rebuilt from source.

---

## How It Works

```
Your code (.py, .ts, .tsx, .js, .jsx) + infra (.sql, Dockerfile, CI workflows)
       │
       ▼
  8-pass pipeline:
    1. AST parsing       (Python ast + tree-sitter for JS/TS)
    2. Name resolution   (Jedi / Pyright / TS Compiler API)
    3. Edge normalization (cross-file ID matching)
    4. API mapping       (frontend HTTP → backend route)
    5. SQL mapping       (function → database table + raw .sql files)
    6. Infra mapping     (Docker services, Dockerfiles, env vars)
    7. FFI mapping       (NAPI, koffi, ffi-napi, WASM bridges)
    8. CI/CD mapping     (GitHub Actions workflows, jobs, secrets)
       │
       ▼
  Unified dependency graph (SQLite, local, never leaves your machine)
       │
       ▼
  60 MCP tools for your AI assistant  (see full list below)
       │
       ▼
  File watcher auto-syncs on every save
```

Everything runs locally. Your code never leaves your machine.

---

### Tested On

Internal testing on a production monorepo (257 files, Python + React + Docker):

| Metric | Value |
|--------|-------|
| **Internal nodes** | 3,222 (1,083 functions, 1,073 methods, 380 classes) |
| **Total edges** | 28,113 across 12 edge types |
| **Confidence** | 79.2% resolved, only 32 unresolved (all `getattr` — expected) |
| **Circular imports** | 0 |
| **reads_table resolution** | 100% (216/216) |
| **writes_table resolution** | 97% (70/72) |
| **Cross-language API edges** | 120 (React → FastAPI) |
| **End-to-end visibility** | `lens_context(handler, depth=2)` traces from React component to SQL table |

Example — one call shows the full login chain:

```
LoginModal.jsx → authAPI.login() → POST /api/auth/login
  → login(request, db)
    → db.query(User)           [reads: users]
    → verify_password()
    → create_jwt_token()
```

---

## Key Features

| Feature | What it does |
|---------|-------------|
| **Impact Analysis** | Shows severity (LOW → CRITICAL) before any change |
| **One-Call Context** | Source + callers + callees + tests in a single request |
| **Cross-Language Edges** | Frontend HTTP calls matched to backend route handlers (CALLS_API) |
| **Database Mapping** | Tracks which functions read/write which tables (READS_TABLE / WRITES_TABLE) |
| **Infra Mapping** | Docker services, Dockerfiles, env vars, CI/CD workflows |
| **FFI Mapping** | Detects NAPI, koffi, ffi-napi, WASM bridges between JS/TS and native code |
| **Surgical Edits** | `lens_patch_node` for targeted find/replace — no full rewrites needed |
| **Test Runner** | `lens_run_tests` runs pytest with auto-tracing and structured results |
| **Runtime Call Tracing** | Merges runtime edges from test execution into the static graph |
| **Session Memory** | Persistent notes survive context resets — AI picks up where it left off |
| **Graph-Aware Search** | Every grep result shows which function contains the match |
| **Auto-Sync** | Graph updates on every file save, AI always sees latest code |
| **Dead Code Detection** | Finds unreachable functions (supports Django, FastAPI, Celery, etc.) |
| **Git at Function Level** | Blame, history, and commit scope per function, not per file |
| **Atomic Changes** | Multi-file updates either all apply or all roll back |
| **Cross-Project Rename** | Rename a function and update every reference |
| **Large File Safety** | Blocks edits on 10K+ char nodes; integrity check catches truncated LLM output |
| **Vibecoding Health Score** | `lens_vibecheck` gives 0-100 score (A–F) across 6 dimensions |
| **NFR Checks** | `lens_nfr_check` flags missing error handling, hardcoded secrets, missing auth |
| **Architecture Rules** | Enforce boundaries between layers — violations block changes automatically |
| **Security Scanning** | `lens_security_scan` runs Bandit; `lens_dep_audit` checks CVEs in dependencies |
| **Remediation Plans** | `lens_fix_plan` generates prioritized action list to improve health score |
| **Hotspot Analysis** | `lens_hotspots` finds functions that change most frequently |

---

## Requirements

| Requirement | Details |
|-------------|---------|
| **Python** | 3.10+ |
| **OS** | macOS, Linux. **Windows is not supported.** |
| **Node.js** | 18+ (only needed for TypeScript/JavaScript projects) |

## Works With

| IDE | Setup |
|-----|-------|
| **Claude Code** | `lenspr setup .` — works automatically |
| **Cursor** | Copy `.mcp.json` to `.cursor/mcp.json` |
| **Any MCP client** | Run `lenspr serve <path>` as MCP server |

### Supported Languages

| Language | Parser | Resolution | Notes |
|----------|--------|------------|-------|
| Python | AST + Jedi (or Pyright) | 95%+ | Module-level functions fully tracked. `self.method()` calls have limited resolution without runtime tracing. |
| TypeScript / JavaScript | tree-sitter + TS Compiler API | 85-95% | Requires `node_modules` installed. Auto-installs during `lenspr init`. |

### Infrastructure & Config Files

| File Type | What gets extracted |
|-----------|-------------------|
| `.sql` files | CREATE TABLE, INSERT, SELECT, ALTER, DROP, pg_cron schedules |
| `Dockerfile*` | Base images, stages, ENV/ARG, EXPOSE, COPY --from, entrypoint |
| `.github/workflows/*.yml` | Workflow name, triggers, jobs, needs, steps, uses, secrets/env refs |
| `docker-compose.yml` | Services, ports, depends_on, environment |
| `.env` files | Variable definitions |

### Cross-Language Connections

| Edge Type | What it connects |
|-----------|-----------------|
| `CALLS_API` | Frontend `fetch("/api/auth/login")` → Backend `@router.post("/login")` or `app.get("/path", handler)` |
| `CALLS_NATIVE` | TS/JS `require("./addon.node")` / `koffi.load("lib.so")` → Native module |
| `READS_TABLE` / `WRITES_TABLE` | Python function → SQLAlchemy/Django table |
| `MIGRATES` | SQL migration → table (from raw `.sql` files) |
| `DEPENDS_ON` | Docker service / CI job → dependency (from compose, Dockerfiles, GitHub Actions) |
| `USES_ENV` | Code `os.getenv("KEY")` / `import.meta.env.VITE_KEY` / `${{ secrets.KEY }}` → env var |

---

## Node & Edge Model

### 5 Node Types

| Type | What it represents |
|------|-------------------|
| `module` | A file (one per parsed file) |
| `class` | Class definition |
| `function` | Module-level function |
| `method` | Class method |
| `block` | Module-level code outside functions/classes (constants, guards, imports) |

### 16 Edge Types

| Category | Edge Type | Meaning |
|----------|-----------|---------|
| **Code** | `calls` | A calls B |
| | `imports` | A imports B |
| | `uses` | A references B (attribute access) |
| | `inherits` | Class A extends class B |
| | `decorates` | Decorator applied to function |
| | `contains` | Nested function/class inside another |
| | `mocks` | Test `@patch("B")` mocks B |
| **Cross-language** | `calls_api` | Frontend HTTP call → backend route handler |
| | `calls_native` | TS/JS calls native code via NAPI/koffi/FFI/WASM |
| **Database** | `reads_table` | Function SELECTs from table |
| | `writes_table` | Function INSERTs/UPDATEs/DELETEs table |
| | `migrates` | SQL migration creates/alters a table |
| **Infrastructure** | `depends_on` | Docker/CI service/job dependency |
| | `exposes_port` | Service exposes a network port |
| | `uses_env` | Code reads environment variable |
| | `handles_route` | Route decorator → handler function |

### Edge Confidence

| Level | Meaning |
|-------|---------|
| `resolved` | Confirmed by Jedi/Pyright/TS — target exists in project |
| `inferred` | AST found the call, resolver didn't confirm |
| `external` | Target is stdlib or pip/npm package |
| `unresolved` | Dynamic dispatch (`getattr`, `eval`) — can't determine statically |

---

## CLI

```bash
lenspr init <path>           # Build the code graph
lenspr setup <path>          # Create .mcp.json for your IDE
lenspr tools list            # Manage tool groups (enable/disable)
lenspr status <path>         # Show graph stats
lenspr search <path> "query" # Find functions by name
lenspr impact <path> <node>  # Check what breaks
lenspr doctor <path>         # Diagnose configuration issues
```

<details>
<summary>All 60 MCP tools by category</summary>

### Navigation & Search (8 tools)
| Tool | Description |
|------|-------------|
| `lens_context` | Source + callers + callees + tests in one call |
| `lens_get_node` | Get source code of a specific node |
| `lens_search` | Search by name, code, or docstring |
| `lens_grep` | Regex search with graph context (shows containing function) |
| `lens_find_usages` | All callers, importers, inheritors (batch mode supported) |
| `lens_get_structure` | Project overview (compact/summary/full modes) |
| `lens_list_nodes` | List all nodes with type/file/name filters |
| `lens_get_connections` | Direct callers and callees for a node |

### Modification (6 tools)
| Tool | Description |
|------|-------------|
| `lens_update_node` | Replace full node source with syntax validation |
| `lens_patch_node` | Surgical find/replace within a node (safer for small changes) |
| `lens_add_node` | Add new function or class to a file |
| `lens_delete_node` | Remove a node from the codebase |
| `lens_rename` | Rename a function/class/method across entire project |
| `lens_batch` | Atomic multi-node updates with rollback on failure |

### Analysis (6 tools)
| Tool | Description |
|------|-------------|
| `lens_check_impact` | Severity (LOW→CRITICAL) + affected nodes before any change |
| `lens_validate_change` | Dry-run validation without applying changes |
| `lens_health` | Graph quality: nodes/edges, confidence %, docstrings, circular imports |
| `lens_dead_code` | Find unreachable code (auto-detects entry points) |
| `lens_dependencies` | External packages used, grouped by package or file |
| `lens_diff` | Show what changed since last sync |

### Testing & Runtime Tracing (3 tools)
| Tool | Description |
|------|-------------|
| `lens_run_tests` | Run pytest with structured results, auto-coverage, auto-tracing |
| `lens_trace` | Run tests with runtime call tracing (Python 3.12+, ~5% overhead) |
| `lens_trace_stats` | Static vs runtime edge statistics and confirmation rate |

### Vibecoding Safety (7 tools)
| Tool | Description |
|------|-------------|
| `lens_vibecheck` | 0–100 health score (grade A–F) across 6 dimensions |
| `lens_nfr_check` | Flag missing error handling, logging, secrets, auth per function |
| `lens_test_coverage` | Runtime (pytest-cov) + graph-based coverage report |
| `lens_security_scan` | Run Bandit security scanner, results mapped to graph nodes |
| `lens_dep_audit` | Check dependencies for known CVEs (pip-audit / npm audit) |
| `lens_fix_plan` | Prioritized remediation plan (CRITICAL→LOW) to improve health score |
| `lens_generate_test_skeleton` | Test spec with scenarios, mocks, and real usage examples |

### Architecture Rules (4 tools)
| Tool | Description |
|------|-------------|
| `lens_arch_rule_add` | Define a rule enforced on every code change |
| `lens_arch_rule_list` | List all defined rules with config |
| `lens_arch_rule_delete` | Remove a rule by ID |
| `lens_arch_check` | Check all rules against current codebase |

### Architecture Metrics (5 tools)
| Tool | Description |
|------|-------------|
| `lens_class_metrics` | Pre-computed class metrics (methods, lines, percentile rank) |
| `lens_project_metrics` | Project-wide class statistics (avg/median/p90/p95) |
| `lens_largest_classes` | Classes sorted by method count (descending) |
| `lens_compare_classes` | Side-by-side metrics comparison of multiple classes |
| `lens_components` | Directory-based component cohesion analysis |

### Cross-Language & Infrastructure (5 tools)
| Tool | Description |
|------|-------------|
| `lens_api_map` | Frontend API calls → backend route handlers (Flask/FastAPI/Express/Fastify/Hono/Koa) |
| `lens_db_map` | Database tables → functions that read/write them (SQLAlchemy/Django/raw SQL) |
| `lens_env_map` | Environment variables: definitions, usages, undefined vars |
| `lens_ffi_map` | FFI bridges: NAPI, koffi, ffi-napi, WASM between TS/JS and native code |
| `lens_infra_map` | Dockerfiles, CI/CD workflows, compose services, secrets |

### Git Integration (4 tools)
| Tool | Description |
|------|-------------|
| `lens_blame` | Who wrote each line of a function |
| `lens_node_history` | Commits that modified a specific function |
| `lens_commit_scope` | What nodes a specific commit affected |
| `lens_recent_changes` | Recently modified nodes from git history |

### Temporal Analysis (2 tools)
| Tool | Description |
|------|-------------|
| `lens_hotspots` | Functions that change most frequently (risk indicator) |
| `lens_node_timeline` | Unified timeline: LensPR history (with reasoning) + git commits |

### Explanation (1 tool)
| Tool | Description |
|------|-------------|
| `lens_explain` | Human-readable explanation with callers, callees, usage examples |

### Semantic Annotations (5 tools)
| Tool | Description |
|------|-------------|
| `lens_annotate` | Generate annotation suggestion for a node |
| `lens_save_annotation` | Save summary, role, and side effects to a node |
| `lens_batch_save_annotations` | Annotate many nodes in one call |
| `lens_annotate_batch` | Get nodes that need annotation |
| `lens_annotation_stats` | Annotation coverage statistics |

### Session Memory (4 tools)
| Tool | Description |
|------|-------------|
| `lens_session_write` | Save a persistent note (survives context resets) |
| `lens_session_read` | Read all session notes to restore context |
| `lens_session_handoff` | Generate handoff doc for next session |
| `lens_resume` | Restore context from auto-generated action log |

</details>

### Tool Groups

LensPR's 60 tools are organized into 12 groups. All groups are enabled by default. Disable unneeded groups to save context window space:

```bash
lenspr tools list              # Show all groups with status
lenspr tools disable infrastructure tracing   # Disable groups
lenspr tools enable git        # Enable groups
lenspr tools reset             # Re-enable all groups
```

| Group | Tools | Description |
|-------|-------|-------------|
| **core** | 7 | Navigation & search (always on) |
| **modification** | 6 | Code changes — update, patch, add, delete, rename |
| **analysis** | 7 | Impact analysis — check what breaks before changes |
| **quality** | 8 | Vibecoding safety — health score, NFR checks, coverage |
| **architecture** | 9 | Architecture rules & metrics |
| **git** | 4 | Blame, history, commit scope at function level |
| **annotations** | 5 | Semantic annotations — summaries, roles, side effects |
| **session** | 4 | Session memory — persistent notes across context resets |
| **infrastructure** | 5 | Cross-language mappers — API routes, DB tables, env vars |
| **temporal** | 2 | Change hotspots, unified timelines |
| **tracing** | 2 | Runtime call tracing |
| **explain** | 1 | Code explanation with usage examples |

The `lenspr setup` command includes interactive group selection. Config is saved in `.lens/config.json`.

---

## Installation Options

```bash
pip install lenspr                # Core (Python projects only)
pip install 'lenspr[mcp]'        # + MCP server (needed for IDE integration)
pip install 'lenspr[typescript]'  # + TypeScript/JS parser
pip install 'lenspr[all]'        # Everything (recommended)
```

---

## Vibecoding Safety

AI agents write a lot of code fast. LensPR adds a safety layer that catches common quality problems before they accumulate.

### Health Score

```
lens_vibecheck()
→ score: 86/100
→ grade: B
→ breakdown:
    test_coverage:    17/25  — 67% tested (410/614 functions)
    dead_code:        20/20  — 0% dead in production code
    circular_imports: 15/15  — 0 cycles
    architecture:     12/15  — 1 violation
    documentation:     8/10  — 81% have descriptions
    graph_confidence: 14/15  — 94% internal edges resolved
```

Run `lens_vibecheck()` periodically to track whether the codebase is improving or degrading.

### NFR Checks

`lens_nfr_check(node_id)` checks a function for:
- IO/network/DB operations without `try/except`
- Hardcoded secrets (passwords, API keys, tokens)
- Missing structured logging in large functions
- Handler/endpoint with no input validation
- Auth-sensitive operation (create/delete/update) with no auth check

### Architecture Rules

Enforce structural boundaries so they can't be violated by accident:

```python
# Parsers must not depend on tool handlers
lens_arch_rule_add(rule_type="no_dependency",
    config={"from_pattern": "*.parsers.*", "to_pattern": "*.tools.*"})

# No class should grow beyond 20 methods
lens_arch_rule_add(rule_type="max_class_methods",
    config={"threshold": 20})

# Every handle_* function must have a test
lens_arch_rule_add(rule_type="required_test",
    config={"pattern": "handle_*"})
```

Rules are checked automatically on every `lens_update_node` call. Violations appear as warnings before any change is applied.

---

## Known Limitations

### What works well

- **Module-level functions** — callers, callees, impact analysis, dead code detection are reliable
- **Cross-language edges** — frontend-to-backend API mapping, DB table tracking, env vars
- **Infrastructure** — Docker, CI/CD, compose services, SQL migrations
- **Git integration** — blame, history, commit scope at function level (uses git directly)

### What doesn't work well yet

- **`self.method()` calls** — the static parser can't resolve instance method dispatch (`self.foo()` → which `foo`?). This means `lens_explain` and `lens_get_connections` return incomplete results for ~50% of nodes (class methods). Workaround: `lens_trace` on Python 3.12+ resolves some of these at runtime.
- **Windows** — `fcntl` dependency means LensPR won't run on Windows. macOS and Linux only.
- **Dynamic code** — `getattr`, `eval`, dynamic imports can't be tracked statically (~0.1% of edges in practice)
- **Security scanning** — `lens_security_scan` and `lens_dep_audit` require optional deps (`pip install bandit`, `pip install pip-audit`)

---

## Roadmap

### Zero-touch setup (coming soon)

The current setup requires 3 commands and creates files in your project. We're working on a zero-invasion mode:

```bash
# One-time global install (not per-project):
pipx install lenspr
lenspr install
# Done. Works on every project you open. No per-project setup.
```

| Current | Planned |
|---------|---------|
| `pip install` in each project venv | `pipx install` once globally |
| `lenspr init .` per project | Auto-init on first tool call |
| `lenspr setup .` creates `.mcp.json` | `lenspr install` registers MCP globally |
| `.lens/` directory in your project | Graph stored in `~/.lenspr/projects/` |
| `.gitignore` edit needed | Nothing to ignore |

**Zero files added to your project. Zero configuration. Zero dependencies.**

Projects that want explicit control (team-shared graphs, pinned config) can still use `lenspr init .` to opt in to per-project mode.

### Also planned

- **Multi-agent setup** — auto-generate config for Cursor, Windsurf, Cline (not just Claude)
- **Cloud graph API** — hosted service, no local setup needed at all
- **Agent contributions** — agents that use LensPR can submit improvements back automatically

---

## Contributing

Contributions welcome:
- **Language parsers** — Go, Rust, Java (BaseParser interface is ready)
- **Mapper plugins** — GitLab CI, Terraform, Kubernetes manifests
- **Bug reports** — even "this doesn't work" is helpful
- **Ideas** — [open an issue](https://github.com/kyrylopr/lenspr/issues)

## License

MIT

---

Built because AI kept breaking my code.
