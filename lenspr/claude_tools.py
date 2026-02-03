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
from lenspr.validator import validate_full

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

    # Update database
    new_hash = hashlib.sha256(new_source.encode()).hexdigest()
    database.update_node_source(node_id, new_source, new_hash, ctx.graph_db)

    # Record history
    from lenspr.tracker import record_change

    G = ctx.get_graph()
    impact = graph.get_impact_zone(G, node_id, depth=1)
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

    # Invalidate graph cache
    ctx.invalidate_graph()

    return ToolResponse(
        success=True,
        data={"node_id": node_id, "new_hash": new_hash},
        warnings=validation.warnings,
        affected_nodes=impact.get("direct_callers", []),
    )


def _handle_add_node(params: dict, ctx: LensContext) -> ToolResponse:
    file_path = ctx.project_root / params["file_path"]
    source_code = params["source_code"]

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
        content = file_path.read_text()
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
    content = file_path.read_text()
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
        caller_content = caller_file.read_text()

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
            text = py_file.read_text()
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
