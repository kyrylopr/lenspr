"""Tool schema definitions for Claude API integration."""

from typing import Any

LENS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "lens_list_nodes",
        "description": "List all nodes, optionally filtered by type, file, or name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["function", "class", "module", "method", "block"],
                    "description": "Filter by node type.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Filter by file path.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Filter by name (substring match, "
                        "e.g. 'parse' finds 'parse_file')."
                    ),
                },
            },
        },
    },
    {
        "name": "lens_get_node",
        "description": "Get full details of a specific node including its source code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Node identifier (e.g. 'app.models.User').",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_get_connections",
        "description": (
            "Get all connections (edges) for a node \u2014 "
            "what it calls and what calls it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "direction": {
                    "type": "string",
                    "enum": ["incoming", "outgoing", "both"],
                    "description": "Direction of edges. Default: both.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_check_impact",
        "description": (
            "Analyze what would be affected by changing a node. "
            "ALWAYS call this before modifying any code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {
                    "type": "integer",
                    "description": (
                        "How many levels of dependencies to check. Default: 2."
                    ),
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_update_node",
        "description": "Update the source code of a node. Validates before applying.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "new_source": {
                    "type": "string",
                    "description": "New source code for the node.",
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why this change is being made. "
                        "Stored in history for future sessions to understand context."
                    ),
                },
            },
            "required": ["node_id", "new_source"],
        },
    },
    {
        "name": "lens_patch_node",
        "description": (
            "Surgical find/replace within a node's source code. "
            "Safer than lens_update_node because you only specify the fragment to change, "
            "not the entire function. The old_fragment must appear exactly once in the node. "
            "Use this for targeted fixes: changing a variable name, fixing a condition, "
            "adding/removing a line. Falls back to lens_update_node for the actual write."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "Node identifier (e.g. 'app.models.User.save').",
                },
                "old_fragment": {
                    "type": "string",
                    "description": (
                        "Exact text to find in the node's source. "
                        "Must appear exactly once — include enough surrounding context "
                        "to make it unique if the string appears multiple times."
                    ),
                },
                "new_fragment": {
                    "type": "string",
                    "description": "Replacement text for the matched fragment.",
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why this change is being made. "
                        "Stored in history for future sessions."
                    ),
                },
            },
            "required": ["node_id", "old_fragment", "new_fragment"],
        },
    },
    {
        "name": "lens_validate_change",
        "description": (
            "Dry-run validation: check what would happen if you update a node. "
            "Returns validation result, proactive warnings, and impact analysis "
            "WITHOUT actually applying changes. Use before lens_update_node."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "The node to validate."},
                "new_source": {
                    "type": "string",
                    "description": "Proposed new source code.",
                },
            },
            "required": ["node_id", "new_source"],
        },
    },
    {
        "name": "lens_add_node",
        "description": "Add a new function or class to a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative file path to add the node to.",
                },
                "source_code": {
                    "type": "string",
                    "description": "Source code of the new function/class.",
                },
                "after_node": {
                    "type": "string",
                    "description": (
                        "Node ID to insert after. If omitted, appends to end of file."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why this node is being added. "
                        "Stored in the session log so lens_resume() can reconstruct history."
                    ),
                },
            },
            "required": ["file_path", "source_code"],
        },
    },
    {
        "name": "lens_delete_node",
        "description": "Delete a node from the codebase. Check impact first!",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why this node is being deleted. "
                        "Stored in the session log so lens_resume() can reconstruct history."
                    ),
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_search",
        "description": "Search nodes by name or content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "search_in": {
                    "type": "string",
                    "enum": ["name", "code", "docstring", "all"],
                    "description": "Where to search. Default: all.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "lens_get_structure",
        "description": (
            "Get compact overview of project structure. "
            "Use mode='compact' for large projects (returns totals only, no file list). "
            "Use mode='summary' for medium projects (counts per file)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_depth": {
                    "type": "integer",
                    "description": (
                        "0=files only, 1=with classes/functions, "
                        "2=with methods. Default: 2."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["full", "summary", "compact"],
                    "description": (
                        "full=all details, summary=counts per file, "
                        "compact=totals only (best for large projects). Default: summary."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max files to return. Default: 100.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip first N files (for pagination). Default: 0.",
                },
                "path_prefix": {
                    "type": "string",
                    "description": "Filter to files starting with this path.",
                },
            },
        },
    },
    {
        "name": "lens_rename",
        "description": "Rename a function/class/method across the entire project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["node_id", "new_name"],
        },
    },
    {
        "name": "lens_context",
        "description": (
            "Get full context for a node in one call: source code, callers, callees, "
            "related tests, and imports. Replaces multiple get_node + get_connections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The node identifier (e.g. app.models.User).",
                },
                "include_callers": {
                    "type": "boolean",
                    "description": (
                        "Include nodes that call/use this node. Default: true."
                    ),
                },
                "include_callees": {
                    "type": "boolean",
                    "description": (
                        "Include nodes this node calls/uses. Default: true."
                    ),
                },
                "include_tests": {
                    "type": "boolean",
                    "description": "Include related test functions. Default: true.",
                },
                "depth": {
                    "type": "integer",
                    "description": (
                        "How many levels of callers/callees to include. Default: 1."
                    ),
                },
                "include_source": {
                    "type": "boolean",
                    "description": (
                        "Include full source code for callers/callees/tests. "
                        "When false, returns only signature, file, line. Default: true."
                    ),
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_grep",
        "description": (
            "Search for a text pattern across all project files. Returns matches "
            "with graph context: which function/class contains each match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for.",
                },
                "file_glob": {
                    "type": "string",
                    "description": (
                        "Glob pattern to filter files "
                        "(e.g. '*.py', 'tests/**'). Default: '*.py'."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return. Default: 50.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "lens_diff",
        "description": (
            "Show what changed since last sync without syncing. "
            "Returns lists of added, modified, and deleted files/nodes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lens_batch",
        "description": (
            "Apply multiple node updates atomically with multi-file rollback. "
            "All changes are validated first. If ANY step fails (patching, graph sync, "
            "or optional test verification), ALL files are restored to their pre-batch state. "
            "Set verify_tests=true to run tests before and after — rolls back on regressions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "description": "List of {node_id, new_source} pairs to apply.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "node_id": {"type": "string"},
                            "new_source": {"type": "string"},
                        },
                        "required": ["node_id", "new_source"],
                    },
                },
                "verify_tests": {
                    "type": "boolean",
                    "description": (
                        "Run tests before and after applying. If new failures appear, "
                        "all files are rolled back. Default: false."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds for test runs. Default: 120.",
                },
            },
            "required": ["updates"],
        },
    },
    {
        "name": "lens_health",
        "description": (
            "Get health report for the code graph: total nodes/edges, "
            "edge confidence breakdown, nodes without docstrings, circular imports."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lens_dependencies",
        "description": (
            "List all external dependencies (stdlib and third-party packages) "
            "used by the project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by": {
                    "type": "string",
                    "enum": ["package", "file"],
                    "description": "Group by package name or by file. Default: package.",
                },
            },
        },
    },
    {
        "name": "lens_dead_code",
        "description": (
            "Find potentially dead code: functions/classes not reachable from "
            "entry points. Entry points are auto-detected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Additional entry point node IDs. If empty, auto-detects "
                        "main(), test_*, and CLI/API handlers."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "description": (
                        "Output mode. 'summary' (default): top 15 dead nodes + "
                        "file counts. 'full': complete lists grouped by file."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "Filter dead code to a specific file path. "
                        "Returns full details for that file only."
                    ),
                },
            },
        },
    },
    {
        "name": "lens_find_usages",
        "description": (
            "Find all usages of a node across the codebase. "
            "Returns callers, importers, and string references. "
            "Supports batch mode: pass node_ids (list) to check multiple nodes in one call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The node to find usages of.",
                },
                "node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple nodes to find usages of (batch mode). Overrides node_id.",
                },
                "include_tests": {
                    "type": "boolean",
                    "description": "Include usages from test files. Default: true.",
                },
            },
        },
    },
    # -- Semantic Annotation Tools --
    {
        "name": "lens_annotate",
        "description": (
            "Generate semantic annotations for a node. Returns suggested summary, "
            "role, and detected side effects based on code analysis and context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "The node to annotate."},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_save_annotation",
        "description": "Save semantic annotations to a node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "The node to annotate."},
                "summary": {
                    "type": "string",
                    "description": "Short description of what this node does.",
                },
                "role": {
                    "type": "string",
                    "enum": [
                        "validator", "transformer", "io", "orchestrator",
                        "pure", "handler", "test", "utility", "factory", "accessor"
                    ],
                    "description": "Semantic role of the node.",
                },
                "side_effects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Side effects like 'writes_file', 'network_io'."
                    ),
                },
                "semantic_inputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Semantic types of inputs like 'user_input'.",
                },
                "semantic_outputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Semantic types of outputs like 'validated_data'.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_annotate_batch",
        "description": (
            "Get nodes that need annotation. Returns nodes without annotations "
            "or with stale annotations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type_filter": {
                    "type": "string",
                    "enum": ["function", "method", "class"],
                    "description": "Filter by node type.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Filter by file path prefix.",
                },
                "unannotated_only": {
                    "type": "boolean",
                    "description": "Only return unannotated nodes. Default: true.",
                },
                "stale_only": {
                    "type": "boolean",
                    "description": (
                        "Only return nodes with stale annotations. Default: false."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max nodes to return. Default: 10.",
                },
            },
        },
    },
    {
        "name": "lens_annotation_stats",
        "description": (
            "Get annotation coverage statistics: total annotatable, annotated count, "
            "stale annotations, breakdown by type and role."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # Git integration tools
    {
        "name": "lens_blame",
        "description": (
            "Get git blame information for a node's source lines. "
            "Shows who wrote each line and when."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The node to get blame info for.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_node_history",
        "description": (
            "Get commit history for a specific node. "
            "Shows commits that modified the lines where this node is defined."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The node to get history for.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max commits to return. Default: 10.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_commit_scope",
        "description": (
            "Analyze what nodes were affected by a specific commit. "
            "Shows which functions/classes were modified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commit": {
                    "type": "string",
                    "description": "Commit hash (short or full).",
                },
            },
            "required": ["commit"],
        },
    },
    {
        "name": "lens_recent_changes",
        "description": (
            "Get recently changed nodes based on git history. "
            "Useful for understanding what's been modified recently."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max commits to analyze. Default: 5.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Filter to specific file path.",
                },
            },
        },
    },
    # -- Explanation Tool --
    {
        "name": "lens_explain",
        "description": (
            "Generate a human-readable explanation of what a function/class does. "
            "Provides rich context (callers, callees, usage examples) plus rule-based "
            "analysis. Use this to understand unfamiliar code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The node to explain (e.g. 'app.utils.validate_email').",
                },
                "include_examples": {
                    "type": "boolean",
                    "description": (
                        "Include usage examples from callers. Default: true."
                    ),
                },
            },
            "required": ["node_id"],
        },
    },
    # Batch save annotations
    {
        "name": "lens_batch_save_annotations",
        "description": (
            "Save multiple annotations at once. ONE confirmation for many nodes. "
            "You only need to provide summary for each node. Role and side_effects "
            "are auto-detected from patterns (no hallucination risk)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "annotations": {
                    "type": "array",
                    "description": (
                        "Array of annotation objects, each with node_id and summary."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "node_id": {"type": "string"},
                            "summary": {"type": "string"},
                            "role": {"type": "string"},
                            "side_effects": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["node_id", "summary"],
                    },
                },
            },
            "required": ["annotations"],
        },
    },
    # -- Architecture Metrics Tools (raw data, Claude decides interpretation) --
    {
        "name": "lens_class_metrics",
        "description": (
            "Get pre-computed metrics for a class: method count, lines, "
            "public/private methods, dependencies, internal calls, method prefixes, "
            "and percentile rank compared to other classes. "
            "Metrics are computed during init/sync - this is O(1) read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The class node ID to get metrics for.",
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "lens_project_metrics",
        "description": (
            "Get project-wide class metrics: total classes, avg/median/min/max methods, "
            "and percentiles (p90, p95). Use this to understand the distribution "
            "before interpreting individual class metrics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lens_largest_classes",
        "description": (
            "Get classes sorted by method count (descending). "
            "Returns the N largest classes with their metrics. "
            "Use this to identify potentially complex classes for review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max classes to return. Default: 10.",
                },
            },
        },
    },
    {
        "name": "lens_compare_classes",
        "description": (
            "Compare metrics between multiple classes. "
            "Returns metrics side-by-side for easy comparison."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of class node IDs to compare.",
                },
            },
            "required": ["node_ids"],
        },
    },
    {
        "name": "lens_components",
        "description": (
            "Analyze components (directory-based modules) with cohesion metrics. "
            "Components are directories containing related code. Returns cohesion score "
            "(internal edges / total edges), public API nodes, and internal nodes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Filter to components under this path.",
                },
                "min_cohesion": {
                    "type": "number",
                    "description": "Minimum cohesion threshold (0.0-1.0). Default: 0.0.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "description": (
                        "Output mode. 'summary' (default): counts instead of "
                        "full node lists. 'full': complete public_api/internal lists."
                    ),
                },
                "component": {
                    "type": "string",
                    "description": (
                        "Drill-down: return full details for a single component "
                        "by ID (e.g. 'lenspr/tools'). Overrides mode."
                    ),
                },
            },
        },
    },
    # -- Session Memory Tools --
    {
        "name": "lens_session_write",
        "description": (
            "Write or overwrite a persistent session note by key. "
            "Notes survive context resets and are stored in .lens/session.db. "
            "Use to save task state, decisions, TODOs, and progress."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Note key (e.g. 'current_task', 'done', 'next_steps').",
                },
                "value": {
                    "type": "string",
                    "description": "Note content (markdown supported).",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "lens_session_read",
        "description": (
            "Read all persistent session notes. "
            "Call at the start of a new session to restore context from the previous one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Testing Tool --
    {
        "name": "lens_run_tests",
        "description": (
            "Run pytest in the project root and return structured results. "
            "Shows passed/failed/error counts, individual failure details, "
            "and trimmed output. Use after making changes to verify nothing broke."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Specific test file or directory to run "
                        "(e.g. 'tests/test_auth.py'). "
                        "If omitted, pytest auto-discovers all tests."
                    ),
                },
                "filter_k": {
                    "type": "string",
                    "description": (
                        "pytest -k expression to filter tests by name "
                        "(e.g. 'test_login or test_register')."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds to wait for tests. Default: 120.",
                },
                "max_output_lines": {
                    "type": "integer",
                    "description": "Max lines of pytest output to return. Default: 150.",
                },
            },
        },
    },
    {
        "name": "lens_session_handoff",
        "description": (
            "Generate a handoff document combining recent LensPR changes (with reasoning) "
            "and all current session notes. Saves the result as the 'handoff' session note "
            "so the next session can restore full context with lens_session_read()."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max recent changes to include. Default: 10.",
                },
            },
        },
    },
    {
        "name": "lens_resume",
        "description": (
            "Reconstruct previous session context from the auto-generated action log. "
            "Every successful lens_update_node / lens_patch_node / lens_add_node / "
            "lens_delete_node call writes a structured entry to the session log automatically. "
            "Call this at the START of a new session to understand what changed in the last "
            "session and why — no manual handoff needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Resolver tools (cross-language mappers) --
    {
        "name": "lens_api_map",
        "description": (
            "Map API routes to frontend calls and create cross-language edges. "
            "Scans backend code for route decorators (@app.get, @app.route) and "
            "frontend code for fetch/axios calls, then matches them by path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lens_db_map",
        "description": (
            "Map database tables to the functions that read/write them. "
            "Detects tables from SQLAlchemy __tablename__, Django models, and "
            "CREATE TABLE statements. Maps SQL queries to containing functions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lens_env_map",
        "description": (
            "Map environment variables and infrastructure dependencies. "
            "Detects env var definitions (.env, docker-compose) and usages "
            "(os.environ, os.getenv, process.env) across the codebase. "
            "Highlights undefined env vars (used but not defined anywhere)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "description": (
                        "Output mode. 'summary' (default): usage counts per var, "
                        "no edges. 'full': complete used_by lists and edges."
                    ),
                },
                "env_var": {
                    "type": "string",
                    "description": (
                        "Drill-down: return full details for a single env var "
                        "by name (e.g. 'DATABASE_URL'). Overrides mode."
                    ),
                },
            },
        },
    },
    {
        "name": "lens_ffi_map",
        "description": (
            "Map FFI bridges between TS/JS and native code. "
            "Detects NAPI (.node imports), koffi (koffi.load), ffi-napi "
            "(ffi.Library), and WASM (WebAssembly.instantiate) bindings. "
            "Shows which JS/TS code calls native modules."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "lens_infra_map",
        "description": (
            "Map infrastructure: Dockerfiles, CI/CD workflows, compose services. "
            "Shows Docker build stages, exposed ports, CI job dependencies, "
            "secret/env references, and service topology."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "description": (
                        "Output mode. 'summary' (default): edge counts by type "
                        "instead of full edge lists. 'full': complete edges."
                    ),
                },
                "focus": {
                    "type": "string",
                    "enum": ["ci", "docker", "compose"],
                    "description": (
                        "Drill-down: return full details for one subsystem "
                        "with relevant edges. Overrides mode."
                    ),
                },
            },
        },
    },
    # -- Temporal tools --
    {
        "name": "lens_hotspots",
        "description": (
            "Find code hotspots — functions that change most frequently. "
            "Primary source: LensPR history (git-independent). "
            "Falls back to git when no LensPR history exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max hotspots to return. Default: 20.",
                },
                "since": {
                    "type": "string",
                    "description": (
                        "Time filter: '30d', '7d', '90d', or ISO date. "
                        "Default: all time."
                    ),
                },
                "file_path": {
                    "type": "string",
                    "description": "Filter to files matching this path substring.",
                },
            },
        },
    },
    {
        "name": "lens_node_timeline",
        "description": (
            "Show unified timeline of changes for a specific node. "
            "Merges LensPR history (with reasoning) and git commits (with author)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "The node to get timeline for.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events to return. Default: 20.",
                },
            },
            "required": ["node_id"],
        },
    },
    # -- Runtime tracing tools --
    {
        "name": "lens_trace",
        "description": (
            "Run tests with runtime call tracing and merge edges into the graph. "
            "Uses sys.monitoring (Python 3.12+, ~5% overhead) to observe actual "
            "caller→callee relationships. Resolves instance method dispatch "
            "(self.method()) and dynamic dispatch (getattr, handler maps)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Specific test file or directory.",
                },
                "filter_k": {
                    "type": "string",
                    "description": "pytest -k expression to filter tests.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max seconds. Default: 120.",
                },
            },
        },
    },
    {
        "name": "lens_trace_stats",
        "description": (
            "Show runtime tracing statistics. Reports edge sources: "
            "static-only, runtime-only, confirmed by both. "
            "Shows runtime confirmation rate and top runtime-discovered nodes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]
