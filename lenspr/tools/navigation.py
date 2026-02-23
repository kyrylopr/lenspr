"""Navigation and discovery tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lenspr import database, graph
from lenspr.models import ToolResponse
from lenspr.tools.helpers import find_containing_node, resolve_or_fail

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_list_nodes(params: dict, ctx: LensContext) -> ToolResponse:
    """List all nodes, optionally filtered by type, file, or name."""
    ctx.ensure_synced()
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


def handle_get_node(params: dict, ctx: LensContext) -> ToolResponse:
    """Get full details of a specific node including its source code."""
    ctx.ensure_synced()
    node_id, err = resolve_or_fail(params["node_id"], ctx)
    if err:
        return err
    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
            hint="Use lens_search or lens_list_nodes to find valid node IDs.",
        )

    # Container nodes (class, module) return metadata + direct children, not full source.
    # Source lives in the individual method/function children — work with those directly.
    # For leaf nodes (function, method, block) always return full source.
    _CONTAINER_TYPES = ("class", "module")
    _LARGE_THRESHOLD = 10_000  # chars

    source = node.source_code
    children: list[dict] | None = None
    warnings: list[str] = []

    if node.type.value in _CONTAINER_TYPES and len(source or "") > _LARGE_THRESHOLD:
        total_lines = len((source or "").splitlines())
        source = None  # Don't return source for large containers

        # Find direct children by ID prefix + depth.
        # Children IDs look like: {node_id}.{child_name} (exactly one dot deeper).
        prefix = node.id + "."
        all_nodes = database.get_nodes(ctx.graph_db)
        child_nodes = []
        for n in all_nodes:
            if not n.id.startswith(prefix):
                continue
            # Direct child: suffix has no dots (one level deeper)
            suffix = n.id[len(prefix):]
            if "." in suffix:
                continue
            if n.type.value in ("function", "method", "class"):
                child_nodes.append({
                    "id": n.id,
                    "name": n.name,
                    "type": n.type.value,
                    "start_line": n.start_line,
                    "end_line": n.end_line,
                    "signature": n.signature,
                })
        # Sort by line number
        child_nodes.sort(key=lambda c: c["start_line"] or 0)
        children = child_nodes

        warnings.append(
            f"Large {node.type.value} ({total_lines} lines). "
            f"Source not returned — use child node IDs listed in 'children' to read individual "
            f"methods/functions. Use lens_update_node on a child node ID to make changes."
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
            "source_code": source,
            "docstring": node.docstring,
            "signature": node.signature,
            **({"children": children} if children is not None else {}),
        },
        warnings=warnings,
    )


def handle_get_connections(params: dict, ctx: LensContext) -> ToolResponse:
    """Get all connections (edges) for a node."""
    ctx.ensure_synced()
    node_id, err = resolve_or_fail(params["node_id"], ctx)
    if err:
        return err
    direction = params.get("direction", "both")
    edges = database.get_edges(node_id, ctx.graph_db, direction)
    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
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


def handle_search(params: dict, ctx: LensContext) -> ToolResponse:
    """Search nodes by name or content."""
    ctx.ensure_synced()
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


def handle_get_structure(params: dict, ctx: LensContext) -> ToolResponse:
    """Get compact overview of project structure."""
    ctx.ensure_synced()
    nx_graph = ctx.get_graph()
    result = graph.get_structure(
        nx_graph,
        max_depth=params.get("max_depth", 2),
        mode=params.get("mode", "summary"),
        limit=params.get("limit", 100),
        offset=params.get("offset", 0),
        path_prefix=params.get("path_prefix"),
    )
    return ToolResponse(success=True, data=result)


def handle_context(params: dict, ctx: LensContext) -> ToolResponse:
    """Get full context for a node in one call."""
    ctx.ensure_synced()
    node_id, err = resolve_or_fail(params["node_id"], ctx)
    if err:
        return err
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

    nx_graph = ctx.get_graph()

    _CONTAINER_TYPES = ("class", "module")
    _LARGE_THRESHOLD = 10_000

    def _get_children(n_id: str) -> list[dict]:
        """Find direct children of a container node by ID prefix."""
        prefix = n_id + "."
        all_nodes = database.get_nodes(ctx.graph_db)
        result = []
        for n in all_nodes:
            if not n.id.startswith(prefix):
                continue
            suffix = n.id[len(prefix):]
            if "." in suffix:
                continue
            if n.type.value in ("function", "method", "class"):
                result.append({
                    "id": n.id,
                    "name": n.name,
                    "type": n.type.value,
                    "start_line": n.start_line,
                    "end_line": n.end_line,
                    "signature": n.signature,
                })
        result.sort(key=lambda c: c["start_line"] or 0)
        return result

    # Container nodes: return metadata + children instead of full source
    source = node.source_code
    children: list[dict] | None = None
    ctx_warnings: list[str] = []

    if node.type.value in _CONTAINER_TYPES and len(source or "") > _LARGE_THRESHOLD:
        total_lines = len((source or "").splitlines())
        source = None
        children = _get_children(node_id)
        ctx_warnings.append(
            f"Large {node.type.value} ({total_lines} lines). "
            f"Source not returned — use child node IDs listed in 'children' to read "
            f"individual methods/functions."
        )

    # Target node info
    target: dict[str, Any] = {
        "id": node.id,
        "type": node.type.value,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "source_code": source if include_source else None,
        "docstring": node.docstring,
        "signature": node.signature,
    }
    if children is not None:
        target["children"] = children

    # Include annotations if present
    if node.is_annotated:
        target["annotation"] = {
            "summary": node.summary,
            "role": node.role.value if node.role else None,
            "side_effects": node.side_effects,
            "semantic_inputs": node.semantic_inputs,
            "semantic_outputs": node.semantic_outputs,
            "is_stale": node.is_annotation_stale,
        }

    # Callers (who depends on this node)
    callers: list[dict] = []
    callers_truncated = False
    max_related = 30  # Cap to prevent response size explosion
    if include_callers and node_id in nx_graph:
        visited: set[str] = set()
        frontier: list[tuple[str, str]] = [
            (p, node_id) for p in nx_graph.predecessors(node_id)
        ]
        for _level in range(depth):
            next_frontier: list[tuple[str, str]] = []
            for pred_id, via in frontier:
                if len(callers) >= max_related:
                    callers_truncated = True
                    break
                if pred_id in visited:
                    continue
                visited.add(pred_id)
                pred_node = database.get_node(pred_id, ctx.graph_db)
                if not pred_node:
                    continue
                edge_data = nx_graph.edges.get((pred_id, via), {})
                caller_info: dict[str, Any] = {
                    "id": pred_node.id,
                    "type": pred_node.type.value,
                    "name": pred_node.name,
                    "file_path": pred_node.file_path,
                    "signature": pred_node.signature,
                    "edge_type": edge_data.get("type", "unknown"),
                    "depth": _level + 1,
                }
                caller_info["start_line"] = pred_node.start_line
                caller_info["end_line"] = pred_node.end_line
                caller_info["docstring"] = (pred_node.docstring or "")[:200] or None
                callers.append(caller_info)
                if _level + 1 < depth:
                    next_frontier.extend(
                        (p, pred_id) for p in nx_graph.predecessors(pred_id)
                    )
            if callers_truncated:
                break
            frontier = next_frontier

    # Callees (what this node depends on)
    callees: list[dict] = []
    callees_truncated = False
    if include_callees and node_id in nx_graph:
        visited_out: set[str] = set()
        frontier_out: list[tuple[str, str]] = [
            (s, node_id) for s in nx_graph.successors(node_id)
        ]
        for _level in range(depth):
            next_frontier_out: list[tuple[str, str]] = []
            for succ_id, via in frontier_out:
                if len(callees) >= max_related:
                    callees_truncated = True
                    break
                if succ_id in visited_out:
                    continue
                visited_out.add(succ_id)
                succ_node = database.get_node(succ_id, ctx.graph_db)
                if not succ_node:
                    continue
                edge_data = nx_graph.edges.get((via, succ_id), {})
                callee_info: dict[str, Any] = {
                    "id": succ_node.id,
                    "type": succ_node.type.value,
                    "name": succ_node.name,
                    "file_path": succ_node.file_path,
                    "signature": succ_node.signature,
                    "edge_type": edge_data.get("type", "unknown"),
                    "depth": _level + 1,
                }
                callee_info["start_line"] = succ_node.start_line
                callee_info["end_line"] = succ_node.end_line
                callee_info["docstring"] = (succ_node.docstring or "")[:200] or None
                callees.append(callee_info)
                if _level + 1 < depth:
                    next_frontier_out.extend(
                        (s, succ_id) for s in nx_graph.successors(succ_id)
                    )
            if callees_truncated:
                break
            frontier_out = next_frontier_out

    # Related tests
    tests: list[dict] = []
    if include_tests:
        node_name = node.name
        # Strategy 1: Find test functions that call this node
        if node_id in nx_graph:
            for pred_id in nx_graph.predecessors(node_id):
                pred_data = nx_graph.nodes.get(pred_id, {})
                pred_name = pred_data.get("name", "")
                pred_file = pred_data.get("file_path", "")
                if pred_name.startswith("test_") or pred_file.startswith("test_"):
                    pred_node = database.get_node(pred_id, ctx.graph_db)
                    if pred_node:
                        test_info: dict[str, Any] = {
                            "id": pred_node.id,
                            "name": pred_node.name,
                            "file_path": pred_node.file_path,
                            "start_line": pred_node.start_line,
                            "end_line": pred_node.end_line,
                        }
                        tests.append(test_info)

        # Strategy 2: Find test functions by naming convention
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
                    "start_line": tn.start_line,
                    "end_line": tn.end_line,
                }
                tests.append(tn_info)

    result: dict[str, Any] = {"target": target}
    if include_callers:
        result["callers"] = callers
        result["caller_count"] = len(callers)
        if callers_truncated:
            result["callers_truncated"] = True
            result["callers_note"] = (
                f"Showing first {max_related} callers. "
                "Use lens_find_usages for the complete list."
            )
    if include_callees:
        result["callees"] = callees
        result["callee_count"] = len(callees)
        if callees_truncated:
            result["callees_truncated"] = True
            result["callees_note"] = (
                f"Showing first {max_related} callees. "
                "Use lens_get_connections for the complete list."
            )
    if include_tests:
        result["tests"] = tests
        result["test_count"] = len(tests)

    # Add modification warning
    caller_count = len(callers) if include_callers else 0
    test_count = len(tests) if include_tests else 0
    if caller_count > 10:
        severity = "CRITICAL" if caller_count > 20 else "HIGH"
        result["modification_warning"] = (
            f"⚠️ {severity}: Modifying this node affects {caller_count} callers. "
            f"Use lens_check_impact before changes."
        )
    elif caller_count > 0:
        result["modification_warning"] = (
            f"ℹ️ Modifying this node affects {caller_count} caller(s). "
        )
    if test_count == 0:
        result["test_warning"] = "⚠️ NO TESTS: Consider adding tests before modifying."

    return ToolResponse(success=True, data=result, warnings=ctx_warnings)


def handle_grep(params: dict, ctx: LensContext) -> ToolResponse:
    """Search for a text pattern across all project files."""
    ctx.ensure_synced()

    import fnmatch
    import re

    from lenspr.parsers import is_supported_file

    pattern_str = params["pattern"]
    file_glob = params.get("file_glob") or None  # None/empty = all supported languages
    max_results = params.get("max_results", 50)

    try:
        regex = re.compile(pattern_str)
    except re.error:
        regex = re.compile(re.escape(pattern_str))

    nx_graph = ctx.get_graph()
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

        # Filter by file type
        if file_glob is None:
            # Default: all supported languages
            if not is_supported_file(str(file_path)):
                continue
        else:
            # User-specified glob pattern
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
                containing_node = find_containing_node(nx_graph, rel, line_num)
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
