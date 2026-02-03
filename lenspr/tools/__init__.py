"""LensPR tool handlers for Claude API integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lenspr.models import ToolResponse
from lenspr.tools.analysis import (
    handle_check_impact,
    handle_dead_code,
    handle_dependencies,
    handle_diff,
    handle_find_usages,
    handle_health,
    handle_validate_change,
)
from lenspr.tools.annotation import (
    handle_annotate,
    handle_annotate_batch,
    handle_annotation_stats,
    handle_save_annotation,
)
from lenspr.tools.modification import (
    handle_add_node,
    handle_batch,
    handle_delete_node,
    handle_rename,
    handle_update_node,
)
from lenspr.tools.navigation import (
    handle_context,
    handle_get_connections,
    handle_get_node,
    handle_get_structure,
    handle_grep,
    handle_list_nodes,
    handle_search,
)
from lenspr.tools.schemas import LENS_TOOLS

if TYPE_CHECKING:
    from lenspr.context import LensContext

__all__ = [
    "LENS_TOOLS",
    "handle_tool_call",
    # Navigation
    "handle_list_nodes",
    "handle_get_node",
    "handle_get_connections",
    "handle_search",
    "handle_get_structure",
    "handle_context",
    "handle_grep",
    # Modification
    "handle_update_node",
    "handle_add_node",
    "handle_delete_node",
    "handle_rename",
    "handle_batch",
    # Analysis
    "handle_check_impact",
    "handle_validate_change",
    "handle_diff",
    "handle_health",
    "handle_dependencies",
    "handle_dead_code",
    "handle_find_usages",
    # Annotation
    "handle_annotate",
    "handle_save_annotation",
    "handle_annotate_batch",
    "handle_annotation_stats",
]


# Tool name to handler mapping
_HANDLERS = {
    "lens_list_nodes": handle_list_nodes,
    "lens_get_node": handle_get_node,
    "lens_get_connections": handle_get_connections,
    "lens_check_impact": handle_check_impact,
    "lens_update_node": handle_update_node,
    "lens_validate_change": handle_validate_change,
    "lens_add_node": handle_add_node,
    "lens_delete_node": handle_delete_node,
    "lens_search": handle_search,
    "lens_get_structure": handle_get_structure,
    "lens_rename": handle_rename,
    "lens_context": handle_context,
    "lens_grep": handle_grep,
    "lens_diff": handle_diff,
    "lens_batch": handle_batch,
    "lens_health": handle_health,
    "lens_dependencies": handle_dependencies,
    "lens_dead_code": handle_dead_code,
    "lens_find_usages": handle_find_usages,
    "lens_annotate": handle_annotate,
    "lens_save_annotation": handle_save_annotation,
    "lens_annotate_batch": handle_annotate_batch,
    "lens_annotation_stats": handle_annotation_stats,
}


def handle_tool_call(
    tool_name: str, parameters: dict, ctx: LensContext
) -> ToolResponse:
    """Route a tool call to the appropriate handler."""
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return ToolResponse(success=False, error=f"Unknown tool: {tool_name}")

    try:
        return handler(parameters, ctx)
    except Exception as e:
        return ToolResponse(success=False, error=str(e))
