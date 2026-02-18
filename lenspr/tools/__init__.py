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
    handle_batch_save_annotations,
    handle_save_annotation,
)
from lenspr.tools.explain import (
    handle_explain,
)
from lenspr.tools.arch import (
    handle_class_metrics,
    handle_compare_classes,
    handle_components,
    handle_largest_classes,
    handle_project_metrics,
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
    handle_patch_node,
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
from lenspr.tools.session import (
    handle_session_handoff,
    handle_session_read,
    handle_session_write,
)
from lenspr.tools.testing import (
    handle_run_tests,
)
from lenspr.tools.safety import (
    handle_nfr_check,
    handle_test_coverage,
    handle_security_scan,
    handle_dep_audit,
    handle_arch_rule_add,
    handle_arch_rule_list,
    handle_arch_rule_delete,
    handle_arch_check,
    handle_vibecheck,
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
    "handle_patch_node",
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
    "handle_batch_save_annotations",
    "handle_annotate_batch",
    "handle_annotation_stats",
    # Explain
    "handle_explain",
    # Git
    "handle_blame",
    "handle_node_history",
    "handle_commit_scope",
    "handle_recent_changes",
    # Architecture Metrics
    "handle_class_metrics",
    "handle_project_metrics",
    "handle_largest_classes",
    "handle_compare_classes",
    "handle_components",
    # Session memory
    "handle_session_write",
    "handle_session_read",
    "handle_session_handoff",
    # Testing
    "handle_run_tests",
    # Safety
    "handle_nfr_check",
    "handle_test_coverage",
    "handle_security_scan",
    "handle_dep_audit",
    "handle_arch_rule_add",
    "handle_arch_rule_list",
    "handle_arch_rule_delete",
    "handle_arch_check",
    "handle_vibecheck",
    "handle_fix_plan",
    "handle_generate_test_skeleton",
]


# Tool name to handler mapping (module_name, function_name)
# Using strings allows hot-reload - we resolve at call time
_HANDLER_MAP: dict[str, tuple[str, str]] = {
    "lens_list_nodes": ("lenspr.tools.navigation", "handle_list_nodes"),
    "lens_get_node": ("lenspr.tools.navigation", "handle_get_node"),
    "lens_get_connections": ("lenspr.tools.navigation", "handle_get_connections"),
    "lens_check_impact": ("lenspr.tools.analysis", "handle_check_impact"),
    "lens_update_node": ("lenspr.tools.modification", "handle_update_node"),
    "lens_patch_node": ("lenspr.tools.modification", "handle_patch_node"),
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
    "lens_batch_save_annotations": ("lenspr.tools.annotation", "handle_batch_save_annotations"),
    "lens_annotate_batch": ("lenspr.tools.annotation", "handle_annotate_batch"),
    "lens_annotation_stats": ("lenspr.tools.annotation", "handle_annotation_stats"),
    # Git integration
    "lens_blame": ("lenspr.tools.git", "handle_blame"),
    "lens_node_history": ("lenspr.tools.git", "handle_node_history"),
    "lens_commit_scope": ("lenspr.tools.git", "handle_commit_scope"),
    "lens_recent_changes": ("lenspr.tools.git", "handle_recent_changes"),
    # Explain
    "lens_explain": ("lenspr.tools.explain", "handle_explain"),
    # Architecture Metrics
    "lens_class_metrics": ("lenspr.tools.arch", "handle_class_metrics"),
    "lens_project_metrics": ("lenspr.tools.arch", "handle_project_metrics"),
    "lens_largest_classes": ("lenspr.tools.arch", "handle_largest_classes"),
    "lens_compare_classes": ("lenspr.tools.arch", "handle_compare_classes"),
    "lens_components": ("lenspr.tools.arch", "handle_components"),
    # Session memory
    "lens_session_write": ("lenspr.tools.session", "handle_session_write"),
    "lens_session_read": ("lenspr.tools.session", "handle_session_read"),
    "lens_session_handoff": ("lenspr.tools.session", "handle_session_handoff"),
    # Testing
    "lens_run_tests": ("lenspr.tools.testing", "handle_run_tests"),
    # Safety
    "lens_nfr_check": ("lenspr.tools.safety", "handle_nfr_check"),
    "lens_test_coverage": ("lenspr.tools.safety", "handle_test_coverage"),
    "lens_security_scan": ("lenspr.tools.safety", "handle_security_scan"),
    "lens_dep_audit": ("lenspr.tools.safety", "handle_dep_audit"),
    "lens_arch_rule_add": ("lenspr.tools.safety", "handle_arch_rule_add"),
    "lens_arch_rule_list": ("lenspr.tools.safety", "handle_arch_rule_list"),
    "lens_arch_rule_delete": ("lenspr.tools.safety", "handle_arch_rule_delete"),
    "lens_arch_check": ("lenspr.tools.safety", "handle_arch_check"),
    "lens_vibecheck": ("lenspr.tools.safety", "handle_vibecheck"),
    "lens_fix_plan": ("lenspr.tools.safety", "handle_fix_plan"),
    "lens_generate_test_skeleton": ("lenspr.tools.safety", "handle_generate_test_skeleton"),
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
    """Get the handler function for a tool.

    Fast path: static registry (_HANDLER_MAP).
    Slow path: auto-discovery by convention — converts 'lens_fix_plan'
    to 'handle_fix_plan' and searches all tools submodules. Caches the
    result in _HANDLER_MAP so discovery only runs once per tool.

    This means new tools added via lens_add_node work without restarting
    the server — no importlib.reload() overhead on each call.
    """
    import importlib

    # Fast path: known tool in static registry
    if tool_name in _HANDLER_MAP:
        import sys
        module_name, func_name = _HANDLER_MAP[tool_name]
        if _hot_reload_enabled:
            if module_name in sys.modules:
                module = importlib.reload(sys.modules[module_name])
            else:
                module = importlib.import_module(module_name)
        else:
            module = importlib.import_module(module_name)
        handler = getattr(module, func_name, None)
        if handler is not None:
            return handler  # type: ignore[return-value]
        # Stale sys.modules cache — function added after process start.
        # Reload once to pick up the new definition.
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
            handler = getattr(module, func_name, None)
            if handler is not None:
                return handler  # type: ignore[return-value]
        # Still missing — fall through to slow-path discovery

    # Slow path: auto-discovery by naming convention
    # "lens_fix_plan" → look for "handle_fix_plan" in each submodule
    if not tool_name.startswith("lens_"):
        return None
    func_name = "handle_" + tool_name[len("lens_"):]

    _DISCOVERY_ORDER = [
        "lenspr.tools.safety",
        "lenspr.tools.modification",
        "lenspr.tools.navigation",
        "lenspr.tools.analysis",
        "lenspr.tools.annotation",
        "lenspr.tools.git",
        "lenspr.tools.arch",
        "lenspr.tools.explain",
        "lenspr.tools.session",
        "lenspr.tools.testing",
        "lenspr.tools.helpers",
        "lenspr.tools.patterns",
    ]
    for module_name in _DISCOVERY_ORDER:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        handler = getattr(module, func_name, None)
        if handler is not None:
            # Cache so this only runs once
            _HANDLER_MAP[tool_name] = (module_name, func_name)
            return handler  # type: ignore[return-value]

    return None


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
