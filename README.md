# LensPR

**LensPR makes Claude 3x more efficient by representing code as a graph.**

Instead of grep/read loops, Claude gets structured tools: one `lens_context` call returns function source + callers + callees + tests.

![LensPR Benchmark Summary](eval/results/chart_summary.png)

| Metric | Without LensPR | With LensPR | Improvement |
|--------|----------------|-------------|-------------|
| **Task Completion** | 33% (1/3) | 100% (3/3) | **+200%** |
| **Tokens Used** | 1.27M | 388K | **-70%** |
| **API Calls** | 84 | 38 | **-55%** |

---

> **This is a learning project.** I'm experimenting with LLM-assisted code development.
> Don't judge too harshly if something doesn't work!
>
> **Want to help?** I'm looking for contributors for JS/TS parser support.
> [Open an issue](https://github.com/kyrylopr/lenspr/issues) to discuss!

---

## Quick Start

### Step 1: Install

```bash
# Recommended: using pipx (isolated, available globally)
pipx install 'lenspr[mcp]'

# Or with pip (in your current environment)
pip install 'lenspr[mcp]'
```

### Step 2: Initialize on your project

```bash
cd ./my_project
lenspr init .                   # Parses all Python files, builds the graph
lenspr setup .                  # Creates .mcp.json config for Claude Code
```

> **Re-initializing?** Use `lenspr init . --force` or delete the `.lens/` folder first.

### Step 3: Restart VSCode

Close VSCode completely (Cmd+Q / Alt+F4) and reopen your project.
Claude Code will now have access to `lens_*` tools.

### Step 4: Use it

**In Claude Code** — just ask:
- "What does my_function do?"
- "What calls validate_user?"
- "Check impact of changing Settings class"

**From CLI:**
```bash
lenspr status .                 # Show graph stats
lenspr search . "validate"      # Find functions by name
lenspr impact . my.function     # Check what breaks if you change it
```

## How It Works

```
Source Files (.py)
       ↓
   AST Parser (Python ast + jedi)
       ↓
   SQLite Graph (nodes + edges)
       ↓
   Tools (29 MCP tools for Claude)
       ↓
   Safe Modifications (3-level validation)
```

LensPR parses Python into a directed graph:
- **Nodes** = functions, classes, methods, modules
- **Edges** = calls, imports, inheritance, uses

Claude gets structured tools to navigate and modify code safely.

## Key Features

| Feature | Description |
|---------|-------------|
| **Impact Analysis** | Know what breaks before you change anything |
| **29 MCP Tools** | Navigation, search, analysis, modification, git integration |
| **3-Level Validation** | Syntax → Structure → Signature checks |
| **Auto-Sync** | Graph updates automatically when files change |
| **Semantic Annotations** | Hybrid approach: Claude writes summaries, patterns detect roles |
| **Git Integration** | Blame, history, commit scope analysis |

## All CLI Commands

```bash
lenspr init <path>              # Build the code graph
lenspr setup <path>             # Create .mcp.json for Claude Code
lenspr status <path>            # Show graph stats (nodes, edges, confidence)
lenspr search <path> "query"    # Search functions/classes by name
lenspr impact <path> <node_id>  # Check what breaks if you change a node
lenspr sync <path>              # Resync after file changes
lenspr serve <path>             # Start MCP server manually
lenspr watch <path>             # Auto-sync on file changes
lenspr annotate <path>          # Show annotation coverage
```

## Python API

```python
import lenspr

# Initialize
lenspr.init("./my_project")

# Get full context in one call
context = lenspr.handle_tool("lens_context", {
    "node_id": "app.utils.validate_email"
})
# Returns: source + callers + callees + tests

# Check what breaks before changing
impact = lenspr.check_impact("app.models.User")
# Returns: severity, affected nodes, test coverage

# Search by name or code content
results = lenspr.handle_tool("lens_search", {
    "query": "validate",
    "search_in": "name"
})
```

## MCP Tools Overview

### Navigation & Search
| Tool | Description |
|------|-------------|
| `lens_context` | **Best tool** — source + callers + callees + tests in one call |
| `lens_get_node` | Get full source code of a node |
| `lens_search` | Search by name, code content, or docstring |
| `lens_grep` | Regex search with graph context |
| `lens_find_usages` | All callers, importers, inheritors |
| `lens_get_structure` | Project overview with pagination |

### Analysis & Safety
| Tool | Description |
|------|-------------|
| `lens_check_impact` | **Always call before modifying** — shows severity |
| `lens_validate_change` | Dry-run validation without applying |
| `lens_health` | Graph quality: nodes, edges, confidence % |
| `lens_dead_code` | Find unreachable code |
| `lens_dependencies` | External packages used |

### Modification
| Tool | Description |
|------|-------------|
| `lens_update_node` | Update with validation + warnings |
| `lens_add_node` | Add new function/class |
| `lens_delete_node` | Remove a node |
| `lens_rename` | Rename across entire project |
| `lens_batch` | Multiple updates atomically |

### Git Integration
| Tool | Description |
|------|-------------|
| `lens_blame` | Who wrote each line |
| `lens_node_history` | Commits that modified this function |
| `lens_commit_scope` | What a commit affected |
| `lens_recent_changes` | Recently modified nodes |

### Semantic Annotations
| Tool | Description |
|------|-------------|
| `lens_annotate` | Get context for annotation |
| `lens_save_annotation` | Save summary (role auto-detected) |
| `lens_batch_save_annotations` | Annotate multiple nodes at once |
| `lens_annotation_stats` | Coverage statistics |

Full reference: [docs/TOOLS.md](docs/TOOLS.md)

## Auto-Sync

The graph syncs automatically **when MCP server is running**.

| Mode | Auto-Sync | How |
|------|-----------|-----|
| **Claude Code** | ✅ Yes | File watcher syncs on every .py change |
| **CLI commands** | ❌ No | Run `lenspr sync .` manually |
| **`lenspr watch`** | ✅ Yes | Standalone watcher |

## Semantic Annotations

LensPR supports semantic annotations with a **hybrid approach**:
- **Claude** writes `summary` (requires semantic understanding)
- **Patterns** auto-detect `role` and `side_effects` (no hallucination)

### Example Node

```python
# Source code
def validate_email(email: str) -> bool:
    """Check if email is valid."""
    return "@" in email
```

```json
// Stored in graph
{
  "id": "app.utils.validate_email",
  "name": "validate_email",
  "type": "function",
  "file_path": "app/utils.py",
  "source_code": "def validate_email(...)",
  "signature": "def validate_email(email: str) -> bool",

  // Annotation fields
  "summary": "Validates email format by checking for @ symbol",
  "role": "validator",      // Auto-detected from "validate_" prefix
  "side_effects": []        // Auto-detected (none for this function)
}
```

### Available Roles

`validator` | `transformer` | `io` | `orchestrator` | `pure` | `handler` | `test` | `utility` | `factory` | `accessor`

### CLI Commands

```bash
lenspr annotate .                    # Show coverage
lenspr annotate . --auto             # Auto-annotate (role/side_effects only)
lenspr annotate . --auto --force     # Rewrite all annotations
lenspr annotate . --node <node_id>   # Annotate specific node
lenspr annotate . --file <path>      # Annotate all nodes in file
```

### Using Claude Code

For full annotations with summaries:

```
"Annotate my codebase"
```

Claude will call `lens_annotate_batch` → analyze code → call `lens_batch_save_annotations`.

## Architecture

```
lenspr/
├── __init__.py          # Public API
├── models.py            # Data classes (Node, Edge, etc.)
├── context.py           # LensContext — central state
├── database.py          # SQLite operations
├── graph.py             # NetworkX algorithms
├── patcher.py           # File patching
├── validator.py         # 3-level validation
├── mcp_server.py        # MCP server (29 tools)
├── cli.py               # CLI entry point
├── parsers/
│   ├── base.py          # BaseParser interface
│   └── python_parser.py # Python AST + jedi
└── tools/
    ├── navigation.py    # Search, list, context
    ├── analysis.py      # Impact, health, dead code
    ├── modification.py  # Update, add, delete, rename
    ├── annotation.py    # Semantic annotations
    ├── git.py           # Blame, history
    └── patterns.py      # Role/side_effects detection
```

Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Project Status

| Metric | Value |
|--------|-------|
| Tests | 188 passed |
| Graph Confidence | 96% |
| MCP Tools | 29 |
| Python Support | ✅ Yes |
| JS/TS Support | ❌ Not yet (help wanted!) |

<details>
<summary>Detailed benchmark results</summary>

![Task Completion](eval/results/chart_success.png)
![Token Usage](eval/results/chart_tokens.png)

| Task | Without | With | Status |
|------|---------|------|--------|
| Understand Function | 602K tokens | 131K tokens | Both passed |
| Find All Usages | 623K tokens | 137K tokens | With: passed, Without: failed |
| Safe Code Change | Rate limit | 121K tokens | With: passed, Without: failed |

Run yourself: `make benchmark`

</details>

## Known Limitations

- **Python only** — JS/TS/Go/Rust parsers not implemented yet
- **Dynamic code** — `getattr`, `eval()` can't be fully tracked
- **Large projects** — not tested on >10k files
- **Alpha stage** — expect rough edges

## Contributing

I especially welcome:
- **JS/TS parser** — `BaseParser` interface is ready
- **Bug reports** — even "this doesn't work" is helpful
- **Ideas** — what would make this useful for you?

## Installation Options

```bash
pip install lenspr           # Core only
pip install 'lenspr[mcp]'    # + MCP server for Claude
pip install 'lenspr[dev]'    # + dev tools
```

## License

MIT

---

**Questions?** [Open an issue](https://github.com/kyrylopr/lenspr/issues)
