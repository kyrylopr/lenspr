"""Tool group definitions for selective tool registration.

Users can enable/disable groups via .lens/config.json or `lenspr tools` CLI.
The MCP server only registers tools from enabled groups, reducing context
window usage for the AI assistant.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Group registry — single source of truth
# ---------------------------------------------------------------------------

TOOL_GROUPS: dict[str, dict] = {
    "core": {
        "description": "Navigation & search — browse the graph, find functions, read code",
        "tools": [
            "lens_list_nodes",
            "lens_get_node",
            "lens_get_connections",
            "lens_search",
            "lens_get_structure",
            "lens_context",
            "lens_grep",
        ],
    },
    "modification": {
        "description": "Code changes — update, patch, add, delete, rename functions",
        "tools": [
            "lens_update_node",
            "lens_patch_node",
            "lens_add_node",
            "lens_delete_node",
            "lens_rename",
            "lens_batch",
        ],
    },
    "analysis": {
        "description": "Impact analysis — check what breaks before making changes",
        "tools": [
            "lens_check_impact",
            "lens_validate_change",
            "lens_diff",
            "lens_health",
            "lens_dependencies",
            "lens_dead_code",
            "lens_find_usages",
        ],
    },
    "quality": {
        "description": "Vibecoding safety — health score, NFR checks, test coverage, security",
        "tools": [
            "lens_vibecheck",
            "lens_nfr_check",
            "lens_test_coverage",
            "lens_security_scan",
            "lens_dep_audit",
            "lens_fix_plan",
            "lens_generate_test_skeleton",
            "lens_run_tests",
        ],
    },
    "architecture": {
        "description": "Architecture rules & metrics — enforce boundaries, class analysis",
        "tools": [
            "lens_arch_rule_add",
            "lens_arch_rule_list",
            "lens_arch_rule_delete",
            "lens_arch_check",
            "lens_class_metrics",
            "lens_project_metrics",
            "lens_largest_classes",
            "lens_compare_classes",
            "lens_components",
        ],
    },
    "git": {
        "description": "Git integration — blame, history, commit scope at function level",
        "tools": [
            "lens_blame",
            "lens_node_history",
            "lens_commit_scope",
            "lens_recent_changes",
        ],
    },
    "annotations": {
        "description": "Semantic annotations — summaries, roles, side effects for nodes",
        "tools": [
            "lens_annotate",
            "lens_save_annotation",
            "lens_batch_save_annotations",
            "lens_annotate_batch",
            "lens_annotation_stats",
        ],
    },
    "session": {
        "description": "Session memory — persistent notes that survive context resets",
        "tools": [
            "lens_session_write",
            "lens_session_read",
            "lens_session_handoff",
            "lens_resume",
        ],
    },
    "infrastructure": {
        "description": "Cross-language mappers — API routes, DB tables, env vars, Docker, FFI",
        "tools": [
            "lens_api_map",
            "lens_db_map",
            "lens_env_map",
            "lens_ffi_map",
            "lens_infra_map",
        ],
    },
    "temporal": {
        "description": "Temporal analysis — change hotspots, unified timelines",
        "tools": [
            "lens_hotspots",
            "lens_node_timeline",
        ],
    },
    "tracing": {
        "description": "Runtime call tracing — merge actual runtime edges into static graph",
        "tools": [
            "lens_trace",
            "lens_trace_stats",
        ],
    },
    "explain": {
        "description": "Code explanation — human-readable analysis with usage examples",
        "tools": [
            "lens_explain",
        ],
    },
}

# "core" is always enabled and cannot be disabled
ALWAYS_ON: set[str] = {"core"}

# All group names for convenience
ALL_GROUPS: list[str] = list(TOOL_GROUPS.keys())


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_enabled_tools(enabled_groups: list[str] | None = None) -> set[str]:
    """Return the set of tool names that should be registered.

    If enabled_groups is None, all groups are enabled (backward compat).
    "core" is always included regardless of config.
    """
    if enabled_groups is None:
        enabled_groups = ALL_GROUPS

    groups = set(enabled_groups) | ALWAYS_ON

    tools: set[str] = set()
    for group_name in groups:
        group = TOOL_GROUPS.get(group_name)
        if group:
            tools.update(group["tools"])
    return tools


def get_all_tool_names() -> set[str]:
    """Return the set of all known tool names across all groups."""
    tools: set[str] = set()
    for group in TOOL_GROUPS.values():
        tools.update(group["tools"])
    return tools


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def load_tool_config(config_path: Path) -> list[str] | None:
    """Load enabled tool groups from config.json.

    Returns None if no tool_groups config exists (= all enabled).
    """
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    tool_config = config.get("tool_groups")
    if tool_config is None:
        return None  # All groups enabled (backward compat)

    return tool_config.get("enabled", ALL_GROUPS)


def save_tool_config(config_path: Path, enabled_groups: list[str]) -> None:
    """Save tool groups config to config.json."""
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}

    all_group_names = set(TOOL_GROUPS.keys())
    enabled_set = set(enabled_groups) | ALWAYS_ON
    disabled = sorted(all_group_names - enabled_set)

    config["tool_groups"] = {
        "enabled": sorted(enabled_set),
        "disabled": disabled,
    }
    text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    config_path.write_text(text, encoding="utf-8")
