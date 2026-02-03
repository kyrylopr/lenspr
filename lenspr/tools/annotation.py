"""Semantic annotation tool handlers.

Hybrid approach:
- Claude generates only `summary` (requires semantic understanding)
- `role` and `side_effects` are auto-detected by patterns (deterministic, no hallucination)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lenspr import database
from lenspr.models import ToolResponse
from lenspr.tools.patterns import VALID_ROLES, auto_annotate

if TYPE_CHECKING:
    from lenspr.context import LensContext


# Re-export for backwards compatibility
__all__ = ["VALID_ROLES"]


def handle_annotate(params: dict, ctx: LensContext) -> ToolResponse:
    """Get node context for annotation. Use lens_save_annotation to save the result."""
    node_id = params["node_id"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    nx_graph = ctx.get_graph()

    # Get context for LLM understanding
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
            "callers": callers,
            "callees": callees,
            "annotation_status": annotation_status,
        },
        hint=(
            "Analyze this code and call lens_save_annotation with only: "
            "summary (1-2 sentences describing what it does). "
            "Role and side_effects are auto-detected from patterns - you don't need to provide them."
        ),
    )


def handle_save_annotation(params: dict, ctx: LensContext) -> ToolResponse:
    """Save semantic annotations to a node.

    Claude provides summary; role and side_effects are auto-detected if not provided.
    """
    node_id = params["node_id"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    # Validate role if provided
    provided_role = params.get("role")
    if provided_role and provided_role not in VALID_ROLES:
        return ToolResponse(
            success=False,
            error=f"Invalid role '{provided_role}'. Must be one of: {', '.join(VALID_ROLES)}",
        )

    # Auto-fill role and side_effects if not provided
    auto = auto_annotate(
        name=node.name,
        node_type=node.type.value,
        source_code=node.source_code or "",
        provided_role=provided_role,
        provided_side_effects=params.get("side_effects"),
    )

    success = database.save_annotation(
        node_id=node_id,
        db_path=ctx.graph_db,
        summary=params.get("summary"),
        role=auto["role"],
        side_effects=auto["side_effects"],
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
            "role": auto["role"],
            "side_effects": auto["side_effects"],
            "auto_detected": provided_role is None,
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
        hint=(
            "For each node, analyze the code and call lens_batch_save_annotations with an array of "
            "{node_id, summary} objects. You only need to provide summary (1-2 sentences). "
            "Role and side_effects are auto-detected from patterns."
        ) if nodes else None,
    )


def handle_batch_save_annotations(params: dict, ctx: LensContext) -> ToolResponse:
    """Save multiple annotations at once. Accepts array of annotation objects.

    Claude provides only summary for each node. Role and side_effects are auto-detected.

    Each annotation should have:
    - node_id (required)
    - summary (required) - Claude's description of what the code does
    - role (optional) - auto-detected if not provided
    - side_effects (optional) - auto-detected if not provided
    """
    annotations = params.get("annotations", [])

    if not annotations:
        return ToolResponse(
            success=False,
            error="No annotations provided. Pass 'annotations' array.",
        )

    saved = []
    errors = []

    for ann in annotations:
        node_id = ann.get("node_id")
        if not node_id:
            errors.append({"error": "Missing node_id", "annotation": ann})
            continue

        # Validate role if provided
        provided_role = ann.get("role")
        if provided_role and provided_role not in VALID_ROLES:
            errors.append({
                "node_id": node_id,
                "error": f"Invalid role '{provided_role}'",
            })
            continue

        # Check node exists
        node = database.get_node(node_id, ctx.graph_db)
        if not node:
            errors.append({
                "node_id": node_id,
                "error": "Node not found",
            })
            continue

        # Auto-fill role and side_effects if not provided
        auto = auto_annotate(
            name=node.name,
            node_type=node.type.value,
            source_code=node.source_code or "",
            provided_role=provided_role,
            provided_side_effects=ann.get("side_effects"),
        )

        # Save annotation
        success = database.save_annotation(
            node_id=node_id,
            db_path=ctx.graph_db,
            summary=ann.get("summary"),
            role=auto["role"],
            side_effects=auto["side_effects"],
            semantic_inputs=ann.get("semantic_inputs"),
            semantic_outputs=ann.get("semantic_outputs"),
        )

        if success:
            saved.append({
                "node_id": node_id,
                "summary": ann.get("summary"),
                "role": auto["role"],
                "side_effects": auto["side_effects"],
            })
        else:
            errors.append({
                "node_id": node_id,
                "error": "Failed to save",
            })

    return ToolResponse(
        success=len(errors) == 0,
        data={
            "saved_count": len(saved),
            "error_count": len(errors),
            "saved": saved,
            "errors": errors if errors else None,
        },
    )


def handle_annotation_stats(params: dict, ctx: LensContext) -> ToolResponse:
    """Get annotation coverage statistics."""
    stats = database.get_annotation_stats(ctx.graph_db)
    return ToolResponse(success=True, data=stats)
