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
        "description": "List all nodes in the codebase, optionally filtered by type or file.",
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
        "description": "Get compact overview of project structure (files, classes, functions).",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_depth": {
                    "type": "integer",
                    "description": "Depth of detail. Default: 2.",
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
    max_depth = params.get("max_depth", 2)
    structure = graph.get_structure(G, max_depth)
    return ToolResponse(success=True, data={"structure": structure})


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
        frontier = set(G.predecessors(node_id))
        for _level in range(depth):
            next_frontier: set[str] = set()
            for pred_id in frontier:
                if pred_id in visited:
                    continue
                visited.add(pred_id)
                pred_node = database.get_node(pred_id, ctx.graph_db)
                if pred_node:
                    edge_data = G.edges.get((pred_id, node_id), {})
                    callers.append({
                        "id": pred_node.id,
                        "type": pred_node.type.value,
                        "name": pred_node.name,
                        "file_path": pred_node.file_path,
                        "signature": pred_node.signature,
                        "source_code": pred_node.source_code,
                        "edge_type": edge_data.get("type", "unknown"),
                        "depth": _level + 1,
                    })
                    if _level + 1 < depth:
                        next_frontier.update(G.predecessors(pred_id))
            frontier = next_frontier

    # Callees (what this node depends on)
    callees: list[dict] = []
    if include_callees and node_id in G:
        visited_out: set[str] = set()
        frontier_out = set(G.successors(node_id))
        for _level in range(depth):
            next_frontier_out: set[str] = set()
            for succ_id in frontier_out:
                if succ_id in visited_out:
                    continue
                visited_out.add(succ_id)
                succ_node = database.get_node(succ_id, ctx.graph_db)
                if succ_node:
                    edge_data = G.edges.get((node_id, succ_id), {})
                    callees.append({
                        "id": succ_node.id,
                        "type": succ_node.type.value,
                        "name": succ_node.name,
                        "file_path": succ_node.file_path,
                        "signature": succ_node.signature,
                        "source_code": succ_node.source_code,
                        "edge_type": edge_data.get("type", "unknown"),
                        "depth": _level + 1,
                    })
                    if _level + 1 < depth:
                        next_frontier_out.update(G.successors(succ_id))
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
                        tests.append({
                            "id": pred_node.id,
                            "name": pred_node.name,
                            "file_path": pred_node.file_path,
                            "source_code": pred_node.source_code,
                        })

        # Strategy 2: Find test functions by naming convention (test_<name>)
        test_nodes = database.search_nodes(
            f"test_{node_name}", ctx.graph_db, search_in="name"
        )
        seen_ids = {t["id"] for t in tests}
        for tn in test_nodes:
            if tn.id not in seen_ids and tn.type.value in ("function", "method"):
                tests.append({
                    "id": tn.id,
                    "name": tn.name,
                    "file_path": tn.file_path,
                    "source_code": tn.source_code,
                })

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
