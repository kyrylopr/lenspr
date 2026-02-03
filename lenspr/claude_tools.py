"""Tool definitions and handlers for Claude integration.

This module re-exports from lenspr.tools for backwards compatibility.
The actual implementations are in lenspr/tools/.
"""

from lenspr.tools import LENS_TOOLS, handle_tool_call

# Re-export handlers with old names for backwards compatibility
from lenspr.tools.analysis import (
    handle_check_impact as _handle_check_impact,
    handle_dead_code as _handle_dead_code,
    handle_dependencies as _handle_dependencies,
    handle_diff as _handle_diff,
    handle_find_usages as _handle_find_usages,
    handle_health as _handle_health,
    handle_validate_change as _handle_validate_change,
)
from lenspr.tools.annotation import (
    handle_annotate as _handle_annotate,
    handle_annotate_batch as _handle_annotate_batch,
    handle_annotation_stats as _handle_annotation_stats,
    handle_save_annotation as _handle_save_annotation,
)
from lenspr.tools.modification import (
    handle_add_node as _handle_add_node,
    handle_batch as _handle_batch,
    handle_delete_node as _handle_delete_node,
    handle_rename as _handle_rename,
    handle_update_node as _handle_update_node,
)
from lenspr.tools.navigation import (
    handle_context as _handle_context,
    handle_get_connections as _handle_get_connections,
    handle_get_node as _handle_get_node,
    handle_get_structure as _handle_get_structure,
    handle_grep as _handle_grep,
    handle_list_nodes as _handle_list_nodes,
    handle_search as _handle_search,
)

__all__ = [
    "LENS_TOOLS",
    "handle_tool_call",
    # Legacy names for backwards compatibility
    "_handle_list_nodes",
    "_handle_get_node",
    "_handle_get_connections",
    "_handle_search",
    "_handle_get_structure",
    "_handle_context",
    "_handle_grep",
    "_handle_update_node",
    "_handle_add_node",
    "_handle_delete_node",
    "_handle_rename",
    "_handle_batch",
    "_handle_check_impact",
    "_handle_validate_change",
    "_handle_diff",
    "_handle_health",
    "_handle_dependencies",
    "_handle_dead_code",
    "_handle_find_usages",
    "_handle_annotate",
    "_handle_save_annotation",
    "_handle_annotate_batch",
    "_handle_annotation_stats",
]
