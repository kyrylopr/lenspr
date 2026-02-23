"""Shared helper functions for tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lenspr import database, graph
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext


def resolve_or_fail(
    node_id: str, ctx: LensContext,
) -> tuple[str, ToolResponse | None]:
    """Resolve a possibly-fuzzy node_id to an exact ID.

    Returns:
        (resolved_id, None)              — success
        (original_id, ToolResponse)      — failure (not found or ambiguous)
    """
    resolved, suggestions = database.resolve_node_id(node_id, ctx.graph_db)
    if resolved:
        return resolved, None
    if suggestions:
        hint_lines = "\n".join(f"  • {s}" for s in suggestions)
        return node_id, ToolResponse(
            success=False,
            error=(
                f"Ambiguous node_id '{node_id}'. Did you mean one of:\n{hint_lines}\n"
                "Provide a more specific identifier."
            ),
        )
    return node_id, ToolResponse(
        success=False,
        error=(
            f"Node not found: '{node_id}'. "
            "Use lens_search or lens_list_nodes to find valid node IDs."
        ),
        hint="Use lens_search or lens_list_nodes to find valid node IDs.",
    )


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
    - circular_dependency: If node is part of a circular import
    - hardcoded_secret: If new_source contains suspicious credential patterns
    - io_without_error_handling: If new_source has IO/network calls but no try/except
    - arch_violation: If change would violate architecture rules
    """
    import re

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

    # 4. Hardcoded secrets detection
    _SECRET_PATTERNS = [
        (r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{3,}["\']', "hardcoded password"),
        (r'(?i)(api_key|apikey|secret_key)\s*=\s*["\'][^"\']{6,}["\']', "hardcoded API key"),
        (r'(?i)(token)\s*=\s*["\'][^"\']{8,}["\']', "hardcoded token"),
        (r'(?i)(secret)\s*=\s*["\'][^"\']{6,}["\']', "hardcoded secret"),
    ]
    for pattern, label in _SECRET_PATTERNS:
        if re.search(pattern, new_source):
            warnings.append(
                f"🔐 HARDCODED SECRET: Possible {label} detected. "
                "Use environment variables or a secrets manager instead."
            )
            break  # One warning is enough

    # 5. IO/network operations without error handling
    _IO_MARKERS = [
        "open(", "requests.", "httpx.", "aiohttp.",
        ".execute(", ".query(", ".fetchone(", ".fetchall(",
        "subprocess.", "socket.", "urllib.",
    ]
    has_io = any(marker in new_source for marker in _IO_MARKERS)
    has_error_handling = "try:" in new_source and "except" in new_source
    if has_io and not has_error_handling:
        warnings.append(
            "⚠️ NO ERROR HANDLING: This code performs IO/network/DB operations "
            "without try/except. Consider wrapping in try/except."
        )

    # 6. Architecture rules check (non-blocking, warns only)
    try:
        from lenspr.tools.safety import check_arch_violations
        violations = check_arch_violations(node_id, ctx)
        for v in violations:
            warnings.append(f"🏛️ ARCH VIOLATION: {v}")
    except Exception:
        pass  # Never block on arch check failure

    return warnings
