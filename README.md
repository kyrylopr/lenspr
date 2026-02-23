# LensPR

**AI coding assistants break code because they don't see dependencies. LensPR fixes that.**

Your AI assistant treats code as text files. It greps, reads, and guesses. When it changes a function, it has no idea that 12 other functions depend on it.

LensPR parses your codebase into a dependency graph and gives your AI the tools to understand it — before making changes.

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
Backend:   @router.post("/login") → def login()
```

It also tracks database operations, Docker services, and environment variables — so the AI sees the full picture, not just one language at a time.

### 4. Search with context

Normal grep: `utils.py:42: # TODO fix this`

LensPR grep: `utils.py:42: # TODO fix this → inside validate_payment()`

Every search result shows which function contains the match. The AI immediately knows the context without opening the file.

---

## Quick Start

```bash
pip install 'lenspr[all]'
cd ./my-project
lenspr init .
lenspr setup .
```

Restart your IDE. Your AI now has access to `lens_*` tools.

That's it.

---

## How It Works

```
Your code (.py, .ts, .tsx, .js, .jsx)
       │
       ▼
  6-pass pipeline:
    1. AST parsing      (Python ast + tree-sitter for JS/TS)
    2. Name resolution   (Jedi / Pyright / TS Compiler API)
    3. Edge normalization (cross-file ID matching)
    4. API mapping       (frontend HTTP → backend route)
    5. SQL mapping       (function → database table)
    6. Infra mapping     (Docker services, env vars)
       │
       ▼
  Unified dependency graph (SQLite, local, never leaves your machine)
       │
       ▼
  58 MCP tools for your AI assistant
       │
       ▼
  File watcher auto-syncs on every save
```

Everything runs locally. Your code never leaves your machine.

---

## Benchmarks

| Metric | Without LensPR | With LensPR | Improvement |
|--------|----------------|-------------|-------------|
| **Task Completion** | 33% (1/3) | 100% (3/3) | **+200%** |
| **Tokens Used** | 1.27M | 388K | **-70%** |
| **API Calls** | 84 | 38 | **-55%** |

<details>
<summary>Detailed results</summary>

| Task | Without | With | Status |
|------|---------|------|--------|
| Understand Function | 602K tokens | 131K tokens | Both passed |
| Find All Usages | 623K tokens | 137K tokens | With: passed, Without: failed |
| Safe Code Change | Rate limit | 121K tokens | With: passed, Without: failed |

Run yourself: `make benchmark`

</details>

### Real-World Validation

Tested on a production monorepo (257 files, Python + React + Docker):

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
| **Infra Mapping** | Docker service dependencies, env var usage across code |
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

## Works With

- **Claude Code** (via MCP)
- **Cursor** (via MCP)
- **Any MCP-compatible AI assistant**

### Supported Languages

| Language | Parser | Resolution |
|----------|--------|------------|
| Python | AST + Jedi (or Pyright) | 95%+ |
| TypeScript / JavaScript | tree-sitter + TS Compiler API | 90%+ |

### Cross-Language Connections

| Edge Type | What it connects |
|-----------|-----------------|
| `CALLS_API` | Frontend `fetch("/api/auth/login")` → Backend `@router.post("/login")` |
| `READS_TABLE` / `WRITES_TABLE` | Python function → SQLAlchemy/Django table |
| `DEPENDS_ON` | Docker service → service (from docker-compose.yml) |
| `USES_ENV` | Code `os.getenv("KEY")` / `import.meta.env.VITE_KEY` → env var |

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

