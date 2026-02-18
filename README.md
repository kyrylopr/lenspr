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

### 3. Search with context

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
  AST parser (Python) + tree-sitter (TypeScript)
       │
       ▼
  Dependency graph (functions, classes, calls, imports)
       │
       ▼
  SQLite database (local, never leaves your machine)
       │
       ▼
  41 MCP tools for your AI assistant
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

---

## Key Features

| Feature | What it does |
|---------|-------------|
| **Impact Analysis** | Shows severity (LOW → CRITICAL) before any change |
| **One-Call Context** | Source + callers + callees + tests in a single request |
| **Surgical Edits** | `lens_patch_node` for targeted find/replace — no full rewrites needed |
| **Test Runner** | `lens_run_tests` runs pytest and returns structured pass/fail results |
| **Session Memory** | Persistent notes survive context resets — AI picks up where it left off |
| **Graph-Aware Search** | Every grep result shows which function contains the match |
| **Auto-Sync** | Graph updates on every file save, AI always sees latest code |
| **Dead Code Detection** | Finds unreachable functions (supports Django, FastAPI, Celery, etc.) |
| **Git at Function Level** | Blame, history, and commit scope per function, not per file |
| **Atomic Changes** | Multi-file updates either all apply or all roll back |
| **Cross-Project Rename** | Rename a function and update every reference |
| **Large File Safety** | Blocks edits on 10K+ char nodes; integrity check catches truncated LLM output |

---

## Works With

- **Claude Code** (via MCP)
- **Cursor** (via MCP)
- **Any MCP-compatible AI assistant**

### Supported Languages

| Language | Parser | Resolution |
|----------|--------|------------|
| Python | AST + jedi | 96%+ |
| TypeScript / JavaScript | tree-sitter + TS Compiler API | 90%+ |

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
<summary>All MCP tools (41)</summary>

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

### Testing
| Tool | Description |
|------|-------------|
| `lens_run_tests` | Run pytest, return structured pass/fail + failure details |

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

### Session Memory
| Tool | Description |
|------|-------------|
| `lens_session_write` | Save a persistent note by key |
| `lens_session_read` | Read all session notes |
| `lens_session_handoff` | Generate handoff doc for next session |

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

## Known Limitations

- **Dynamic code** (`getattr`, `eval`, dynamic imports) can't be fully tracked
- **Not tested on >10k files** — works well on projects up to ~500 files
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
