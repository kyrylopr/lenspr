# LensPR Tools Reference

LensPR provides 27 tools for code navigation, analysis, and modification.

## Navigation & Discovery

| Tool | Description |
|------|-------------|
| `lens_list_nodes` | List all functions/classes/modules with type/file/name filters |
| `lens_get_node` | Get full source code of a specific node |
| `lens_get_connections` | See what calls a node and what it calls |
| `lens_context` | **One call = source + callers + callees + tests + annotations** |
| `lens_search` | Search nodes by name, code content, or docstring |
| `lens_grep` | Text/regex search with graph context (which function contains each match) |
| `lens_get_structure` | Compact project overview with pagination (summary mode for large projects) |
| `lens_find_usages` | Find all callers, importers, and inheritors of a node |

## Analysis & Safety

| Tool | Description |
|------|-------------|
| `lens_check_impact` | **Always call before modifying** - shows severity (CRITICAL/HIGH/MEDIUM/LOW) |
| `lens_validate_change` | Dry-run validation: check what would happen without applying |
| `lens_health` | Graph quality report: nodes, edges, confidence %, docstring coverage |
| `lens_diff` | Show what changed since last sync (added/modified/deleted files) |
| `lens_dead_code` | Find unreachable code from entry points |
| `lens_dependencies` | List all external dependencies (stdlib, third-party) |

## Modification

| Tool | Description |
|------|-------------|
| `lens_update_node` | Update node source with 3-level validation + proactive warnings |
| `lens_add_node` | Add new function/class to a file |
| `lens_delete_node` | Remove a node from the codebase |
| `lens_rename` | Rename a function/class across the entire project |
| `lens_batch` | Apply multiple updates atomically (all-or-nothing) |

**Proactive Warnings in `lens_update_node`:**
- HIGH IMPACT: >10 callers affected
- NO TESTS: No test coverage for this node
- CIRCULAR: Part of a circular import

## Semantic Annotations

| Tool | Description |
|------|-------------|
| `lens_annotate` | Generate suggested role, side_effects from code analysis |
| `lens_save_annotation` | Save semantic annotations (summary, role, side_effects, inputs, outputs) |
| `lens_annotate_batch` | Get nodes needing annotation (unannotated or stale) |
| `lens_annotation_stats` | Coverage stats: annotated %, breakdown by type and role |

**Node Roles:** `validator`, `transformer`, `io`, `orchestrator`, `pure`, `handler`, `test`, `utility`, `factory`, `accessor`

## Git Integration

| Tool | Description |
|------|-------------|
| `lens_blame` | Git blame for a node's source lines (who wrote what, when) |
| `lens_node_history` | Commit history for a specific node (line-level tracking) |
| `lens_commit_scope` | What nodes were affected by a specific commit |
| `lens_recent_changes` | Recently modified nodes from git log |

## Usage Examples

### Get full context for a function

```python
result = lenspr.handle_tool("lens_context", {
    "node_id": "app.utils.validate_email",
    "include_callers": True,
    "include_callees": True,
    "include_tests": True
})
```

### Check impact before modification

```python
result = lenspr.handle_tool("lens_check_impact", {
    "node_id": "app.models.User",
    "depth": 3
})
# Returns: severity, direct_callers, indirect_callers, affected_count
```

### Search for code patterns

```python
# By name
result = lenspr.handle_tool("lens_search", {"query": "validate", "search_in": "name"})

# By code content (with regex)
result = lenspr.handle_tool("lens_grep", {"pattern": "raise.*Error", "file_glob": "*.py"})
```

### Safe code update

```python
# 1. Check impact first
impact = lenspr.handle_tool("lens_check_impact", {"node_id": "my.function"})

# 2. Validate the change
validation = lenspr.handle_tool("lens_validate_change", {
    "node_id": "my.function",
    "new_source": "def my_function(x, y):\n    return x + y"
})

# 3. Apply if valid
if validation["success"]:
    result = lenspr.handle_tool("lens_update_node", {
        "node_id": "my.function",
        "new_source": "def my_function(x, y):\n    return x + y"
    })
```
