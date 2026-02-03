# LensPR Project Instructions

## Code Navigation - USE LENSPR TOOLS

This project has LensPR MCP tools available. **ALWAYS prefer lenspr tools over Read/Grep/Glob** for code exploration.

### Primary Tool: lens_context
```
lens_context("module.Class.method")  # Returns: source + callers + callees + tests
```
**Use this first.** One call gives you everything needed to understand a function.

### Finding Code
```
lens_search("function_name")              # Find by name
lens_search("pattern", search_in="code")  # Find in code content
lens_grep("TODO|FIXME")                   # Regex search with graph context
lens_list_nodes(type="function")          # List all functions
lens_get_structure(mode="summary")        # Project overview (compact)
```

### Understanding Dependencies
```
lens_get_connections("node_id")           # Direct callers/callees
lens_find_usages("node_id")               # ALL references (callers + importers + inheritors)
lens_check_impact("node_id", depth=3)     # Full impact zone with severity
```

### Before Modifying Code - REQUIRED
```
lens_check_impact("node_id")              # ALWAYS check first - shows CRITICAL/HIGH/MEDIUM/LOW
lens_validate_change("node_id", code)     # Dry-run: validates without applying
```

### Git Integration
```
lens_blame("node_id")                     # Who wrote each line
lens_node_history("node_id")              # Commits that modified this function
lens_recent_changes(limit=10)             # What changed recently
lens_commit_scope("abc123")               # What a specific commit affected
```

### Code Quality
```
lens_health()                             # Graph confidence %, docstring coverage
lens_dead_code()                          # Find unreachable code
lens_dependencies()                       # External packages used
lens_annotation_stats()                   # Semantic annotation coverage
```

## Workflow Examples

### "What does this function do?"
```
lens_context("module.function")  # Get code + who calls it + what it calls + tests
```

### "I need to change function X"
```
1. lens_check_impact("X")        # Check severity first
2. lens_context("X")             # Understand the function
3. lens_validate_change("X", new_code)  # Test change
4. Edit tool to apply           # Only after validation passes
```

### "Find where errors are handled"
```
lens_grep("except|raise", file_glob="*.py")  # With graph context
```

### "Who wrote this code?"
```
lens_blame("module.function")    # Git blame per line
lens_node_history("module.function")  # Commit history
```

### "Clean up dead code"
```
lens_dead_code()                 # Lists unused functions
lens_find_usages("suspected_dead")  # Verify no callers
```

## Why LensPR Over Traditional Tools?

| Task | Traditional | LensPR | Benefit |
|------|-------------|--------|---------|
| Read function | Read file, find function | `lens_get_node` | Exact code only |
| Understand function | Read + Grep callers + Grep tests | `lens_context` | **One call** |
| Safe refactoring | Hope for the best | `lens_check_impact` | Know severity |
| Find usages | Grep (misses dynamic) | `lens_find_usages` | Graph-aware |
| Code search | Grep | `lens_grep` | Shows containing function |

## When to Use Traditional Tools

- **Write/Edit** - For actually modifying files
- **Bash** - For git commits, running tests, shell commands
- **Read** - For non-Python files (configs, markdown, JSON)

## Project Structure

```
lenspr/
├── parsers/     # Python AST + jedi parser
├── tools/       # Tool handlers (analysis, navigation, modification)
├── mcp_server.py  # MCP server (27 tools)
└── validator.py   # 3-level code validation
tests/           # pytest suite
```

## Development Commands

```bash
make dev          # Install with dev dependencies
make test         # Run tests
make check        # Lint + typecheck + test
make health       # Show graph stats
make publish      # Build and publish to PyPI
```
