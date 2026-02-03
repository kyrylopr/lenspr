# LensPR Project Instructions

## Code Navigation - USE LENSPR TOOLS

This project has LensPR MCP tools available. **ALWAYS prefer lenspr tools over Read/Grep/Glob** for code exploration:

### Finding Code
```
lens_search("function_name")           # Instead of Grep
lens_search("pattern", search_in="code")  # Search in code content
lens_list_nodes(type="function")       # List all functions
lens_get_structure(max_depth=2)        # Project overview
```

### Reading Code with Context
```
lens_get_node("module.ClassName.method")  # Instead of Read for a specific function
lens_context("node_id")                   # Get node + callers + callees + tests
lens_get_connections("node_id")           # What calls this / what it calls
```

### Before Modifying Code
```
lens_check_impact("node_id")           # ALWAYS check before editing
lens_validate_change("node_id", new_source)  # Dry-run validation
lens_find_usages("node_id")            # All references to this node
```

### Searching with Graph Context
```
lens_grep("pattern")                   # Returns matches WITH containing function info
```

### Analysis
```
lens_dead_code()                       # Find unreachable code
lens_health()                          # Graph statistics
lens_dependencies()                    # External dependencies
```

## Why Use LensPR Tools?

1. **Less context** - Get exactly what you need, not entire files
2. **Better understanding** - See callers/callees/tests in one call
3. **Safer changes** - Impact analysis before modification
4. **Graph-aware search** - Know which function contains each match

## When to Use Traditional Tools

- **Write/Edit** - For actually modifying files (lenspr is read-mostly)
- **Bash** - For git, running tests, shell commands
- **Read** - For non-Python files (configs, markdown, etc.)

## Project Structure

- `lenspr/` - Main package
  - `parsers/` - Python AST parser
  - `tools/` - Tool implementations (analysis, navigation, modification)
  - `mcp_server.py` - MCP server for Claude integration
- `tests/` - pytest test suite

## Development Commands

```bash
make dev          # Install with dev dependencies
make test         # Run tests
make check        # Lint + typecheck + test
lenspr init       # Initialize graph
lenspr serve      # Start MCP server
```
