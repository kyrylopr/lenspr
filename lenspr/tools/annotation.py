"""Semantic annotation tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lenspr import database
from lenspr.models import Node, ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_annotate(params: dict, ctx: LensContext) -> ToolResponse:
    """Generate suggested annotations for a node based on code analysis."""
    node_id = params["node_id"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    nx_graph = ctx.get_graph()

    # Analyze the code to suggest annotations
    suggested_role = _detect_role(node, nx_graph)
    detected_side_effects = _detect_side_effects(node)
    detected_inputs, detected_outputs = _detect_io_semantics(node)

    # Get context for better understanding
    callers = []
    callees = []

    if node_id in nx_graph:
        for pred_id in list(nx_graph.predecessors(node_id))[:5]:
            pred_data = nx_graph.nodes.get(pred_id, {})
            callers.append({
                "id": pred_id,
                "name": pred_data.get("name", ""),
                "type": pred_data.get("type", ""),
            })

        for succ_id in list(nx_graph.successors(node_id))[:5]:
            succ_data = nx_graph.nodes.get(succ_id, {})
            callees.append({
                "id": succ_id,
                "name": succ_data.get("name", ""),
                "type": succ_data.get("type", ""),
            })

    annotation_status = {
        "is_annotated": node.is_annotated,
        "is_stale": node.is_annotation_stale,
        "current_summary": node.summary,
        "current_role": node.role.value if node.role else None,
    }

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "name": node.name,
            "type": node.type.value,
            "file_path": node.file_path,
            "source_code": node.source_code,
            "signature": node.signature,
            "docstring": node.docstring,
            "suggested_role": suggested_role,
            "detected_side_effects": detected_side_effects,
            "detected_inputs": detected_inputs,
            "detected_outputs": detected_outputs,
            "callers": callers,
            "callees": callees,
            "annotation_status": annotation_status,
        },
    )


def handle_save_annotation(params: dict, ctx: LensContext) -> ToolResponse:
    """Save semantic annotations to a node."""
    node_id = params["node_id"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    success = database.save_annotation(
        node_id=node_id,
        db_path=ctx.graph_db,
        summary=params.get("summary"),
        role=params.get("role"),
        side_effects=params.get("side_effects"),
        semantic_inputs=params.get("semantic_inputs"),
        semantic_outputs=params.get("semantic_outputs"),
    )

    if not success:
        return ToolResponse(
            success=False,
            error=f"Failed to save annotation for {node_id}",
        )

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "saved": True,
            "summary": params.get("summary"),
            "role": params.get("role"),
        },
    )


def handle_annotate_batch(params: dict, ctx: LensContext) -> ToolResponse:
    """Get nodes that need annotation."""
    type_filter = params.get("type_filter")
    file_path = params.get("file_path")
    unannotated_only = params.get("unannotated_only", True)
    stale_only = params.get("stale_only", False)
    limit = params.get("limit", 10)

    nodes = database.get_nodes(
        ctx.graph_db,
        type_filter=type_filter,
        file_filter=file_path,
    )

    # Filter to annotatable types
    nodes = [n for n in nodes if n.type.value in ("function", "method", "class")]

    # Apply filters
    if stale_only:
        nodes = [n for n in nodes if n.is_annotation_stale]
    elif unannotated_only:
        nodes = [n for n in nodes if not n.is_annotated]

    nodes = nodes[:limit]

    return ToolResponse(
        success=True,
        data={
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "type": n.type.value,
                    "file_path": n.file_path,
                    "signature": n.signature,
                    "is_annotated": n.is_annotated,
                    "is_stale": n.is_annotation_stale,
                }
                for n in nodes
            ],
            "count": len(nodes),
            "filter_applied": {
                "type": type_filter,
                "file_path": file_path,
                "unannotated_only": unannotated_only,
                "stale_only": stale_only,
            },
        },
    )


def handle_annotation_stats(params: dict, ctx: LensContext) -> ToolResponse:
    """Get annotation coverage statistics."""
    stats = database.get_annotation_stats(ctx.graph_db)
    return ToolResponse(success=True, data=stats)


# -- Helper functions for annotation detection --


def _detect_role(node: Node, graph: Any) -> str:
    """Detect semantic role based on code patterns."""
    name = node.name.lower()
    source = node.source_code.lower()

    # Test function
    if name.startswith("test_") or "_test" in name:
        return "test"

    # Validator patterns
    if any(p in name for p in ["validate", "check", "verify", "is_", "has_"]):
        return "validator"

    # Accessor patterns
    if name.startswith("get_") or name.startswith("set_"):
        return "accessor"

    # Factory patterns
    if any(p in name for p in ["create_", "make_", "build_", "new_"]):
        return "factory"

    # Handler patterns
    if any(p in name for p in ["handle_", "_handler", "on_", "_callback"]):
        return "handler"

    # I/O patterns
    io_keywords = ["open(", "read(", "write(", "requests.", "http", "socket", "cursor"]
    if any(kw in source for kw in io_keywords):
        return "io"

    # Transformer patterns (has return and parameters)
    if node.signature and "->" in node.signature and "(" in node.signature:
        params = node.signature.split("(")[1].split(")")[0]
        if params and "self" not in params.split(",")[0]:
            return "transformer"

    # Orchestrator (calls many other functions)
    if node.id in graph:
        callees = list(graph.successors(node.id))
        if len(callees) > 5:
            return "orchestrator"

    # Pure function (no side effects detected)
    if not _detect_side_effects(node):
        return "pure"

    return "utility"


def _detect_side_effects(node: Node) -> list[str]:
    """Detect potential side effects from code patterns."""
    source = node.source_code.lower()
    effects: list[str] = []

    # File I/O
    if any(p in source for p in ["open(", "write(", "writelines(", "pathlib"]):
        effects.append("writes_file")
    if any(p in source for p in ["read(", "readlines(", "json.load"]):
        effects.append("reads_file")

    # Network I/O
    if any(p in source for p in ["requests.", "urllib", "http", "socket", "aiohttp"]):
        effects.append("network_io")

    # Database
    if any(p in source for p in ["cursor", "execute(", "commit(", "session."]):
        effects.append("database_io")

    # Logging/printing
    if any(p in source for p in ["print(", "logging.", "logger."]):
        effects.append("logging")

    # State modification
    if any(p in source for p in ["self.", "global ", "nonlocal "]):
        if "=" in source:
            effects.append("modifies_state")

    return effects


def _detect_io_semantics(node: Node) -> tuple[list[str], list[str]]:
    """Detect semantic input/output types from signature and docstring."""
    inputs: list[str] = []
    outputs: list[str] = []

    sig = node.signature or ""
    doc = node.docstring or ""
    name = node.name.lower()

    # Input detection from parameter names
    param_patterns = {
        "user": "user_input",
        "config": "config",
        "request": "request",
        "data": "data",
        "path": "file_path",
        "url": "url",
        "query": "query",
        "id": "identifier",
    }

    for pattern, semantic in param_patterns.items():
        if pattern in sig.lower():
            inputs.append(semantic)

    # Output detection from return type and name
    if "-> bool" in sig or name.startswith("is_") or name.startswith("has_"):
        outputs.append("boolean")
    if "-> str" in sig:
        outputs.append("string")
    if "-> list" in sig or "-> List" in sig:
        outputs.append("list")
    if "-> dict" in sig or "-> Dict" in sig:
        outputs.append("dict")
    if "error" in name or "exception" in doc.lower():
        outputs.append("error")
    if "valid" in name:
        outputs.append("validation_result")

    return inputs, outputs
