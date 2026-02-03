"""LensPR tool handlers for Claude API integration."""

from __future__ import annotations

from collections.abc import Callable
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
from lenspr.tools.explain import (
    handle_explain,
)
from lenspr.tools.git import (
    handle_blame,
    handle_commit_scope,
    handle_node_history,
    handle_recent_changes,
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
    "enable_hot_reload",
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
    # Explain
    "handle_explain",
    # Git
    "handle_blame",
    "handle_node_history",
    "handle_commit_scope",
    "handle_recent_changes",
]


# Tool name to handler mapping (module_name, function_name)
# Using strings allows hot-reload - we resolve at call time
_HANDLER_MAP: dict[str, tuple[str, str]] = {
    "lens_list_nodes": ("lenspr.tools.navigation", "handle_list_nodes"),
    "lens_get_node": ("lenspr.tools.navigation", "handle_get_node"),
    "lens_get_connections": ("lenspr.tools.navigation", "handle_get_connections"),
    "lens_check_impact": ("lenspr.tools.analysis", "handle_check_impact"),
    "lens_update_node": ("lenspr.tools.modification", "handle_update_node"),
    "lens_validate_change": ("lenspr.tools.analysis", "handle_validate_change"),
    "lens_add_node": ("lenspr.tools.modification", "handle_add_node"),
    "lens_delete_node": ("lenspr.tools.modification", "handle_delete_node"),
    "lens_search": ("lenspr.tools.navigation", "handle_search"),
    "lens_get_structure": ("lenspr.tools.navigation", "handle_get_structure"),
    "lens_rename": ("lenspr.tools.modification", "handle_rename"),
    "lens_context": ("lenspr.tools.navigation", "handle_context"),
    "lens_grep": ("lenspr.tools.navigation", "handle_grep"),
    "lens_diff": ("lenspr.tools.analysis", "handle_diff"),
    "lens_batch": ("lenspr.tools.modification", "handle_batch"),
    "lens_health": ("lenspr.tools.analysis", "handle_health"),
    "lens_dependencies": ("lenspr.tools.analysis", "handle_dependencies"),
    "lens_dead_code": ("lenspr.tools.analysis", "handle_dead_code"),
    "lens_find_usages": ("lenspr.tools.analysis", "handle_find_usages"),
    "lens_annotate": ("lenspr.tools.annotation", "handle_annotate"),
    "lens_save_annotation": ("lenspr.tools.annotation", "handle_save_annotation"),
    "lens_annotate_batch": ("lenspr.tools.annotation", "handle_annotate_batch"),
    "lens_annotation_stats": ("lenspr.tools.annotation", "handle_annotation_stats"),
    # Git integration
    "lens_blame": ("lenspr.tools.git", "handle_blame"),
    "lens_node_history": ("lenspr.tools.git", "handle_node_history"),
    "lens_commit_scope": ("lenspr.tools.git", "handle_commit_scope"),
    "lens_recent_changes": ("lenspr.tools.git", "handle_recent_changes"),
    # Explain
    "lens_explain": ("lenspr.tools.explain", "handle_explain"),
}

# Hot-reload mode: when True, handlers are resolved dynamically each call
_hot_reload_enabled: bool = False


def enable_hot_reload(enabled: bool = True) -> None:
    """Enable or disable hot-reload mode.

    When enabled, handler functions are resolved dynamically on each call,
    allowing code changes to be picked up without restarting.
    """
    global _hot_reload_enabled
    _hot_reload_enabled = enabled


def _get_handler(
    tool_name: str,
) -> Callable[[dict, LensContext], ToolResponse] | None:
    """Get the handler function for a tool, with hot-reload support."""
    if tool_name not in _HANDLER_MAP:
        return None

    module_name, func_name = _HANDLER_MAP[tool_name]

    if _hot_reload_enabled:
        # Dynamic resolution - always get fresh function
        import importlib
        import sys

        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
        else:
            module = importlib.import_module(module_name)
        handler: Callable[[dict, LensContext], ToolResponse] = getattr(module, func_name)
        return handler
    else:
        # Use pre-imported handlers (faster)
        handler_maybe = globals().get(func_name)
        return handler_maybe  # type: ignore[return-value]


def handle_tool_call(
    tool_name: str, parameters: dict, ctx: LensContext
) -> ToolResponse:
    """Route a tool call to the appropriate handler."""
    handler = _get_handler(tool_name)
    if not handler:
        return ToolResponse(success=False, error=f"Unknown tool: {tool_name}")

    try:
        return handler(parameters, ctx)
    except Exception as e:
        return ToolResponse(success=False, error=str(e))
