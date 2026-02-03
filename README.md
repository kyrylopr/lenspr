# LensPR

**Code-as-graph for safe LLM-assisted development.**

LensPR parses your Python codebase into a graph (nodes = functions, classes, modules; edges = calls, imports, inheritance) and gives LLMs structured tools to navigate, analyze impact, and safely modify code.

## The Problem

LLMs see code as text. They don't know that the function on line 50 is called from three places in other files. Changes happen without understanding consequences. Bugs surface later.

## The Solution

```
Source Files → AST Parser → Graph (SQLite + NetworkX) → Claude Tools
```

LensPR provides:
- **Impact analysis** before every change — know what breaks before it breaks
- **Structured navigation** — LLMs explore code through graph queries, not file reads
- **Safe patching** — validated changes applied to original files, no regeneration
- **Confidence scoring** — explicit about what the graph can and cannot see

## Quick Start

```bash
pip install lenspr
```

```python
import lenspr

# Initialize on your project
lenspr.init("./my_project")

# Get tools for Claude API
tools = lenspr.get_claude_tools()
prompt = lenspr.get_system_prompt()

# Or use directly
nodes = lenspr.list_nodes(type="function")
impact = lenspr.check_impact("app.models.User")
```

## With Claude API

```python
import anthropic
import lenspr

# Initialize
ctx = lenspr.init("./my_project")
tools = lenspr.get_claude_tools()
system_prompt = lenspr.get_system_prompt()

# Create Claude client
client = anthropic.Anthropic()

# Claude can now use lens_* tools to navigate and modify your code
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

## Available Tools

| Tool | Description |
|------|-------------|
| `lens_list_nodes` | List all functions, classes, modules with filters |
| `lens_get_node` | Get full source code of a specific node |
| `lens_get_connections` | See what calls a node and what it calls |
| `lens_check_impact` | **Always call before modifying** — shows affected nodes |
| `lens_update_node` | Update node source with 3-level validation |
| `lens_add_node` | Add new function/class to a file |
| `lens_delete_node` | Remove a node from the codebase |
| `lens_rename` | Rename across the entire project |
| `lens_search` | Search nodes by name, code, or docstring |
| `lens_get_structure` | Compact project overview |

## Architecture

```
Source Files (always source of truth)
     │
     ▼
Parser (ast + jedi) ──→ SQLite (graph.db, history.db)
                              │
                              ▼
                        NetworkX (lazy cache for graph algorithms)
                              │
                              ▼
                        Tools (Claude API / MCP / Python API)
                              │
                              ▼
                        Patcher (line-based replace, bottom-to-top)
                              │
                              ▼
                        Validator (syntax → structure → signature)
```

Key design decisions:
- **Patcher, not generator** — files are patched in place, never regenerated
- **SQLite is the single source of truth** — NetworkX is a read-only cache
- **Pluggable parsers** — `BaseParser` interface ready for JS/TS/Go
- **Confidence scoring** — edges marked as resolved, inferred, or unresolved
- **Batch patching** — multiple changes to one file applied bottom-to-top

## Development

```bash
git clone https://github.com/kyrylopr/lenspr.git
cd lenspr
python3 -m venv .venv
source .venv/bin/activate
make dev
make test
```

## Project Structure

```
lenspr/
├── lenspr/
│   ├── __init__.py          # Public API
│   ├── models.py            # Data classes (Node, Edge, Change, etc.)
│   ├── context.py           # LensContext — central state manager
│   ├── database.py          # SQLite operations
│   ├── graph.py             # NetworkX graph algorithms
│   ├── patcher.py           # File patching (no regeneration)
│   ├── validator.py         # 3-level validation
│   ├── tracker.py           # Change history
│   ├── claude_tools.py      # Tool definitions + handlers
│   ├── parsers/
│   │   ├── base.py          # BaseParser interface
│   │   └── python_parser.py # Python AST + jedi parser
│   ├── plugins/
│   │   └── (future: pytest tracer, runtime sampler)
│   └── prompts/
│       └── system.md        # Claude system prompt template
├── tests/
├── Makefile
├── pyproject.toml
└── README.md
```

## License

MIT — see [LICENSE](LICENSE).
