"""Shared helper functions for tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lenspr import database, graph

if TYPE_CHECKING:
    from lenspr.context import LensContext


def find_containing_node(
    nx_graph: Any, file_path: str, line_num: int
) -> dict[str, str] | None:
    """Find the most specific graph node containing a given line."""
    best: dict[str, Any] | None = None
    best_span = float("inf")

    for nid, data in nx_graph.nodes(data=True):
        if data.get("file_path") != file_path:
            continue
        start = data.get("start_line", 0)
        end = data.get("end_line", 0)
        if start <= line_num <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best = {
                    "id": nid,
                    "name": data.get("name", ""),
                    "type": data.get("type", ""),
                }

    return best


def get_proactive_warnings(
    node_id: str, new_source: str, ctx: LensContext
) -> list[str]:
    """
    Generate proactive warnings before applying a change.

    Warnings:
    - high_impact: If node has >10 direct callers
    - no_tests: If no test functions call this node
    - signature_change: If function signature differs (detected by validator)
    - circular_dependency: If node is part of a circular import
    """
    warnings: list[str] = []
    nx_graph = ctx.get_graph()

    if node_id not in nx_graph:
        return warnings

    # 1. High impact warning (>10 callers)
    direct_callers = list(nx_graph.predecessors(node_id))
    caller_count = len(direct_callers)
    if caller_count > 10:
        warnings.append(
            f"⚠️ HIGH IMPACT: This node has {caller_count} direct callers. "
            "Changes may affect many parts of the codebase."
        )
    elif caller_count > 5:
        warnings.append(
            f"⚠️ MODERATE IMPACT: This node has {caller_count} direct callers."
        )

    # 2. No tests warning
    has_tests = False
    node_data = nx_graph.nodes.get(node_id, {})
    node_name = node_data.get("name", "")

    for pred_id in direct_callers:
        pred_data = nx_graph.nodes.get(pred_id, {})
        pred_name = pred_data.get("name", "")
        pred_file = pred_data.get("file_path", "")
        if pred_name.startswith("test_") or "test_" in pred_file:
            has_tests = True
            break

    # Also check by naming convention
    if not has_tests:
        test_nodes = database.search_nodes(
            f"test_{node_name}", ctx.graph_db, search_in="name"
        )
        has_tests = len(test_nodes) > 0

    if not has_tests:
        warnings.append(
            "⚠️ NO TESTS: No test functions found for this node. "
            "Consider adding tests before modifying."
        )

    # 3. Circular import warning
    cycles = graph.detect_circular_imports(nx_graph)
    node_module = node_id.rsplit(".", 1)[0] if "." in node_id else node_id
    for cycle in cycles:
        if node_module in cycle:
            warnings.append(
                f"⚠️ CIRCULAR DEPENDENCY: This node is part of a circular import: "
                f"{' → '.join(cycle)}"
            )
            break

    return warnings
