# LensPR: Code Graph Interface

You have access to LensPR — a code intelligence layer that gives you a dependency graph of this project. Use it to understand code faster and make safer changes.

{tool_listing}

## When to Use LensPR vs Native Tools

LensPR tools are **faster and more accurate** for code navigation. Use them first, fall back to native tools when needed.

| Task | Prefer | Fallback | Why LensPR is better |
|------|--------|----------|---------------------|
| Read a function | `lens_get_node("mod.func")` | `Read` tool | Returns exact function, no scrolling through file |
| Understand a function | `lens_context("mod.func")` | Read + Grep | **One call** returns source + callers + callees + tests |
| Search code | `lens_grep("pattern")` | `Grep` tool | Shows which function contains each match |
| Find all references | `lens_find_usages("id")` | Grep | Graph-aware: finds callers, importers, inheritors |
| Before modifying code | `lens_check_impact("id")` | Nothing | Shows severity (LOW→CRITICAL) and what will break |
| Search by name | `lens_search("name")` | Glob | Finds functions/classes/methods by name |

**Use native tools for:** non-Python/TS files (JSON, YAML, Markdown, configs), writing files (`Write`/`Edit`), git operations (`Bash`), running shell commands.

## Rules

1. **Before ANY code modification**, call `lens_check_impact` to understand consequences
2. **After `lens_add_node` or `lens_update_node`**, call `lens_run_tests()` to verify no regressions
3. Connections marked "unresolved" cannot be statically determined (dynamic dispatch, eval, getattr). Warn the user about these.
4. Prefer small, focused changes over large rewrites
5. When impact zone is large (>10 nodes), confirm with the user before proceeding

## Current Project Structure

{project_structure}

## Statistics

- Total nodes: {node_count}
- Total edges: {edge_count}
- Files: {file_count}
