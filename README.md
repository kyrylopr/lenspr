# LensPR

**Code-as-graph for safe LLM-assisted development.**

LensPR parses your Python codebase into a directed graph (nodes = functions, classes, modules; edges = calls, imports, inheritance) and gives LLMs structured tools to navigate, analyze impact, and safely modify code.

## The Problem

LLMs see code as text. They don't know that the function on line 50 is called from three places in other files. Changes happen without understanding consequences. Bugs surface later.

## The Solution

```
Source Files → AST Parser → Graph (SQLite + NetworkX) → Tools (CLI / MCP / API)
```

LensPR provides:
- **Impact analysis** before every change — know what breaks before it breaks
- **Structured navigation** — LLMs explore code through graph queries, not file reads
- **Safe patching** — validated changes applied to original files, no regeneration
- **Confidence scoring** — explicit about what the graph can and cannot see
- **Change history** — every modification tracked with rollback capability

## Installation

```bash
# Basic install
pip install lenspr

# With MCP server support (for Claude Code / Claude Desktop)
pip install lenspr[mcp]

# Development install
pip install lenspr[dev]
```

## Quick Start

### CLI

```bash
# Initialize LensPR on your project
lenspr init ./my_project

# Re-sync after code changes
lenspr sync ./my_project

# View project stats
lenspr status ./my_project

# Search for functions/classes
lenspr search ./my_project "validate"

# Check impact before changing a function
lenspr impact ./my_project app.models.User

# Start MCP server (requires lenspr[mcp])
lenspr serve ./my_project

# Start MCP server with hot-reload (for development)
lenspr serve ./my_project --dev
```

### Python API

```python
import lenspr

# Initialize on your project
lenspr.init("./my_project")

# List all functions
nodes = lenspr.list_nodes(type="function")

# Get source code of a specific node
node = lenspr.get_node("app.models.User")

# Check what would break if you change a node
impact = lenspr.check_impact("app.models.User")

# See connections (who calls this, what does it call)
connections = lenspr.get_connections("app.utils.validate_email")

# Search by name, code, or docstring
results = lenspr.list_nodes()  # all nodes

# Get full context in one call (source + callers + callees + tests)
context = lenspr.handle_tool("lens_context", {"node_id": "app.utils.validate"})

# Check graph health
health = lenspr.handle_tool("lens_health", {})
print(f"Nodes: {health['data']['total_nodes']}, Confidence: {health['data']['confidence_pct']}%")
```

### With Claude API

```python
import anthropic
import lenspr

# Initialize
lenspr.init("./my_project")
tools = lenspr.get_claude_tools()
system_prompt = lenspr.get_system_prompt()

# Create Claude client
client = anthropic.Anthropic()

# Claude can now use lens_* tools to navigate and modify code
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    system=system_prompt,
    tools=tools,
    messages=[{"role": "user", "content": "Add error handling to the fetch_page function"}],
)

# Handle tool calls
for block in response.content:
    if block.type == "tool_use":
        result = lenspr.handle_tool(block.name, block.input)
        print(result)
```

### With Claude Code (MCP)

Create `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "lenspr": {
      "command": "lenspr",
      "args": ["serve", "/absolute/path/to/your/project"]
    }
  }
}
```

Restart Claude Code — the `lens_*` tools will be available automatically.

The MCP server automatically watches for file changes and re-syncs the graph (using watchdog or polling fallback).

