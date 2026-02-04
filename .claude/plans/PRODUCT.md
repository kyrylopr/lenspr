# LensPR: Product Overview

## What It Is

LensPR is a **code-as-graph** interface for Claude. It transforms source code into a navigable graph of nodes (functions, classes, modules) and edges (calls, imports, inheritance), accessible via MCP tools.

**Philosophy:** LensPR is a **data provider**, not a decision maker. It provides raw metrics and structural information — Claude decides what they mean.

---

## Core Value Proposition

| Without LensPR | With LensPR |
|----------------|-------------|
| Read entire file to find one function | `lens_get_node` returns exact code |
| Grep callers + Grep tests + read files | `lens_context` returns everything in one call |
| Edit code, hope nothing breaks | `lens_check_impact` shows severity before change |
| Manual search for dead code | `lens_dead_code` finds unreachable functions |
| Read file, count methods manually | `lens_class_metrics` returns pre-computed metrics |

---

## Strengths

### 1. Semantic Understanding
LensPR understands code **semantically**, not just as text:
- Knows `foo.bar()` calls method `bar` on class `Foo`
- Tracks inheritance, imports, nested classes
- Distinguishes between `user` variable and `User` class

### 2. Impact Analysis
`lens_check_impact` answers: "What breaks if I change this?"
- Returns severity: CRITICAL / HIGH / MEDIUM / LOW
- Shows affected nodes: direct callers, transitive dependents, tests
- Prevents "I changed one function and broke 50 things"

### 3. Atomic Modifications
`lens_update_node` edits a single function/class:
- 3-level validation: syntax → AST structure → imports
- Auto-updates graph after change
- No risk of corrupting surrounding code

### 4. Auto-Sync
File watcher keeps graph up-to-date:
- No manual "refresh" needed
- Every tool call sees current state
- Works with IDE saves, git operations

### 5. Pre-Computed Metrics
Metrics computed during `init/sync`, not at query time:
- Class metrics: method count, lines, public/private, dependencies
- Project metrics: avg/median/p90/p95 methods per class
- Percentile ranking: "This class is in the 95th percentile"
- O(1) reads, no computation on query

### 6. Git Integration
- `lens_blame`: Who wrote each line of a function
- `lens_node_history`: Commits that touched this function
- `lens_recent_changes`: What changed in last N commits
- `lens_commit_scope`: What a specific commit affected

---

## Weaknesses

### 1. Python + TypeScript Only
- Python: Full support via AST + jedi
- TypeScript: Good support via tree-sitter
- JavaScript: ~60-70% edge resolution (no types)
- Other languages: Not supported

### 2. No Runtime Understanding
Static analysis only:
- `eval()`, `exec()` — invisible
- `getattr(obj, "method_" + name)` — unresolved
- Dynamic imports — unresolved
- Edges marked "unresolved" for these cases

### 3. Large Files = Many Tokens
`lens_get_node` for 500-line function = 500 lines of context.
No summarization, no chunking.

### 4. Class-Only Metrics
Currently metrics only for classes:
- No function-level complexity metrics
- No module-level metrics
- No cross-module coupling analysis

### 5. No Visualization
Graph exists in database, but:
- No visual explorer
- No dependency diagrams
- CLI and MCP only

---

## Roadmap

### Priority 1: Runtime Understanding (HIGH)
Improve detection of dynamic patterns:
- Pattern matching for common `getattr` idioms
- Decorator analysis (`@route`, `@property`, etc.)
- Factory pattern detection
- Mark confidence level on edges

### Priority 2: Function/Module Metrics (HIGH)
Extend metrics beyond classes:
- Function complexity (cyclomatic, cognitive)
- Module cohesion scores
- Cross-module coupling analysis
- Hotspot detection (high complexity + high change frequency)

### Priority 3: Large File Handling (MEDIUM)
Reduce token usage for large code:
- Signature-only mode for initial exploration
- Configurable truncation
- Summary generation for large functions

### Priority 4: TypeScript Path Resolution (MEDIUM)
Improve JS/TS resolution:
- Better tsconfig.json path alias support
- Monorepo cross-package imports
- Framework-specific patterns (Next.js, etc.)

### Priority 5: Visualization (LOW)
Add visual exploration:
- Dependency graph export (DOT, Mermaid)
- Interactive web viewer
- IDE integration

---

## Architecture

```
lenspr/
├── parsers/          # Python AST + tree-sitter (TS/JS)
│   ├── python.py     # jedi-based Python parser
│   ├── typescript.py # tree-sitter TypeScript
│   └── multi.py      # Language detection, coordination
├── tools/            # MCP tool handlers
│   ├── navigation.py # get_node, search, context
│   ├── modification.py # update, add, delete nodes
│   ├── analysis.py   # impact, dead_code, health
│   ├── arch.py       # class/project metrics
│   └── git.py        # blame, history, recent_changes
├── architecture.py   # Metrics computation
├── database.py       # SQLite graph storage
├── context.py        # Graph lifecycle, file watcher
├── validator.py      # 3-level code validation
└── mcp_server.py     # MCP server (27 tools)
```

### Data Flow
```
init/sync → parse files → build graph → compute metrics → store in DB
query → read from DB (O(1)) → return to Claude
modify → validate → apply → re-sync affected nodes
```

---

## Key Commands

```bash
lenspr init              # Initialize graph for project
lenspr init --force      # Rebuild from scratch
lenspr doctor            # Check environment and graph health
lenspr architecture .    # Show class metrics
lenspr architecture . --largest 10    # Top 10 largest classes
lenspr architecture . --explain Class # Detailed class analysis
```

---

## MCP Tools Summary

| Category | Tools |
|----------|-------|
| Navigation | `lens_get_node`, `lens_context`, `lens_search`, `lens_grep`, `lens_list_nodes`, `lens_get_structure` |
| Modification | `lens_update_node`, `lens_add_node`, `lens_delete_node`, `lens_rename`, `lens_batch` |
| Analysis | `lens_check_impact`, `lens_validate_change`, `lens_dead_code`, `lens_find_usages`, `lens_health` |
| Metrics | `lens_class_metrics`, `lens_project_metrics`, `lens_largest_classes`, `lens_compare_classes`, `lens_components` |
| Git | `lens_blame`, `lens_node_history`, `lens_commit_scope`, `lens_recent_changes` |
| Annotation | `lens_annotate`, `lens_save_annotation`, `lens_batch_save_annotations`, `lens_annotation_stats` |

---

## Success Metrics

How to know if LensPR is helping:
1. **Context usage** — fewer Read/Grep calls, more lens_* calls
2. **Impact awareness** — check_impact called before modifications
3. **Refactoring confidence** — changes don't break unexpected code
4. **Class quality** — tracking largest classes over time, reducing God Classes
