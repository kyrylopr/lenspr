# LensPR Project Instructions

## ⛔ ABSOLUTE REQUIREMENTS — NO EXCEPTIONS

**STOP. Before ANY action on Python files (.py), you MUST use lenspr tools FIRST.**

This includes:
- Editing `.py` files → `lens_check_impact` FIRST
- Deleting `.py` files → `lens_search` + `lens_find_usages` FIRST
- Moving/renaming `.py` files → `lens_find_usages` FIRST
- Reading `.py` files → `lens_get_node` or `lens_context` instead of `Read`
- Searching in `.py` files → `lens_grep` or `lens_search` instead of `Grep`

**VIOLATION = IMMEDIATE STOP.** If you catch yourself using Bash `rm`, `mv` on .py files, or using `Grep`/`Read` on Python code — STOP and use lenspr instead.

### Mandatory Checklist Before Python File Operations:

```
□ Is this a .py file? → USE LENSPR
□ Am I deleting? → lens_search (is it in graph?) + lens_find_usages (who uses it?)
□ Am I editing? → lens_check_impact (what breaks?)
□ Am I reading? → lens_get_node or lens_context (not Read)
□ Am I searching? → lens_grep or lens_search (not Grep)
```

### Mandatory Checklist After Every Code Change:

```
□ After lens_add_node or lens_update_node → run lens_run_tests()
□ Syntax validation ≠ behavioral correctness
□ Do not proceed to the next change until tests pass
□ For NEW functions: also run lens_generate_test_skeleton(node_id) and write tests
```

**What `lens_run_tests()` actually catches:** import-time crashes (NameError, missing symbols, broken references between modules). Python resolves imports at load time — `pytest` will fail at collection if the new code references a non-existent function.

**What `lens_run_tests()` does NOT catch:** behavioral bugs in the new code itself. Logic errors, wrong return values, unhandled edge cases — none of this is verified without tests that specifically call the new function.

**Full verification loop for new code:**
1. `lens_run_tests()` → no import crashes, no regressions in already-covered code
2. `lens_generate_test_skeleton(node_id)` → test spec (scenarios, mocks, usage examples)
3. Write tests based on the spec → behavioral correctness confirmed

### Why This Is Non-Negotiable:

You have a **proven pattern** of:
1. Using lenspr once, then forgetting it exists
2. Falling back to Bash/Grep/Read out of habit
3. Deleting files without checking dependencies
4. Making changes without impact analysis

**This causes bugs that lenspr was built to prevent.**

### Specific Violations To Avoid:

❌ **WRONG:** `Read("/Users/.../lenspr/cli.py")` to read Python code
✅ **RIGHT:** `lens_get_node("lenspr.cli.cmd_init")` or `lens_context("lenspr.cli")`

❌ **WRONG:** `Grep(pattern="results_v2", path="lenspr/")` to search Python
✅ **RIGHT:** `lens_grep("results_v2")` or `lens_search("results_v2", search_in="code")`

❌ **WRONG:** Reading full .py file with `Read` then manually finding a function
✅ **RIGHT:** `lens_get_node("module.function")` returns exact code

❌ **WRONG:** "Let me verify the file imports correctly" using `python -c "import ..."`
✅ **RIGHT:** Trust lenspr — if you edited via `lens_update_node`, it validated syntax

❌ **WRONG:** `Bash("rm -f some_file.py")` without checking graph
✅ **RIGHT:** `lens_search` + `lens_find_usages` FIRST, then delete

---

## CRITICAL: READ CODE VS RUN CODE

**If you have read 3+ files in a row and everything "looks correct" — STOP. Run the code.**

LensPR tools are convenient for reading code but cannot execute it. When debugging runtime behavior (what edges get created, what jedi resolves, what a function actually returns), **running code is 30 seconds, reading code is 25 tool calls**.

### The Anti-Pattern (documented from real session, 2026-02-18):

**Symptom:** Debugging why `from lenspr import database` creates no graph edges.
**Wrong approach:** Read `_extract_calls` → looks correct. Read `_ImportTable.resolve` → looks correct. Read `_resolve_edges_with_jedi` → looks correct. Read `normalize_edge_targets` → looks correct. Read `save_graph` → looks correct. (25 tool calls, ~25 minutes, no answer)
**Right approach:** Run the parser directly:
```python
python3 -c "
from lenspr.parsers.python_parser import CodeGraphVisitor
import ast
source = 'from lenspr import database\ndef f():\n    database.save_graph(x)'
visitor = CodeGraphVisitor(source.splitlines(), 'test', 'test.py')
visitor.visit(ast.parse(source))
for e in visitor.edges: print(e.from_node, '->', e.to_node, e.confidence.value)
"
```
Result in 30 seconds: edges ARE created. Bug is not in parser. Move to next layer.

### Decision Rule:

```
□ Debugging runtime behavior?       → python3 -c first, THEN read code if needed
□ Third file looks "correct"?        → STOP. Run the thing.
□ "jedi should return X"?            → script.goto() in python3 -c to verify
□ "edge should be created"?          → parse a minimal example, check visitor.edges
□ "save_graph should save it"?       → run parse_project() + inspect edges list
```

**Root cause:** LensPR is the primary tool → agent stays inside LensPR → LensPR can't execute code → agent reads instead of runs. Bash is available and faster for verification. Use it.

---

## TRUST THE GRAPH

The MCP server has a **file watcher that auto-syncs** the graph before every tool call.

**DO:**
- Call `lens_search` and trust the result
- Call `lens_check_impact` and trust the result
- Assume the graph is up-to-date

**DON'T:**
- Call `lens_diff` "just to check" if graph is synced — it's automatic
- Double-check lenspr results with Grep/Read — trust the graph
- Say "the graph might not be synced" — it is synced
- Use `Read` on .py files "to see the full file" — use `lens_get_node` for specific code

If lenspr says a function doesn't exist, it doesn't exist. Trust it.

**Exception:** Use `Read` on .py files ONLY when you need to rewrite the ENTIRE file with `Write`. Otherwise, always use lenspr.

---

## CRITICAL: MANDATORY RULES FOR CODE CHANGES

**BLOCKING REQUIREMENTS — violations will cause bugs:**

1. **BEFORE modifying ANY Python code**, you MUST call `lens_check_impact("node_id")`. No exceptions.
2. **NEVER use Edit on .py files** without first calling `lens_check_impact`.
3. **NEVER delete .py files** without first calling `lens_search` + `lens_find_usages`.
4. **NEVER use Grep on Python code** — use `lens_grep` instead.
5. **NEVER use Read on Python code** — use `lens_get_node` or `lens_context` instead.
6. If `lens_check_impact` returns severity CRITICAL or HIGH — warn the user and wait for confirmation.

---

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
make annotations  # Show annotation coverage
make publish      # Build and publish to PyPI
```
