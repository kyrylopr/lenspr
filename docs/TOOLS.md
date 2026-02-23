# LensPR Tools Reference

LensPR provides 60+ MCP tools organized in 12 groups. All groups are enabled by default — disable unneeded groups with `lenspr tools` to save context window space.

---

## Navigation & Search (8 tools)

| Tool | Description |
|------|-------------|
| `lens_context` | **One call = source + callers + callees + tests.** Primary tool for understanding any function. |
| `lens_get_node` | Get full source code of a specific node by ID |
| `lens_search` | Search nodes by name, code content, or docstring |
| `lens_grep` | Regex search with graph context — shows which function contains each match |
| `lens_find_usages` | All callers, importers, and inheritors of a node (batch mode supported) |
| `lens_get_structure` | Compact project overview with pagination (summary/full/compact modes) |
| `lens_list_nodes` | List all nodes with type/file/name filters |
| `lens_get_connections` | Direct callers and callees for a node |

## Modification (6 tools)

| Tool | Description |
|------|-------------|
| `lens_update_node` | Replace full node source with syntax validation + proactive warnings |
| `lens_patch_node` | Surgical find/replace within a node — safer for small changes |
| `lens_add_node` | Add new function or class to a file |
| `lens_delete_node` | Remove a node from the codebase |
| `lens_rename` | Rename a function/class/method across the entire project |
| `lens_batch` | Atomic multi-node updates — all apply or all roll back |

**Proactive warnings on update:**
- HIGH IMPACT: >10 callers affected
- NO TESTS: no test coverage for this node
- ARCHITECTURE: violation of defined rules

## Analysis (7 tools)

| Tool | Description |
|------|-------------|
| `lens_check_impact` | **Always call before modifying.** Shows severity (CRITICAL/HIGH/MEDIUM/LOW) and affected nodes. |
| `lens_validate_change` | Dry-run: validate syntax and structure without applying |
| `lens_health` | Graph quality: nodes/edges, confidence %, docstrings, circular imports |
| `lens_dead_code` | Find unreachable code (auto-detects Django, FastAPI, Celery, CLI entry points) |
| `lens_find_usages` | All callers, importers, string references (batch mode) |
| `lens_dependencies` | External packages used, grouped by package or file |
| `lens_diff` | Show what changed since last sync (added/modified/deleted files) |

## Quality / Vibecoding Safety (8 tools)

| Tool | Description |
|------|-------------|
| `lens_vibecheck` | 0–100 health score (grade A–F) across 6 dimensions |
| `lens_nfr_check` | Flag missing error handling, hardcoded secrets, missing auth per function |
| `lens_test_coverage` | Runtime (pytest-cov) + graph-based coverage report |
| `lens_security_scan` | Bandit security scanner, results mapped to graph nodes |
| `lens_dep_audit` | Check dependencies for known CVEs (pip-audit / npm audit) |
| `lens_fix_plan` | Prioritized remediation plan (CRITICAL→LOW) to improve health score |
| `lens_generate_test_skeleton` | Test spec with scenarios, mock candidates, and real usage examples |
| `lens_run_tests` | Run pytest with structured results and auto-tracing |

## Architecture Rules & Metrics (9 tools)

| Tool | Description |
|------|-------------|
| `lens_arch_rule_add` | Define a rule enforced on every code change |
| `lens_arch_rule_list` | List all defined rules with config |
| `lens_arch_rule_delete` | Remove a rule by ID |
| `lens_arch_check` | Check all rules against the current codebase |
| `lens_class_metrics` | Pre-computed class metrics (methods, lines, percentile rank) |
| `lens_project_metrics` | Project-wide class statistics (avg/median/p90/p95) |
| `lens_largest_classes` | Classes sorted by method count (descending) |
| `lens_compare_classes` | Side-by-side metrics comparison of multiple classes |
| `lens_components` | Directory-based component cohesion analysis |

**Rule types:**
- `no_dependency` — forbid calls between layers (e.g., parsers → tools)
- `max_class_methods` — cap class size (e.g., max 20 methods)
- `required_test` — every function matching pattern must have a test
- `no_circular_imports` — forbid circular imports

## Git Integration (4 tools)

| Tool | Description |
|------|-------------|
| `lens_blame` | Who wrote each line of a function |
| `lens_node_history` | Commits that modified a specific function |
| `lens_commit_scope` | What nodes a specific commit affected |
| `lens_recent_changes` | Recently modified nodes from git history |

## Cross-Language & Infrastructure (5 tools)