### With Claude Desktop (MCP)

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lenspr": {
      "command": "lenspr",
      "args": ["serve", "/absolute/path/to/your/project"]
    }
  }
}
```

## Available Tools (27 total)

### Navigation & Discovery

| Tool | Description |
|------|-------------|
| `lens_list_nodes` | List all functions/classes/modules with type/file/name filters |
| `lens_get_node` | Get full source code of a specific node |
| `lens_get_connections` | See what calls a node and what it calls |
| `lens_context` | **One call = source + callers + callees + tests + annotations** |
| `lens_search` | Search nodes by name, code content, or docstring |
| `lens_grep` | Text/regex search with graph context (which function contains each match) |
| `lens_get_structure` | Compact project overview with pagination (summary mode for large projects) |
| `lens_find_usages` | Find all callers, importers, and inheritors of a node |

### Analysis & Safety

| Tool | Description |
|------|-------------|
| `lens_check_impact` | **Always call before modifying** — shows severity (CRITICAL/HIGH/MEDIUM/LOW) |
| `lens_validate_change` | Dry-run validation: check what would happen without applying |
| `lens_health` | Graph quality report: nodes, edges, confidence %, docstring coverage |
| `lens_diff` | Show what changed since last sync (added/modified/deleted files) |
| `lens_dead_code` | Find unreachable code from entry points |
| `lens_dependencies` | List all external dependencies (stdlib, third-party) |

### Modification

| Tool | Description |
|------|-------------|
| `lens_update_node` | Update node source with 3-level validation + proactive warnings |
| `lens_add_node` | Add new function/class to a file |
| `lens_delete_node` | Remove a node from the codebase |
| `lens_rename` | Rename a function/class across the entire project |
| `lens_batch` | Apply multiple updates atomically (all-or-nothing) |

### Semantic Annotations

| Tool | Description |
|------|-------------|
| `lens_annotate` | Generate suggested role, side_effects from code analysis |
| `lens_save_annotation` | Save semantic annotations (summary, role, side_effects, inputs, outputs) |
| `lens_annotate_batch` | Get nodes needing annotation (unannotated or stale) |
| `lens_annotation_stats` | Coverage stats: annotated %, breakdown by type and role |

**Node Roles:** `validator`, `transformer`, `io`, `orchestrator`, `pure`, `handler`, `test`, `utility`, `factory`, `accessor`

### Git Integration

| Tool | Description |
|------|-------------|
| `lens_blame` | Git blame for a node's source lines (who wrote what, when) |
| `lens_node_history` | Commit history for a specific node (line-level tracking) |
| `lens_commit_scope` | What nodes were affected by a specific commit |
| `lens_recent_changes` | Recently modified nodes from git log |

**Proactive Warnings in `lens_update_node`:**
- ⚠️ HIGH IMPACT: >10 callers affected
- ⚠️ NO TESTS: No test coverage for this node
- ⚠️ CIRCULAR: Part of a circular import

## Architecture

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

### Key Design Decisions

- **Patcher, not generator** — files are patched in place, never regenerated. Only the changed lines are touched.
- **SQLite is the single source of truth** — NetworkX graph is a read-only cache rebuilt on demand.
- **3-level validation** — every code change is checked for valid syntax, preserved structure (function stays function), and signature compatibility.
- **Bottom-to-top patching** — multiple patches in one file are applied from the end upward to avoid line number corruption.
- **Pluggable parsers** — `BaseParser` interface ready for JS/TS/Go/Rust parsers.
- **Confidence scoring** — edges marked as `resolved` (jedi confirmed), `inferred` (AST-based), or `unresolved` (dynamic dispatch).
- **Change tracking** — every modification recorded in `history.db` with old/new source and affected nodes list.

## Project Structure

```
lenspr/
├── lenspr/
│   ├── __init__.py            # Public API (init, sync, list_nodes, check_impact, etc.)
│   ├── models.py              # Data classes (Node, Edge, Change, Patch, etc.)
│   ├── context.py             # LensContext — central state manager
│   ├── database.py            # SQLite operations (3 databases)
│   ├── graph.py               # NetworkX graph algorithms (impact, dead code, cycles)
│   ├── patcher.py             # File patching (PatchBuffer, bottom-to-top apply)
│   ├── validator.py           # 3-level validation (syntax → structure → signature)
│   ├── tracker.py             # Change history and rollback
│   ├── claude_tools.py        # Tool definitions + handlers for Claude API
│   ├── cli.py                 # CLI entry point (init, sync, status, search, impact, serve)
│   ├── mcp_server.py          # MCP server (FastMCP, stdio transport)
│   ├── parsers/
│   │   ├── base.py            # BaseParser interface (pluggable for other languages)
│   │   └── python_parser.py   # Python AST + jedi parser
│   ├── plugins/               # Future: pytest tracer, runtime sampler
│   └── prompts/
│       └── system.md          # Claude system prompt template
├── tests/
│   ├── test_parser.py         # Parser tests (nodes, edges, cross-file resolution)
│   ├── test_database.py       # SQLite CRUD tests
│   ├── test_graph.py          # Graph algorithm tests (impact, dead code, cycles)
│   ├── test_patcher.py        # Patching tests (single, multi, insert, remove)
│   ├── test_validator.py      # Validation tests (syntax, structure, signature)
│   ├── test_tool_operations.py # Tool handler tests (update, add, delete, context, grep, batch, health)
│   ├── test_mcp_server.py     # MCP server tests (watcher, auto-sync, tool wrappers)
│   ├── test_cli.py            # CLI command tests
│   └── fixtures/              # Sample project for testing
│       └── sample_project/
├── Makefile                   # Dev commands (test, lint, typecheck, serve, etc.)
├── pyproject.toml             # Project config (hatchling, ruff, mypy, pytest)
├── LICENSE                    # MIT
└── README.md
```

## Development

```bash
# Clone and setup
git clone https://github.com/kyrylopr/lenspr.git
cd lenspr
python3 -m venv .venv
source .venv/bin/activate
make dev

# Run all checks
make check          # lint + typecheck + test

# Individual commands
make test           # pytest
make test-cov       # pytest with coverage
make lint           # ruff check
make lint-fix       # ruff check --fix
make typecheck      # mypy
make format         # ruff format

# Run MCP server locally
make serve

# Demo: parse lenspr itself
make demo
```

## Requirements

- Python 3.11+
- Core: `networkx`, `jedi`
- MCP server: `mcp`, `watchdog` (optional, install with `pip install lenspr[mcp]`)
- Dev: `pytest`, `pytest-cov`, `ruff`, `mypy`

## License

MIT — see [LICENSE](LICENSE).
