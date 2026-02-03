# LensPR Architecture

## Data Flow

```
Source Files (always source of truth)
     |
     v
Parser (ast + jedi) --> SQLite (graph.db, history.db, resolve_cache.db)
                              |
                              v
                        NetworkX (lazy cache for graph algorithms)
                              |
                              v
                        Tools (CLI / MCP Server / Python API / Claude API)
                              |
                              v
                        Patcher (line-based replace, bottom-to-top)
                              |
                              v
                        Validator (syntax -> structure -> signature)
```

## Key Design Decisions

### Patcher, not generator
Files are patched in place, never regenerated. Only the changed lines are touched. This preserves formatting, comments, and makes diffs minimal.

### SQLite is the single source of truth
NetworkX graph is a read-only cache rebuilt on demand. This ensures consistency and allows for efficient queries.

### 3-level validation
Every code change is checked for:
1. **Syntax** - valid Python AST
2. **Structure** - function stays function, class stays class
3. **Signature** - parameter compatibility with callers

### Bottom-to-top patching
Multiple patches in one file are applied from the end upward to avoid line number corruption.

### Pluggable parsers
`BaseParser` interface ready for JS/TS/Go/Rust parsers. Currently only Python is implemented.

### Confidence scoring
Edges are marked as:
- `resolved` - jedi confirmed the target
- `inferred` - AST-based, likely correct
- `unresolved` - dynamic dispatch, can't be sure

### Change tracking
Every modification recorded in `history.db` with old/new source and affected nodes list.

## Project Structure

```
lenspr/
├── lenspr/
│   ├── __init__.py            # Public API (init, sync, list_nodes, check_impact, etc.)
│   ├── models.py              # Data classes (Node, Edge, Change, Patch, etc.)
│   ├── context.py             # LensContext - central state manager
│   ├── database.py            # SQLite operations (3 databases)
│   ├── graph.py               # NetworkX graph algorithms (impact, dead code, cycles)
│   ├── patcher.py             # File patching (PatchBuffer, bottom-to-top apply)
│   ├── validator.py           # 3-level validation (syntax -> structure -> signature)
│   ├── tracker.py             # Change history and rollback
│   ├── claude_tools.py        # Tool definitions + handlers for Claude API
│   ├── cli.py                 # CLI entry point
│   ├── mcp_server.py          # MCP server (FastMCP, stdio transport)
│   ├── parsers/
│   │   ├── base.py            # BaseParser interface
│   │   └── python_parser.py   # Python AST + jedi parser
│   ├── plugins/               # Future: pytest tracer, runtime sampler
│   └── prompts/
│       └── system.md          # Claude system prompt template
├── tests/                     # pytest suite
├── eval/                      # Benchmark notebooks and results
└── docs/                      # Documentation
```

## Databases

### graph.db
Main graph storage: nodes and edges tables.

### history.db
Change tracking: every modification with old/new source.

### resolve_cache.db
Jedi resolution cache: speeds up repeated parsing.

## MCP Server

The MCP server (`lenspr serve`) provides all 27 tools to Claude Code/Desktop via stdio transport.

Features:
- Auto-sync on file changes (via watchdog)
- Hot reload in dev mode
- Graceful error handling

## Adding a New Parser

1. Create `lenspr/parsers/typescript_parser.py`
2. Implement `BaseParser` interface:
   - `parse_file(path) -> list[Node]`
   - `extract_edges(nodes) -> list[Edge]`
3. Register in `lenspr/parsers/__init__.py`
4. Add file extension mapping
