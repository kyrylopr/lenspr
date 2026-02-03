"""Tool definitions and handlers for Claude integration."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from lenspr import database, graph
from lenspr.models import (
    PatchError,
    ToolResponse,
)
from lenspr.patcher import insert_code, remove_lines
from lenspr.validator import validate_full, validate_syntax

if TYPE_CHECKING:
    from lenspr.context import LensContext

# -- Tool definitions for Claude API --

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
        "description": "Get all connections (edges) for a node — what it calls and what calls it.",
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
                    "description": "How many levels of dependencies to check. Default: 2.",
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
                    "description": "Node ID to insert after. If omitted, appends to end of file.",
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
            "Use mode='summary' for large projects (returns counts instead of details)."
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
                    "enum": ["full", "summary"],
                    "description": "full=all details, summary=counts only. Default: summary.",
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
            "related tests, and imports. Replaces multiple get_node + get_connections calls."
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
                    "description": "Include nodes that call/use this node. Default: true.",
                },
                "include_callees": {
                    "type": "boolean",
                    "description": "Include nodes this node calls/uses. Default: true.",
                },
                "include_tests": {
                    "type": "boolean",
                    "description": "Include related test functions. Default: true.",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many levels of callers/callees to include. Default: 1.",
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
            "with graph context: which function/class contains each match, and who calls it."
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
            "Apply multiple node updates atomically. All changes are validated first, "
            "then applied together with a single reparse. Rolls back everything on error."
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
]


# -- Tool handlers --


def handle_tool_call(
    tool_name: str, parameters: dict, ctx: LensContext
) -> ToolResponse:
    """Route a tool call to the appropriate handler."""
    handlers = {
        "lens_list_nodes": _handle_list_nodes,
        "lens_get_node": _handle_get_node,
        "lens_get_connections": _handle_get_connections,
        "lens_check_impact": _handle_check_impact,
        "lens_update_node": _handle_update_node,
        "lens_add_node": _handle_add_node,
        "lens_delete_node": _handle_delete_node,
        "lens_search": _handle_search,
        "lens_get_structure": _handle_get_structure,
        "lens_rename": _handle_rename,
        "lens_context": _handle_context,
        "lens_grep": _handle_grep,
        "lens_diff": _handle_diff,
        "lens_batch": _handle_batch,
        "lens_health": _handle_health,
        "lens_dependencies": _handle_dependencies,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return ToolResponse(success=False, error=f"Unknown tool: {tool_name}")

    try:
        return handler(parameters, ctx)
    except Exception as e:
        return ToolResponse(success=False, error=str(e))


def _handle_list_nodes(params: dict, ctx: LensContext) -> ToolResponse:
    nodes = database.get_nodes(
        ctx.graph_db,
        type_filter=params.get("type"),
        file_filter=params.get("file_path"),
        name_filter=params.get("name"),
    )
    return ToolResponse(
        success=True,
        data={
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type.value,
                    "name": n.name,
                    "file_path": n.file_path,
                    "signature": n.signature,
                    "start_line": n.start_line,
                }
                for n in nodes
            ],
            "count": len(nodes),
        },
    )


def _handle_get_node(params: dict, ctx: LensContext) -> ToolResponse:
    node = database.get_node(params["node_id"], ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {params['node_id']}",
            hint="Use lens_search or lens_list_nodes to find valid node IDs.",
        )
    return ToolResponse(
        success=True,
        data={
            "id": node.id,
            "type": node.type.value,
            "name": node.name,
            "qualified_name": node.qualified_name,
            "file_path": node.file_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "source_code": node.source_code,
            "docstring": node.docstring,
            "signature": node.signature,
        },
    )


def _handle_get_connections(params: dict, ctx: LensContext) -> ToolResponse:
    direction = params.get("direction", "both")
    edges = database.get_edges(params["node_id"], ctx.graph_db, direction)
    return ToolResponse(
        success=True,
        data={
            "node_id": params["node_id"],
            "direction": direction,
            "edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    "type": e.type.value,
                    "confidence": e.confidence.value,
                    "line": e.line_number,
                }
                for e in edges
            ],
            "count": len(edges),
        },
    )


def _handle_check_impact(params: dict, ctx: LensContext) -> ToolResponse:
    G = ctx.get_graph()
    depth = params.get("depth", 2)
    impact = graph.get_impact_zone(G, params["node_id"], depth)
    return ToolResponse(success=True, data=impact)


def _handle_update_node(params: dict, ctx: LensContext) -> ToolResponse:
    node_id = params["node_id"]
    new_source = params["new_source"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    # Three-level validation
    validation = validate_full(new_source, node)
    if not validation.valid:
        return ToolResponse(
            success=False,
            error=validation.errors[0] if validation.errors else "Validation failed.",
            hint="Fix the issues and try again.",
            warnings=validation.warnings,
        )

    # Buffer the patch
    file_path = ctx.project_root / node.file_path
    ctx.patch_buffer.add(file_path, node, new_source)

    # Apply immediately (single-node update)
    try:
        ctx.patch_buffer.flush()
    except PatchError as e:
        ctx.patch_buffer.discard()
        return ToolResponse(success=False, error=str(e))

    # Compute impact BEFORE reparse (graph still has old edges)
    G = ctx.get_graph()
    impact = graph.get_impact_zone(G, node_id, depth=1)

    # Record history
    from lenspr.tracker import record_change

    new_hash = hashlib.sha256(new_source.encode()).hexdigest()
    record_change(
        node_id=node_id,
        action="modified",
        old_source=node.source_code,
        new_source=new_source,
        old_hash=node.hash,
        new_hash=new_hash,
        affected_nodes=impact.get("direct_callers", []),
        description=f"Updated {node.name}",
        db_path=ctx.history_db,
    )

    # Reparse file to rebuild nodes AND edges from the patched source
    ctx.reparse_file(file_path)

    return ToolResponse(
        success=True,
        data={"node_id": node_id, "new_hash": new_hash},
        warnings=validation.warnings,
        affected_nodes=impact.get("direct_callers", []),
    )


def _handle_add_node(params: dict, ctx: LensContext) -> ToolResponse:
    file_path = ctx.project_root / params["file_path"]
    source_code = params["source_code"]

    # Validate syntax before inserting
    syntax_check = validate_syntax(source_code)
    if not syntax_check.valid:
        return ToolResponse(
            success=False,
            error=syntax_check.errors[0] if syntax_check.errors else "Syntax error.",
            hint="Fix the syntax and try again.",
        )

    if not file_path.exists():
        return ToolResponse(
            success=False,
            error=f"File not found: {params['file_path']}",
        )

    after_node_id = params.get("after_node")
    after_line = 0

    if after_node_id:
        after_node = database.get_node(after_node_id, ctx.graph_db)
        if after_node:
            after_line = after_node.end_line
        else:
            return ToolResponse(
                success=False, error=f"Node not found: {after_node_id}"
            )
    else:
        # Append to end of file
        content = file_path.read_text(encoding="utf-8")
        after_line = len(content.splitlines())

    new_content = insert_code(file_path, source_code, after_line)
    file_path.write_text(new_content, encoding="utf-8")

    # Reparse file to update graph
    ctx.reparse_file(file_path)

    return ToolResponse(
        success=True,
        data={"file": params["file_path"], "inserted_after_line": after_line},
    )


def _handle_delete_node(params: dict, ctx: LensContext) -> ToolResponse:
    node_id = params["node_id"]
    node = database.get_node(node_id, ctx.graph_db)

    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    file_path = ctx.project_root / node.file_path
    new_content = remove_lines(file_path, node.start_line, node.end_line)
    file_path.write_text(new_content, encoding="utf-8")

    # Record deletion
    from lenspr.tracker import record_change

    record_change(
        node_id=node_id,
        action="deleted",
        old_source=node.source_code,
        new_source=None,
        old_hash=node.hash,
        new_hash="",
        affected_nodes=[],
        description=f"Deleted {node.name}",
        db_path=ctx.history_db,
    )

    # Remove from database and reparse
    database.delete_node(node_id, ctx.graph_db)
    ctx.reparse_file(file_path)

    return ToolResponse(success=True, data={"deleted": node_id})


def _handle_search(params: dict, ctx: LensContext) -> ToolResponse:
    search_in = params.get("search_in", "all")
    nodes = database.search_nodes(params["query"], ctx.graph_db, search_in)
    return ToolResponse(
        success=True,
        data={
            "results": [
                {
                    "id": n.id,
                    "type": n.type.value,
                    "name": n.name,
                    "file_path": n.file_path,
                    "signature": n.signature,
                }
                for n in nodes
            ],
            "count": len(nodes),
        },
    )


def _handle_get_structure(params: dict, ctx: LensContext) -> ToolResponse:
    G = ctx.get_graph()
    result = graph.get_structure(
        G,
        max_depth=params.get("max_depth", 2),
        mode=params.get("mode", "summary"),  # Default to summary for scalability
        limit=params.get("limit", 100),
        offset=params.get("offset", 0),
        path_prefix=params.get("path_prefix"),
    )
    return ToolResponse(success=True, data=result)


def _handle_rename(params: dict, ctx: LensContext) -> ToolResponse:
    node_id = params["node_id"]
    new_name = params["new_name"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    old_name = node.name

    # Find all incoming edges (callers/importers)
    edges = database.get_edges(node_id, ctx.graph_db, direction="incoming")
    warnings: list[str] = []

    # Update definition
    file_path = ctx.project_root / node.file_path
    content = file_path.read_text(encoding="utf-8")
    # Replace in definition only (within the node's line range)
    lines = content.splitlines()
    for i in range(node.start_line - 1, min(node.end_line, len(lines))):
        if old_name in lines[i]:
            lines[i] = lines[i].replace(old_name, new_name, 1)
            break
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Update callers
    files_modified = {node.file_path}
    refs_updated = 1  # The definition

    for edge in edges:
        caller = database.get_node(edge.from_node, ctx.graph_db)
        if not caller:
            continue

        caller_file = ctx.project_root / caller.file_path
        caller_content = caller_file.read_text(encoding="utf-8")

        if old_name in caller_content:
            caller_content = caller_content.replace(old_name, new_name)
            caller_file.write_text(caller_content, encoding="utf-8")
            files_modified.add(caller.file_path)
            refs_updated += caller_content.count(new_name)

    # Scan for string references that were NOT renamed
    needs_review: list[dict] = []
    for py_file in ctx.project_root.rglob("*.py"):
        rel = str(py_file.relative_to(ctx.project_root))
        if rel in files_modified:
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if old_name in line:
                needs_review.append({
                    "file": rel,
                    "line": i,
                    "context": line.strip(),
                })

    if needs_review:
        warnings.append(
            f"Found {len(needs_review)} possible string references not auto-renamed. "
            f"Review these manually."
        )

    # Reparse all modified files
    for f in files_modified:
        ctx.reparse_file(ctx.project_root / f)

    return ToolResponse(
        success=True,
        data={
            "old_name": old_name,
            "new_name": new_name,
            "files_modified": len(files_modified),
            "references_updated": refs_updated,
            "needs_review": needs_review,
        },
        warnings=warnings,
    )


def _handle_context(params: dict, ctx: LensContext) -> ToolResponse:
    node_id = params["node_id"]
    include_callers = params.get("include_callers", True)
    include_callees = params.get("include_callees", True)
    include_tests = params.get("include_tests", True)
    include_source = params.get("include_source", True)
    depth = params.get("depth", 1)

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
            hint="Use lens_search or lens_list_nodes to find valid node IDs.",
        )

    G = ctx.get_graph()

    # Target node info
    target = {
        "id": node.id,
        "type": node.type.value,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "source_code": node.source_code,
        "docstring": node.docstring,
        "signature": node.signature,
    }

    # Callers (who depends on this node)
    callers: list[dict] = []
    if include_callers and node_id in G:
        visited: set[str] = set()
        # frontier: list of (pred_id, via_node) — via_node is who pred calls
        frontier: list[tuple[str, str]] = [
            (p, node_id) for p in G.predecessors(node_id)
        ]
        for _level in range(depth):
            next_frontier: list[tuple[str, str]] = []
            for pred_id, via in frontier:
                if pred_id in visited:
                    continue
                visited.add(pred_id)
                pred_node = database.get_node(pred_id, ctx.graph_db)
                if not pred_node:
                    continue  # skip external/phantom nodes
                edge_data = G.edges.get((pred_id, via), {})
                caller_info: dict[str, Any] = {
                    "id": pred_node.id,
                    "type": pred_node.type.value,
                    "name": pred_node.name,
                    "file_path": pred_node.file_path,
                    "signature": pred_node.signature,
                    "edge_type": edge_data.get("type", "unknown"),
                    "depth": _level + 1,
                }
                if include_source:
                    caller_info["source_code"] = pred_node.source_code
                else:
                    caller_info["start_line"] = pred_node.start_line
                    caller_info["end_line"] = pred_node.end_line
                callers.append(caller_info)
                if _level + 1 < depth:
                    next_frontier.extend(
                        (p, pred_id) for p in G.predecessors(pred_id)
                    )
            frontier = next_frontier

    # Callees (what this node depends on)
    callees: list[dict] = []
    if include_callees and node_id in G:
        visited_out: set[str] = set()
        frontier_out: list[tuple[str, str]] = [
            (s, node_id) for s in G.successors(node_id)
        ]
        for _level in range(depth):
            next_frontier_out: list[tuple[str, str]] = []
            for succ_id, via in frontier_out:
                if succ_id in visited_out:
                    continue
                visited_out.add(succ_id)
                succ_node = database.get_node(succ_id, ctx.graph_db)
                if not succ_node:
                    continue  # skip external/phantom nodes
                edge_data = G.edges.get((via, succ_id), {})
                callee_info: dict[str, Any] = {
                    "id": succ_node.id,
                    "type": succ_node.type.value,
                    "name": succ_node.name,
                    "file_path": succ_node.file_path,
                    "signature": succ_node.signature,
                    "edge_type": edge_data.get("type", "unknown"),
                    "depth": _level + 1,
                }
                if include_source:
                    callee_info["source_code"] = succ_node.source_code
                else:
                    callee_info["start_line"] = succ_node.start_line
                    callee_info["end_line"] = succ_node.end_line
                callees.append(callee_info)
                if _level + 1 < depth:
                    next_frontier_out.extend(
                        (s, succ_id) for s in G.successors(succ_id)
                    )
            frontier_out = next_frontier_out

    # Related tests
    tests: list[dict] = []
    if include_tests:
        node_name = node.name
        # Strategy 1: Find test functions that call this node (graph edges)
        if node_id in G:
            for pred_id in G.predecessors(node_id):
                pred_data = G.nodes.get(pred_id, {})
                pred_name = pred_data.get("name", "")
                pred_file = pred_data.get("file_path", "")
                if pred_name.startswith("test_") or pred_file.startswith("test_"):
                    pred_node = database.get_node(pred_id, ctx.graph_db)
                    if pred_node:
                        test_info: dict[str, Any] = {
                            "id": pred_node.id,
                            "name": pred_node.name,
                            "file_path": pred_node.file_path,
                        }
                        if include_source:
                            test_info["source_code"] = pred_node.source_code
                        tests.append(test_info)

        # Strategy 2: Find test functions by naming convention (test_<name>)
        test_nodes = database.search_nodes(
            f"test_{node_name}", ctx.graph_db, search_in="name"
        )
        seen_ids = {t["id"] for t in tests}
        for tn in test_nodes:
            if tn.id not in seen_ids and tn.type.value in ("function", "method"):
                tn_info: dict[str, Any] = {
                    "id": tn.id,
                    "name": tn.name,
                    "file_path": tn.file_path,
                }
                if include_source:
                    tn_info["source_code"] = tn.source_code
                tests.append(tn_info)

    result: dict[str, Any] = {
        "target": target,
    }
    if include_callers:
        result["callers"] = callers
        result["caller_count"] = len(callers)
    if include_callees:
        result["callees"] = callees
        result["callee_count"] = len(callees)
    if include_tests:
        result["tests"] = tests
        result["test_count"] = len(tests)

    return ToolResponse(success=True, data=result)


def _handle_grep(params: dict, ctx: LensContext) -> ToolResponse:
    import fnmatch
    import re

    pattern_str = params["pattern"]
    file_glob = params.get("file_glob", "*.py")
    max_results = params.get("max_results", 50)

    try:
        regex = re.compile(pattern_str)
    except re.error:
        # Fall back to literal search
        regex = re.compile(re.escape(pattern_str))

    G = ctx.get_graph()
    results: list[dict] = []

    skip_dirs = {
        "__pycache__", ".git", ".lens", ".venv", "venv", "env",
        "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", ".eggs", ".tox",
    }

    for file_path in sorted(ctx.project_root.rglob("*")):
        if len(results) >= max_results:
            break
        if not file_path.is_file():
            continue
        if any(part in skip_dirs for part in file_path.parts):
            continue
        rel = str(file_path.relative_to(ctx.project_root))
        if not fnmatch.fnmatch(rel, file_glob) and not fnmatch.fnmatch(
            file_path.name, file_glob
        ):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for line_num, line_text in enumerate(content.splitlines(), 1):
            if len(results) >= max_results:
                break
            if regex.search(line_text):
                # Find which graph node contains this line
                containing_node = _find_containing_node(G, rel, line_num)
                match_info: dict[str, Any] = {
                    "file": rel,
                    "line": line_num,
                    "text": line_text.strip(),
                }
                if containing_node:
                    match_info["node_id"] = containing_node["id"]
                    match_info["node_name"] = containing_node["name"]
                    match_info["node_type"] = containing_node["type"]
                results.append(match_info)

    return ToolResponse(
        success=True,
        data={
            "pattern": pattern_str,
            "results": results,
            "count": len(results),
            "truncated": len(results) >= max_results,
        },
    )


def _find_containing_node(
    graph: Any, file_path: str, line_num: int
) -> dict[str, str] | None:
    """Find the most specific graph node containing a given line."""
    best: dict[str, Any] | None = None
    best_span = float("inf")

    for nid, data in graph.nodes(data=True):
        if data.get("file_path") != file_path:
            continue
        start = data.get("start_line", 0)
        end = data.get("end_line", 0)
        if start <= line_num <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best = {"id": nid, "name": data.get("name", ""), "type": data.get("type", "")}

    return best


def _handle_diff(params: dict, ctx: LensContext) -> ToolResponse:
    """Compare current filesystem against graph DB without syncing."""
    parser = ctx._parser
    extensions = set(parser.get_file_extensions())
    skip_dirs = {
        "__pycache__", ".git", ".lens", ".venv", "venv", "env",
        "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", ".eggs", ".tox",
    }

    # Load current graph nodes
    old_nodes, _ = database.load_graph(ctx.graph_db)
    old_by_file: dict[str, list[dict[str, Any]]] = {}
    for n in old_nodes:
        old_by_file.setdefault(n.file_path, []).append({
            "id": n.id, "name": n.name, "type": n.type.value, "hash": n.hash,
        })
    old_files = set(old_by_file.keys())

    # Scan current filesystem
    current_files: set[str] = set()
    for file_path in sorted(ctx.project_root.rglob("*")):
        if not file_path.is_file():
            continue
        if any(part in skip_dirs for part in file_path.parts):
            continue
        if file_path.suffix not in extensions:
            continue
        current_files.add(str(file_path.relative_to(ctx.project_root)))

    # Compare using fingerprints for speed
    fingerprints = ctx._load_fingerprints()

    added_files: list[str] = sorted(current_files - old_files)
    deleted_files: list[str] = sorted(old_files - current_files)
    modified_files: list[str] = []

    for rel in sorted(current_files & old_files):
        file_path = ctx.project_root / rel
        stat = file_path.stat()
        old_fp = fingerprints.get(rel, {})
        if (
            stat.st_mtime != old_fp.get("mtime")
            or stat.st_size != old_fp.get("size")
        ):
            modified_files.append(rel)

    return ToolResponse(
        success=True,
        data={
            "added_files": added_files,
            "deleted_files": deleted_files,
            "modified_files": modified_files,
            "total_changes": (
                len(added_files) + len(deleted_files) + len(modified_files)
            ),
            "deleted_nodes": [
                node_info
                for f in deleted_files
                for node_info in old_by_file.get(f, [])
            ],
        },
    )


def _handle_batch(params: dict, ctx: LensContext) -> ToolResponse:
    """Apply multiple node updates atomically."""
    updates = params["updates"]
    if not updates:
        return ToolResponse(success=False, error="No updates provided.")

    # Phase 1: Validate all updates
    nodes_to_update: list[tuple[Any, str]] = []  # (node, new_source)
    for upd in updates:
        node_id = upd["node_id"]
        new_source = upd["new_source"]

        node = database.get_node(node_id, ctx.graph_db)
        if not node:
            return ToolResponse(
                success=False,
                error=f"Node not found: {node_id}",
                hint="All updates aborted. Fix the node_id and retry.",
            )

        validation = validate_full(new_source, node)
        if not validation.valid:
            return ToolResponse(
                success=False,
                error=(
                    f"Validation failed for {node_id}: "
                    f"{validation.errors[0] if validation.errors else 'unknown'}"
                ),
                hint="All updates aborted. Fix the source and retry.",
                warnings=validation.warnings,
            )

        nodes_to_update.append((node, new_source))

    # Phase 2: Buffer all patches
    files_to_reparse: set[str] = set()
    for node, new_source in nodes_to_update:
        file_path = ctx.project_root / node.file_path
        ctx.patch_buffer.add(file_path, node, new_source)
        files_to_reparse.add(node.file_path)

    # Phase 3: Apply all patches at once
    try:
        ctx.patch_buffer.flush()
    except PatchError as e:
        ctx.patch_buffer.discard()
        return ToolResponse(
            success=False,
            error=f"Patch failed: {e}",
            hint="All updates rolled back.",
        )

    # Phase 4: Record history and reparse
    from lenspr.tracker import record_change

    results: list[dict[str, str]] = []
    for node, new_source in nodes_to_update:
        new_hash = hashlib.sha256(new_source.encode()).hexdigest()
        record_change(
            node_id=node.id,
            action="modified",
            old_source=node.source_code,
            new_source=new_source,
            old_hash=node.hash,
            new_hash=new_hash,
            affected_nodes=[],
            description=f"Batch update: {node.name}",
            db_path=ctx.history_db,
        )
        results.append({"node_id": node.id, "new_hash": new_hash})

    # Single reparse for all affected files
    for rel_path in files_to_reparse:
        ctx.reparse_file(ctx.project_root / rel_path)

    return ToolResponse(
        success=True,
        data={
            "updated": results,
            "count": len(results),
            "files_reparsed": len(files_to_reparse),
        },
    )


def _handle_health(params: dict, ctx: LensContext) -> ToolResponse:
    """Generate health report for the code graph."""
    G = ctx.get_graph()

    # Node stats — separate project nodes from phantom/external references
    project_nodes = 0
    external_refs = 0
    nodes_by_type: dict[str, int] = {}
    nodes_without_docstring = 0
    for _nid, data in G.nodes(data=True):
        ntype = data.get("type")
        if ntype is None:
            external_refs += 1
            continue
        project_nodes += 1
        nodes_by_type[ntype] = nodes_by_type.get(ntype, 0) + 1
        if ntype in ("function", "method", "class") and not data.get("docstring"):
            nodes_without_docstring += 1

    # Edge stats — separate internal (project) vs external (stdlib/third-party)
    total_edges = G.number_of_edges()
    edges_by_type: dict[str, int] = {}
    edges_by_confidence: dict[str, int] = {}
    unresolved_edges: list[dict[str, str]] = []
    internal_resolved = 0
    internal_total = 0
    external_count = 0

    for u, v, data in G.edges(data=True):
        etype = data.get("type", "unknown")
        edges_by_type[etype] = edges_by_type.get(etype, 0) + 1
        conf = data.get("confidence", "unknown")
        edges_by_confidence[conf] = edges_by_confidence.get(conf, 0) + 1

        # Track internal vs external for confidence calculation
        if conf == "external":
            external_count += 1
        else:
            internal_total += 1
            if conf == "resolved":
                internal_resolved += 1

        if conf == "unresolved":
            reason = data.get("untracked_reason", "")
            unresolved_edges.append({
                "from": u, "to": v, "reason": reason,
            })

    # Circular imports
    from lenspr.graph import detect_circular_imports
    cycles = detect_circular_imports(G)

    # Confidence % is now calculated only for internal edges
    internal_confidence_pct = (
        (internal_resolved / internal_total * 100) if internal_total > 0 else 100.0
    )

    documentable = (
        nodes_by_type.get("function", 0)
        + nodes_by_type.get("method", 0)
        + nodes_by_type.get("class", 0)
    )
    docstring_pct = (
        ((documentable - nodes_without_docstring) / documentable * 100)
        if documentable > 0
        else 100.0
    )

    return ToolResponse(
        success=True,
        data={
            "total_nodes": project_nodes,
            "external_refs": external_refs,
            "nodes_by_type": nodes_by_type,
            "total_edges": total_edges,
            "edges_by_type": edges_by_type,
            "edges_by_confidence": edges_by_confidence,
            # New: separate internal/external metrics
            "internal_edges": {
                "total": internal_total,
                "resolved": internal_resolved,
                "confidence_pct": round(internal_confidence_pct, 1),
            },
            "external_edges": external_count,
            "confidence_pct": round(internal_confidence_pct, 1),  # Keep for backwards compat
            "nodes_without_docstring": nodes_without_docstring,
            "docstring_pct": round(docstring_pct, 1),
            "circular_imports": cycles,
            "unresolved_edges": unresolved_edges[:20],
            "unresolved_count": len(unresolved_edges),
        },
    )


def _handle_dependencies(params: dict, ctx: LensContext) -> ToolResponse:
    """List all external dependencies (stdlib and third-party)."""
    import sys
    from collections import defaultdict

    G = ctx.get_graph()
    group_by = params.get("group_by", "package")

    # Get stdlib modules (Python 3.10+)
    stdlib_names: set[str]
    try:
        stdlib_names = set(sys.stdlib_module_names)
    except AttributeError:
        # Fallback for older Python
        from lenspr.parsers.python_parser import _STDLIB_MODULES
        stdlib_names = _STDLIB_MODULES

    # Collect external edges
    deps_by_package: dict[str, dict] = defaultdict(lambda: {"usages": 0, "files": set()})
    deps_by_file: dict[str, list] = defaultdict(list)

    for u, v, data in G.edges(data=True):
        conf = data.get("confidence", "")
        if conf != "external":
            continue

        # Get package name (first part of target)
        target = v
        package = target.split(".")[0] if target else ""
        if not package:
            continue

        # Determine if stdlib or third-party
        is_stdlib = package in stdlib_names

        # Get source file
        source_node = G.nodes.get(u, {})
        source_file = source_node.get("file_path", "unknown")

        deps_by_package[package]["usages"] += 1
        deps_by_package[package]["files"].add(source_file)
        deps_by_package[package]["type"] = "stdlib" if is_stdlib else "third-party"

        deps_by_file[source_file].append({
            "package": package,
            "target": target,
            "type": "stdlib" if is_stdlib else "third-party",
        })

    if group_by == "file":
        result = [
            {
                "file": fp,
                "dependencies": sorted(deps, key=lambda x: x["package"]),
                "count": len(deps),
            }
            for fp, deps in sorted(deps_by_file.items())
        ]
        return ToolResponse(
            success=True,
            data={
                "by_file": result,
                "total_files": len(result),
            },
        )
    else:
        # group_by == "package"
        stdlib_deps = []
        third_party_deps = []
        for pkg, info in sorted(deps_by_package.items()):
            entry = {
                "package": pkg,
                "type": info["type"],
                "usages": info["usages"],
                "used_in_files": len(info["files"]),
            }
            if info["type"] == "stdlib":
                stdlib_deps.append(entry)
            else:
                third_party_deps.append(entry)

        return ToolResponse(
            success=True,
            data={
                "dependencies": stdlib_deps + third_party_deps,
                "total_packages": len(deps_by_package),
                "stdlib_count": len(stdlib_deps),
                "third_party_count": len(third_party_deps),
            },
        )