### 12 Edge Types

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
| **Database** | `reads_table` | Function SELECTs from table |
| | `writes_table` | Function INSERTs/UPDATEs/DELETEs table |
| **Infrastructure** | `depends_on` | Docker service dependency |
| | `uses_env` | Code reads environment variable |

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
lenspr status <path>         # Show graph stats
lenspr search <path> "query" # Find functions by name
lenspr impact <path> <node>  # Check what breaks
lenspr doctor <path>         # Diagnose configuration issues
```

<details>
<summary>All MCP tools (58)</summary>

### Navigation & Search
| Tool | Description |
|------|-------------|
| `lens_context` | Source + callers + callees + tests in one call |
| `lens_get_node` | Get source code of a node |
| `lens_search` | Search by name, code, or docstring |
| `lens_grep` | Regex search with graph context |
| `lens_find_usages` | All callers, importers, inheritors |
| `lens_get_structure` | Project overview |
| `lens_list_nodes` | List all nodes with filters |
| `lens_get_connections` | Direct callers/callees |

### Analysis & Safety
| Tool | Description |
|------|-------------|
| `lens_check_impact` | Severity + affected nodes before changes |
| `lens_validate_change` | Dry-run validation without applying |
| `lens_health` | Graph quality stats |
| `lens_dead_code` | Find unreachable code |
| `lens_dependencies` | External packages used |
| `lens_diff` | Changes since last sync |

### Modification
| Tool | Description |
|------|-------------|
| `lens_update_node` | Replace full node source with validation |
| `lens_patch_node` | Surgical find/replace within a node |
| `lens_add_node` | Add new function/class |
| `lens_delete_node` | Remove a node |
| `lens_rename` | Rename across project |
| `lens_batch` | Atomic multi-node updates |

### Testing & Tracing
| Tool | Description |
|------|-------------|
| `lens_run_tests` | Run pytest with structured results + auto-coverage |
| `lens_trace` | Run tests with runtime call tracing (Python 3.12+) |
| `lens_trace_stats` | Show static vs runtime edge statistics |

### Git Integration
| Tool | Description |
|------|-------------|
| `lens_blame` | Who wrote each line |
| `lens_node_history` | Commits per function |
| `lens_commit_scope` | What a commit affected |
| `lens_recent_changes` | Recently modified nodes |

### Semantic Annotations
| Tool | Description |
|------|-------------|
| `lens_annotate` | Generate annotation context |
| `lens_save_annotation` | Save summary + role |
| `lens_batch_save_annotations` | Annotate many nodes at once |
| `lens_annotate_batch` | Get nodes needing annotation |
| `lens_annotation_stats` | Coverage statistics |

### Architecture Metrics
| Tool | Description |
|------|-------------|
| `lens_class_metrics` | Pre-computed class metrics |
| `lens_project_metrics` | Project-wide statistics |
| `lens_largest_classes` | Classes sorted by size |
| `lens_compare_classes` | Compare class metrics |
| `lens_components` | Component cohesion analysis |

### Explanation
| Tool | Description |
|------|-------------|
| `lens_explain` | Human-readable explanation of a node |

### Cross-Language & Infrastructure
| Tool | Description |
|------|-------------|
| `lens_api_map` | Map frontend API calls to backend route handlers |
| `lens_db_map` | Map database tables to functions that read/write them |
| `lens_env_map` | Map environment variables across code and config files |

### Session Memory
| Tool | Description |
|------|-------------|
| `lens_session_write` | Save a persistent note by key |
| `lens_session_read` | Read all session notes |
| `lens_session_handoff` | Generate handoff doc for next session |
| `lens_resume` | Restore context from auto-generated action log |

### Vibecoding Safety
| Tool | Description |
|------|-------------|
| `lens_vibecheck` | 0-100 health score (grade A–F) across 6 dimensions |
| `lens_nfr_check` | Flag missing error handling, logging, secrets, auth per function |
| `lens_test_coverage` | Graph-based + pytest-cov coverage report |
| `lens_security_scan` | Run Bandit security scanner, results mapped to graph nodes |
| `lens_dep_audit` | Check dependencies for known CVEs (pip-audit / npm audit) |
| `lens_fix_plan` | Prioritized remediation plan to improve health score |
| `lens_generate_test_skeleton` | Test spec with scenarios, mocks, and usage examples |

### Architecture Rules
| Tool | Description |
|------|-------------|
| `lens_arch_rule_add` | Define a rule enforced on every code change |
| `lens_arch_rule_list` | List all defined rules |
| `lens_arch_rule_delete` | Remove a rule by ID |
| `lens_arch_check` | Check all rules against current codebase |

### Temporal Analysis
| Tool | Description |
|------|-------------|
| `lens_hotspots` | Find functions that change most frequently |
| `lens_node_timeline` | Unified timeline of changes (LensPR + git) |

</details>

---

## Installation Options

```bash
pip install lenspr                # Core (Python only)
pip install 'lenspr[mcp]'        # + MCP server
pip install 'lenspr[typescript]'  # + TypeScript/JS parser
pip install 'lenspr[all]'        # Everything
```

TypeScript support requires Node.js 18+.

---

## Vibecoding Safety

AI agents write a lot of code fast. LensPR adds a safety layer that catches common quality problems before they accumulate.

### Health Score

```
lens_vibecheck()
→ score: 85/100
→ grade: B
→ breakdown:
    test_coverage:    16/25  — 64% tested
    dead_code:       20/20  — 0% dead
    circular_imports:15/15  — 0 cycles ✓
    architecture:    12/15  — 1 violation
    documentation:    8/10  — 81% have descriptions
    graph_confidence:14/15  — 95% edges resolved
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

- **Dynamic code** (`getattr`, `eval`, dynamic imports) can't be fully tracked — accounts for ~0.1% of edges in practice
- **Instance method dispatch** — `self.method()` calls have limited resolution without runtime tracing (`lens_trace` resolves these on Python 3.12+)
- **Not tested on >10k files** — validated on projects up to 257 files / 3,222 nodes
- **TypeScript needs Node.js 18+** for full type inference

---

## Contributing

Contributions welcome:
- **Language parsers** — Go, Rust, Java (BaseParser interface is ready)
- **Bug reports** — even "this doesn't work" is helpful
- **Ideas** — [open an issue](https://github.com/kyrylopr/lenspr/issues)

## License

MIT

---

Built because AI kept breaking my code.
