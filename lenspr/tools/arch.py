"""Architecture metrics tool handlers for LensPR.

Philosophy: LensPR is a data provider, not a decision maker.
These tools return raw metrics computed during sync. Claude decides what they mean.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lenspr import database
from lenspr.architecture import (
    detect_components,
    get_class_analysis_from_stored,
)
from lenspr.models import NodeType, ToolResponse
from lenspr.tools.helpers import resolve_or_fail

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_class_metrics(params: dict, ctx: LensContext) -> ToolResponse:
    """Get pre-computed metrics for a class.

    Returns method count, lines, public/private methods, dependencies,
    internal calls, method prefixes, and percentile rank.

    Metrics are computed during init/sync - this is O(1) read.
    """
    raw_id = params.get("node_id")
    if not raw_id:
        return ToolResponse(success=False, error="node_id is required")
    node_id, err = resolve_or_fail(raw_id, ctx)
    if err:
        return err

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    if node.type != NodeType.CLASS:
        return ToolResponse(
            success=False,
            error=f"Node {node_id} is {node.type.value}, not a class"
        )

    analysis = get_class_analysis_from_stored(node)

    return ToolResponse(success=True, data=analysis)


def handle_project_metrics(params: dict, ctx: LensContext) -> ToolResponse:
    """Get project-wide class metrics.

    Returns total classes, avg/median/min/max methods, and percentiles (p90, p95).
    Use this to understand the distribution before interpreting individual class metrics.

    Metrics are computed during init/sync - this is O(1) read.
    """
    metrics = database.get_project_metrics(ctx.graph_db)

    if not metrics:
        return ToolResponse(
            success=False,
            error="No project metrics found. Run 'lenspr init --force' to compute."
        )

    return ToolResponse(
        success=True,
        data={
            "total_classes": metrics.get("total_classes", 0),
            "avg_methods": metrics.get("avg_methods", 0),
            "median_methods": metrics.get("median_methods", 0),
            "min_methods": metrics.get("min_methods", 0),
            "max_methods": metrics.get("max_methods", 0),
            "p90_methods": metrics.get("p90_methods", 0),
            "p95_methods": metrics.get("p95_methods", 0),
        }
    )


def handle_largest_classes(params: dict, ctx: LensContext) -> ToolResponse:
    """Get classes sorted by method count (descending).

    Returns the N largest classes with their metrics.
    Use this to identify potentially complex classes for review.

    Args:
        limit: Max classes to return (default 10)
    """
    limit = params.get("limit", 10)

    nodes, _ = database.load_graph(ctx.graph_db)

    # Filter to classes with metrics
    classes_with_metrics = [
        n for n in nodes
        if n.type == NodeType.CLASS and n.metrics and "method_count" in n.metrics
    ]

    # Sort by method count descending
    sorted_classes = sorted(
        classes_with_metrics,
        key=lambda n: n.metrics.get("method_count", 0),
        reverse=True
    )[:limit]

    return ToolResponse(
        success=True,
        data={
            "classes": [
                {
                    "node_id": n.id,
                    "name": n.name,
                    "method_count": n.metrics.get("method_count", 0),
                    "lines": n.metrics.get("lines", 0),
                    "dependency_count": n.metrics.get("dependency_count", 0),
                    "percentile_rank": n.metrics.get("percentile_rank", 0),
                }
                for n in sorted_classes
            ],
            "count": len(sorted_classes),
        }
    )


def handle_compare_classes(params: dict, ctx: LensContext) -> ToolResponse:
    """Compare metrics between multiple classes.

    Args:
        node_ids: List of class node IDs to compare

    Returns metrics side-by-side for easy comparison.
    """
    node_ids = params.get("node_ids", [])
    if not node_ids or len(node_ids) < 2:
        return ToolResponse(
            success=False,
            error="At least 2 node_ids required for comparison"
        )

    comparisons = []
    for node_id in node_ids:
        node = database.get_node(node_id, ctx.graph_db)
        if not node:
            comparisons.append({"node_id": node_id, "error": "Not found"})
            continue
        if node.type != NodeType.CLASS:
            comparisons.append({
                "node_id": node_id,
                "error": f"Not a class ({node.type.value})"
            })
            continue

        analysis = get_class_analysis_from_stored(node)
        comparisons.append(analysis)

    return ToolResponse(success=True, data={"comparisons": comparisons})


def handle_components(params: dict, ctx: LensContext) -> ToolResponse:
    """Analyze components (directory-based modules) with cohesion metrics.

    Components are directories containing related code. Returns:
    - Cohesion score (internal edges / total edges)
    - Public API nodes (called from outside)
    - Internal nodes (only used within component)

    Args:
        mode: "summary" (default) for counts only, "full" for complete node lists.
        component: Drill into a specific component by ID to see its full node lists.
    """
    min_cohesion = params.get("min_cohesion", 0.0)
    path_prefix = params.get("path")
    mode = params.get("mode", "summary")
    component_filter = params.get("component")

    nodes, edges = database.load_graph(ctx.graph_db)
    project_root = Path(ctx.project_root)

    components = detect_components(nodes, edges, project_root)

    # Filter
    if path_prefix:
        components = [c for c in components if c.path.startswith(path_prefix)]
    components = [c for c in components if c.cohesion >= min_cohesion]

    # Drill-down: single component with full lists
    if component_filter:
        match = [c for c in components if c.id == component_filter]
        if not match:
            return ToolResponse(
                success=False,
                error=f"Component '{component_filter}' not found.",
                hint="Use lens_components() to see all component IDs.",
            )
        c = match[0]
        return ToolResponse(
            success=True,
            data={
                "id": c.id,
                "name": c.name,
                "path": c.path,
                "cohesion": c.cohesion,
                "modules": c.modules,
                "classes": c.classes,
                "public_api": c.public_api,
                "internal_nodes": c.internal_nodes,
                "internal_edges": c.internal_edges,
                "external_edges": c.external_edges,
            },
        )

    # Sort by cohesion descending
    components = sorted(components, key=lambda c: -c.cohesion)

    avg_cohesion = (
        round(sum(c.cohesion for c in components) / len(components), 2)
        if components
        else 0
    )

    if mode == "summary":
        return ToolResponse(
            success=True,
            data={
                "components": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "path": c.path,
                        "cohesion": c.cohesion,
                        "module_count": len(c.modules),
                        "class_count": len(c.classes),
                        "public_api_count": len(c.public_api),
                        "internal_count": len(c.internal_nodes),
                        "internal_edges": c.internal_edges,
                        "external_edges": c.external_edges,
                    }
                    for c in components
                ],
                "count": len(components),
                "avg_cohesion": avg_cohesion,
                "hint": (
                    "Use mode='full' for complete node lists,"
                    " or component='<id>' to drill into one."
                ),
            },
        )

    # mode == "full" — original behavior
    return ToolResponse(
        success=True,
        data={
            "components": [
                {
                    "id": c.id,
                    "name": c.name,
                    "path": c.path,
                    "cohesion": c.cohesion,
                    "modules": c.modules,
                    "classes": c.classes,
                    "public_api": c.public_api,
                    "internal_nodes": c.internal_nodes,
                    "internal_edges": c.internal_edges,
                    "external_edges": c.external_edges,
                }
                for c in components
            ],
            "count": len(components),
            "avg_cohesion": avg_cohesion,
        },
    )