| Tool | Description |
|------|-------------|
| `lens_api_map` | Frontend API calls → backend route handlers (Flask/FastAPI/Express/Fastify/Hono/Koa) |
| `lens_db_map` | Database tables → functions that read/write them (SQLAlchemy/Django/raw SQL) |
| `lens_env_map` | Environment variables: definitions (.env, compose), usages (os.getenv, process.env), undefined vars |
| `lens_ffi_map` | FFI bridges: NAPI, koffi, ffi-napi, WASM between TS/JS and native code |
| `lens_infra_map` | Dockerfiles, CI/CD workflows (GitHub Actions), compose services, secrets |

## Testing & Runtime Tracing (3 tools)

| Tool | Description |
|------|-------------|
| `lens_run_tests` | Run pytest with structured results, auto-coverage |
| `lens_trace` | Run tests with runtime call tracing (Python 3.12+, sys.monitoring, ~5% overhead) |
| `lens_trace_stats` | Static vs runtime edge statistics and confirmation rate |

**Runtime tracing** resolves the #1 graph limitation: `self.method()` dispatch. During test execution, actual caller→callee pairs are observed and merged into the static graph.

## Semantic Annotations (5 tools)

| Tool | Description |
|------|-------------|
| `lens_annotate` | Generate annotation suggestion for a node |
| `lens_save_annotation` | Save summary, role, and side effects to a node |
| `lens_batch_save_annotations` | Annotate many nodes in one call |
| `lens_annotate_batch` | Get nodes needing annotation (unannotated or stale) |
| `lens_annotation_stats` | Coverage stats: annotated %, breakdown by type and role |

**Hybrid approach:** AI provides `summary` (semantic understanding). `role` and `side_effects` are auto-detected from code patterns (no hallucination risk).

**Roles:** validator, transformer, io, orchestrator, pure, handler, test, utility, factory, accessor

## Session Memory (4 tools)

| Tool | Description |
|------|-------------|
| `lens_session_write` | Save a persistent note (survives context resets) |
| `lens_session_read` | Read all session notes to restore context |
| `lens_session_handoff` | Generate handoff doc combining changes + notes |
| `lens_resume` | Restore context from auto-generated action log |

## Temporal Analysis (2 tools)

| Tool | Description |
|------|-------------|
| `lens_hotspots` | Functions that change most frequently (risk indicator) |
| `lens_node_timeline` | Unified timeline: LensPR history (with reasoning) + git commits |

## Explanation (1 tool)

| Tool | Description |
|------|-------------|
| `lens_explain` | Human-readable explanation with callers, callees, usage examples |

---

## Tool Groups

```bash
lenspr tools list                              # Show all groups with status
lenspr tools disable infrastructure tracing    # Disable groups
lenspr tools enable git                        # Enable groups
lenspr tools reset                             # Re-enable all groups
```

| Group | Tools | Description |
|-------|-------|-------------|
| **core** | 7 | Navigation & search (always on) |
| **modification** | 6 | Code changes |
| **analysis** | 7 | Impact analysis |
| **quality** | 8 | Vibecoding safety |
| **architecture** | 9 | Architecture rules & metrics |
| **git** | 4 | Function-level git |
| **annotations** | 5 | Semantic annotations |
| **session** | 4 | Session memory |
| **infrastructure** | 5 | Cross-language mappers |
| **temporal** | 2 | Change hotspots |
| **tracing** | 2 | Runtime call tracing |
| **explain** | 1 | Code explanation |

---

## Usage Examples

### Understand a function (one call)

```
lens_context("auth.login_handler")
→ source, 8 callers, 3 callees, 2 tests
```

### Check impact before modification

```
lens_check_impact("models.User")
→ severity: CRITICAL, 15 direct + 23 indirect dependents
```

### Search with context

```
lens_grep("raise.*Error", file_glob="*.py")
→ utils.py:42: raise ValidationError → inside validate_payment()
```

### Safe code update workflow

```
1. lens_check_impact("my.function")       # Check severity
2. lens_validate_change("my.function", …) # Dry-run
3. lens_update_node("my.function", …)     # Apply
4. lens_run_tests()                        # Verify
```

### Health check

```
lens_vibecheck()
→ score: 86/100 (B)
  test_coverage: 67%, dead_code: 0%, circular_imports: 0
```

### Annotate codebase

```
lens_annotate_batch(limit=20)              # Get unannotated nodes
lens_batch_save_annotations([              # Save summaries
  {"node_id": "app.validate", "summary": "Validates email format"},
  {"node_id": "app.db.save", "summary": "Persists user data"}
])
```
