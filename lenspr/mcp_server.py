"""MCP server for LensPR — exposes code graph tools over Model Context Protocol."""

from __future__ import annotations

import importlib
import json
import logging
import sys
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("lenspr.mcp")

# Global queue of nodes needing annotation (populated by auto-sync)
_pending_annotation_nodes: list[dict] = []
_pending_lock = threading.Lock()


def _add_pending_annotations(nodes: list) -> None:
    """Add nodes to the pending annotation queue."""
    global _pending_annotation_nodes
    with _pending_lock:
        for node in nodes:
            if hasattr(node, "type") and node.type.value in ("function", "method", "class"):
                _pending_annotation_nodes.append({
                    "id": node.id,
                    "name": node.name,
                    "type": node.type.value,
                    "file_path": node.file_path,
                })


def _get_and_clear_pending() -> list[dict]:
    """Get pending annotations and clear the queue."""
    global _pending_annotation_nodes
    with _pending_lock:
        pending = _pending_annotation_nodes.copy()
        _pending_annotation_nodes = []
        return pending


def _wrap_result_with_pending(result: str) -> str:
    """Wrap tool result with pending annotation hint if any."""
    pending = _get_and_clear_pending()
    if not pending:
        return result

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return result

    # Add pending annotations to response
    node_ids = [n["id"] for n in pending[:10]]  # Limit to 10
    remaining = len(pending) - 10 if len(pending) > 10 else 0

    data["_pending_annotations"] = {
        "count": len(pending),
        "nodes": pending[:10],
        "hint": (
            f"ACTION REQUIRED: {len(pending)} node(s) were added/modified and need annotation. "
            f"Please analyze: {', '.join(node_ids)}"
            + (f" (+{remaining} more)" if remaining > 0 else "")
            + ". Call lens_batch_save_annotations([{node_id, summary}, ...]). "
            "Only provide summary (1-2 sentences) - role/side_effects are auto-detected."
        ),
    }

    return json.dumps(data, indent=2)


def _tool_result(tool_name: str, params: dict) -> str:
    """Execute tool and wrap result with pending annotations."""
    import lenspr
    result = lenspr.handle_tool(tool_name, params)
    return _wrap_result_with_pending(json.dumps(result, indent=2))

# Modules to reload when hot-reload is triggered (in dependency order)
_LENSPR_MODULES = [
    "lenspr.models",
    "lenspr.database",
    "lenspr.graph",
    "lenspr.patcher",
    "lenspr.tools.helpers",
    "lenspr.tools.schemas",
    "lenspr.tools.navigation",
    "lenspr.tools.modification",
    "lenspr.tools.analysis",
    "lenspr.tools.annotation",
    "lenspr.tools.explain",
    "lenspr.tools.git",
    "lenspr.tools.arch",
    "lenspr.tools",
    "lenspr.claude_tools",
    "lenspr.context",
]


def _reload_lenspr_modules() -> int:
    """Reload all lenspr tool modules to pick up code changes.

    Returns the number of modules successfully reloaded.
    """
    reloaded = 0
    for module_name in _LENSPR_MODULES:
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                reloaded += 1
            except Exception as e:
                logger.warning("Failed to reload %s: %s", module_name, e)
    return reloaded


def _is_lenspr_file(file_path: str) -> bool:
    """Check if a file path belongs to lenspr package."""
    path = Path(file_path)
    return "lenspr" in path.parts and file_path.endswith(".py")


def _is_tracked_file(file_path: str) -> bool:
    """Check if a file should be tracked by the graph."""
    from lenspr.parsers import is_supported_file

    # Skip common non-project directories
    skip_parts = {
        "node_modules", "__pycache__", ".git", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
        ".next", ".nuxt", "coverage", ".lens",
    }
    path = Path(file_path)
    if any(part in skip_parts for part in path.parts):
        return False

    return is_supported_file(file_path)


def _start_watcher(project_path: str, hot_reload: bool = False) -> None:
    """Start a background file watcher that auto-syncs on changes.

    Uses watchdog if available, otherwise falls back to a simple
    polling watcher.

    Args:
        project_path: Path to watch for changes.
        hot_reload: If True, reload lenspr modules when they change.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        _start_watchdog_watcher(project_path, FileSystemEventHandler, Observer, hot_reload)  # type: ignore[arg-type]
    except ImportError:
        logger.info("watchdog not installed, using polling watcher")
        _start_polling_watcher(project_path, hot_reload)


def _start_watchdog_watcher(
    project_path: str,
    handler_cls: type,
    observer_cls: type,
    hot_reload: bool = False,
) -> None:
    """Watchdog-based file watcher running in a daemon thread."""
    import lenspr

    # Debounce settings
    poll_interval_ms = 50  # Check every 50ms
    debounce_ms = 200  # Wait 200ms of no changes before syncing

    class _SyncHandler(handler_cls):  # type: ignore[misc]
        def __init__(self) -> None:
            self._pending_sync = False
            self._pending_reload = False
            self._changed_files: set[str] = set()
            self._last_change_time: float = 0
            self._lock = threading.Lock()

        def on_modified(self, event: object) -> None:
            if hasattr(event, "src_path"):
                src_path = event.src_path  # type: ignore[union-attr]
                if _is_tracked_file(src_path):
                    with self._lock:
                        self._pending_sync = True
                        self._changed_files.add(src_path)
                        self._last_change_time = time.time()
                        # Track if lenspr code changed (for hot-reload)
                        if hot_reload and _is_lenspr_file(src_path):
                            self._pending_reload = True

        def on_created(self, event: object) -> None:
            self.on_modified(event)

        def on_deleted(self, event: object) -> None:
            self.on_modified(event)

    handler = _SyncHandler()
    observer = observer_cls()
    observer.schedule(handler, project_path, recursive=True)
    observer.daemon = True
    observer.start()

    def _sync_loop() -> None:
        while True:
            time.sleep(poll_interval_ms / 1000)  # 50ms poll interval
            should_sync = False
            should_reload = False
            changed: set[str] = set()

            with handler._lock:
                if handler._pending_sync:
                    # Debounce: only sync if no changes for debounce_ms
                    time_since_last = (time.time() - handler._last_change_time) * 1000
                    if time_since_last >= debounce_ms:
                        handler._pending_sync = False
                        should_sync = True
                        changed = handler._changed_files.copy()
                        handler._changed_files.clear()
                if handler._pending_reload:
                    handler._pending_reload = False
                    should_reload = True

            # Reload lenspr modules first if needed
            if should_reload:
                try:
                    count = _reload_lenspr_modules()
                    if count > 0:
                        logger.info("Hot-reload: %d lenspr modules reloaded", count)
                except Exception:
                    logger.exception("Hot-reload failed")

            # Then sync graph
            if should_sync:
                try:
                    result = lenspr.sync()
                    total = (
                        len(result.added)
                        + len(result.modified)
                        + len(result.deleted)
                    )
                    if total > 0:
                        logger.info(
                            "Auto-sync: +%d ~%d -%d (%d files changed)",
                            len(result.added),
                            len(result.modified),
                            len(result.deleted),
                            len(changed),
                        )
                        # Queue nodes for annotation
                        _add_pending_annotations(result.added + result.modified)
                except Exception:
                    logger.exception("Auto-sync failed")

    t = threading.Thread(target=_sync_loop, daemon=True)
    t.start()
    mode = "hot-reload" if hot_reload else "standard"
    logger.info("Watchdog file watcher started (%s mode) for: %s", mode, project_path)


def _start_polling_watcher(project_path: str, hot_reload: bool = False) -> None:
    """Simple polling watcher as fallback when watchdog is unavailable."""
    import lenspr

    def _poll_loop() -> None:
        while True:
            time.sleep(1)  # Poll every 1 second (reduced from 3s)
            try:
                # Hot-reload lenspr modules if enabled
                if hot_reload:
                    count = _reload_lenspr_modules()
                    if count > 0:
                        logger.debug("Hot-reload: %d modules reloaded", count)

                result = lenspr.sync()
                total = (
                    len(result.added)
                    + len(result.modified)
                    + len(result.deleted)
                )
                if total > 0:
                    logger.info(
                        "Poll-sync: +%d ~%d -%d",
                        len(result.added),
                        len(result.modified),
                        len(result.deleted),
                    )
            except Exception:
                logger.exception("Poll-sync failed")

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    mode = "hot-reload" if hot_reload else "standard"
    logger.info(
        "Polling file watcher started (%s mode) for: %s (every 1s)",
        mode, project_path
    )


def run_server(project_path: str, hot_reload: bool = False) -> None:
    """Initialize LensPR and start the MCP server on stdio.

    Args:
        project_path: Path to the project to analyze.
        hot_reload: Enable hot-reload of lenspr modules (for development).
    """
    import lenspr
    from lenspr.tools import enable_hot_reload

    lenspr.init(project_path)
    instructions = lenspr.get_system_prompt()

    # Enable hot-reload in tool dispatch if requested
    if hot_reload:
        enable_hot_reload(True)
        logger.info("Hot-reload mode enabled for tool handlers")

    # Start background file watcher for auto-sync
    _start_watcher(project_path, hot_reload=hot_reload)

    mcp = FastMCP(
        name="lenspr",
        instructions=instructions,
    )

    # --- MCP Resources ---
    # These provide read-only access to graph data

    @mcp.resource("lenspr://structure")
    def get_structure_resource() -> str:
        """Get current project structure as a resource."""
        result = lenspr.handle_tool("lens_get_structure", {"mode": "summary"})
        return json.dumps(result, indent=2)

    @mcp.resource("lenspr://pagination")
    def get_pagination_info() -> str:
        """Get pagination info for large projects."""
        g = lenspr.get_context().get_graph()
        return json.dumps({
            "total_nodes": g.number_of_nodes(),
            "total_edges": g.number_of_edges(),
            "hint": "Use lens_get_structure with limit/offset for pagination",
        }, indent=2)

    @mcp.tool()
    def lens_list_nodes(
        type: str | None = None,
        file_path: str | None = None,
        name: str | None = None,
    ) -> str:
        """List all nodes in the codebase, optionally filtered by type, file, or name.

        Args:
            type: Filter by node type (module, class, function, method, block).
            file_path: Filter by file path.
            name: Filter by name (substring match, e.g. 'parse' finds 'parse_file').
        """
        params: dict = {}
        if type is not None:
            params["type"] = type
        if file_path is not None:
            params["file_path"] = file_path
        if name is not None:
            params["name"] = name
        return _tool_result("lens_list_nodes", params)

    @mcp.tool()
    def lens_get_node(node_id: str) -> str:
        """Get full details of a specific node including its source code.

        Args:
            node_id: The node identifier (e.g. app.models.User).
        """
        return _tool_result("lens_get_node", {"node_id": node_id})

    @mcp.tool()
    def lens_get_connections(
        node_id: str,
        direction: str = "both",
    ) -> str:
        """Get all connections (edges) for a node — what it calls and what calls it.

        Args:
            node_id: The node identifier.
            direction: Direction of edges: incoming, outgoing, or both.
        """
        result = lenspr.handle_tool("lens_get_connections", {
            "node_id": node_id,
            "direction": direction,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_check_impact(
        node_id: str,
        depth: int = 2,
    ) -> str:
        """Analyze what would be affected by changing a node. ALWAYS call before modifying code.

        Args:
            node_id: The node identifier.
            depth: How many levels of dependents to traverse.
        """
        return _tool_result("lens_check_impact", {
            "node_id": node_id,
            "depth": depth,
        })

    @mcp.tool()
    def lens_update_node(
        node_id: str,
        new_source: str,
        reasoning: str = "",
    ) -> str:
        """Update the source code of a node. Validates syntax and structure before applying.

        Args:
            node_id: The node identifier.
            new_source: The new source code for the node.
            reasoning: Why this change is being made. Stored in history for future sessions.
        """
        result = lenspr.handle_tool("lens_update_node", {
            "node_id": node_id,
            "new_source": new_source,
            "reasoning": reasoning,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_patch_node(
        node_id: str,
        old_fragment: str,
        new_fragment: str,
        reasoning: str = "",
    ) -> str:
        """Surgical find/replace within a node's source code.

        Args:
            node_id: The node identifier.
            old_fragment: Exact text to find in the node's source (must appear exactly once).
            new_fragment: Replacement text for the matched fragment.
            reasoning: Why this change is being made. Stored in history for future sessions.
        """
        result = lenspr.handle_tool("lens_patch_node", {
            "node_id": node_id,
            "old_fragment": old_fragment,
            "new_fragment": new_fragment,
            "reasoning": reasoning,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_add_node(
        file_path: str,
        source_code: str,
        after_node: str | None = None,
    ) -> str:
        """Add a new function or class to a file.

        Args:
            file_path: Path to the target file.
            source_code: The source code to insert.
            after_node: Optional node ID to insert after.
        """
        params: dict = {"file_path": file_path, "source_code": source_code}
        if after_node is not None:
            params["after_node"] = after_node
        result = lenspr.handle_tool("lens_add_node", params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_delete_node(node_id: str) -> str:
        """Delete a node from the codebase. Check impact first!

        Args:
            node_id: The node identifier to delete.
        """
        result = lenspr.handle_tool("lens_delete_node", {"node_id": node_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_search(
        query: str,
        search_in: str = "all",
    ) -> str:
        """Search nodes by name, code content, or docstring.

        Args:
            query: Search query string.
            search_in: Where to search: name, code, docstring, or all.
        """
        return _tool_result("lens_search", {
            "query": query,
            "search_in": search_in,
        })

    @mcp.tool()
    def lens_get_structure(
        max_depth: int = 2,
        mode: str = "summary",
        limit: int = 100,
        offset: int = 0,
        path_prefix: str | None = None,
    ) -> str:
        """Get compact overview of project structure (files, classes, functions).

        Args:
            max_depth: 0=files only, 1=with classes/functions, 2=with methods.
            mode: "full" for all details, "summary" for counts only (default).
                  "compact" for totals only (best for large projects).
            limit: Max files to return (for pagination).
            offset: Skip first N files.
            path_prefix: Filter to files starting with this path.
        """
        params: dict = {"max_depth": max_depth, "mode": mode, "limit": limit, "offset": offset}
        if path_prefix is not None:
            params["path_prefix"] = path_prefix
        return _tool_result("lens_get_structure", params)

    @mcp.tool()
    def lens_rename(
        node_id: str,
        new_name: str,
    ) -> str:
        """Rename a function, class, or method across the entire project.

        Args:
            node_id: The node identifier to rename.
            new_name: The new name.
        """
        result = lenspr.handle_tool("lens_rename", {
            "node_id": node_id,
            "new_name": new_name,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_context(
        node_id: str,
        include_callers: bool = True,
        include_callees: bool = True,
        include_tests: bool = True,
        depth: int = 1,
        include_source: bool = True,
    ) -> str:
        """Get full context for a node in one call: source, callers, callees, tests, imports.

        Replaces multiple get_node + get_connections calls. Returns the target node's
        source code plus source code of all related nodes.

        Args:
            node_id: The node identifier (e.g. app.models.User).
            include_callers: Include nodes that call/use this node.
            include_callees: Include nodes this node calls/uses.
            include_tests: Include related test functions.
            depth: How many levels of callers/callees to include.
            include_source: Include full source code for callers/callees/tests.
        """
        return _tool_result("lens_context", {
            "node_id": node_id,
            "include_callers": include_callers,
            "include_callees": include_callees,
            "include_tests": include_tests,
            "depth": depth,
            "include_source": include_source,
        })

    @mcp.tool()
    def lens_grep(
        pattern: str,
        file_glob: str = "*.py",
        max_results: int = 50,
    ) -> str:
        """Search for a text pattern across all project files with graph context.

        Returns matching lines with information about which function/class contains
        each match. Supports regex patterns.

        Args:
            pattern: Text or regex pattern to search for.
            file_glob: Glob pattern to filter files (e.g. '*.py', 'tests/**').
            max_results: Maximum number of results to return.
        """
        return _tool_result("lens_grep", {
            "pattern": pattern,
            "file_glob": file_glob,
            "max_results": max_results,
        })

    @mcp.tool()
    def lens_diff() -> str:
        """Show what changed since last sync without syncing.

        Returns lists of added, modified, and deleted files compared
        to the current graph state.
        """
        return _tool_result("lens_diff", {})

    @mcp.tool()
    def lens_batch(updates: list[dict]) -> str:
        """Apply multiple node updates atomically with a single reparse.

        All changes are validated first. If any validation fails, nothing is applied.
        On patch error, all changes are rolled back.

        Args:
            updates: List of {node_id, new_source} pairs to apply.
        """
        result = lenspr.handle_tool("lens_batch", {"updates": updates})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_health() -> str:
        """Get health report for the code graph.

        Returns: total nodes/edges, breakdown by type and confidence,
        percentage of resolved edges, nodes without docstrings, circular imports.
        """
        result = lenspr.handle_tool("lens_health", {})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_dependencies(group_by: str = "package") -> str:
        """List all external dependencies (stdlib and third-party packages).

        Args:
            group_by: "package" to group by package name, "file" to group by source file.
        """
        result = lenspr.handle_tool("lens_dependencies", {"group_by": group_by})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_validate_change(node_id: str, new_source: str) -> str:
        """Dry-run validation: check what would happen if you update a node.

        Returns validation result, proactive warnings, and impact analysis
        WITHOUT actually applying changes. Use before lens_update_node.

        Args:
            node_id: The node to validate.
            new_source: Proposed new source code.
        """
        result = lenspr.handle_tool("lens_validate_change", {
            "node_id": node_id,
            "new_source": new_source,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_dead_code(entry_points: list[str] | None = None) -> str:
        """Find potentially dead code not reachable from entry points.

        Entry points are auto-detected (main, CLI commands, test functions, API handlers).

        Args:
            entry_points: Additional entry point node IDs. If empty, auto-detects.
        """
        params: dict = {}
        if entry_points is not None:
            params["entry_points"] = entry_points
        result = lenspr.handle_tool("lens_dead_code", params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_find_usages(
        node_id: str = "", include_tests: bool = True,
        node_ids: list[str] | None = None,
    ) -> str:
        """Find all usages of a node across the codebase.

        Returns callers, importers, and string references.
        Supports batch mode: pass node_ids (list) to check multiple nodes in one call.

        Args:
            node_id: The node to find usages of.
            include_tests: Include usages from test files. Default: true.
            node_ids: Multiple nodes to find usages of (batch mode). Overrides node_id.
        """
        params: dict = {"include_tests": include_tests}
        if node_ids:
            params["node_ids"] = node_ids
        else:
            params["node_id"] = node_id
        result = lenspr.handle_tool("lens_find_usages", params)
        return json.dumps(result, indent=2)

    # -- Semantic Annotation Tools --

    @mcp.tool()
    def lens_annotate(node_id: str) -> str:
        """Generate semantic annotations for a node.

        Returns suggested summary, role, and side effects.

        Args:
            node_id: The node to annotate.
        """
        result = lenspr.handle_tool("lens_annotate", {"node_id": node_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_save_annotation(
        node_id: str,
        summary: str | None = None,
        role: str | None = None,
        side_effects: list[str] | None = None,
        semantic_inputs: list[str] | None = None,
        semantic_outputs: list[str] | None = None,
    ) -> str:
        """Save semantic annotations to a node.

        Args:
            node_id: The node to annotate.
            summary: Short description of what this node does.
            role: Semantic role (validator, transformer, io, etc.).
            side_effects: List of side effects like 'writes_file', 'network_io'.
            semantic_inputs: Semantic types of inputs.
            semantic_outputs: Semantic types of outputs.
        """
        params: dict = {"node_id": node_id}
        if summary is not None:
            params["summary"] = summary
        if role is not None:
            params["role"] = role
        if side_effects is not None:
            params["side_effects"] = side_effects
        if semantic_inputs is not None:
            params["semantic_inputs"] = semantic_inputs
        if semantic_outputs is not None:
            params["semantic_outputs"] = semantic_outputs
        result = lenspr.handle_tool("lens_save_annotation", params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_batch_save_annotations(
        annotations: list[dict],
    ) -> str:
        """Save multiple annotations at once. ONE confirmation for many nodes.

        You only need to provide summary for each node. Role and side_effects
        are auto-detected from patterns (no hallucination risk).

        Args:
            annotations: Array of annotation objects, each with:
                - node_id (required): The node to annotate
                - summary (required): 1-2 sentence description of what the code does
                - role (optional): Auto-detected if not provided
                - side_effects (optional): Auto-detected if not provided

        Example:
            annotations=[
                {"node_id": "app.utils.validate", "summary": "Validates email"},
                {"node_id": "app.db.save", "summary": "Persists user data"}
            ]
        """
        result = lenspr.handle_tool("lens_batch_save_annotations", {"annotations": annotations})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_annotate_batch(
        type_filter: str | None = None,
        file_path: str | None = None,
        unannotated_only: bool = True,
        stale_only: bool = False,
        limit: int = 10,
    ) -> str:
        """Get nodes that need annotation.

        Args:
            type_filter: Filter by node type (function, method, class).
            file_path: Filter by file path prefix.
            unannotated_only: Only return unannotated nodes. Default: true.
            stale_only: Only return nodes with stale annotations. Default: false.
            limit: Max nodes to return. Default: 10.
        """
        params: dict = {
            "unannotated_only": unannotated_only,
            "stale_only": stale_only,
            "limit": limit,
        }
        if type_filter is not None:
            params["type_filter"] = type_filter
        if file_path is not None:
            params["file_path"] = file_path
        return _tool_result("lens_annotate_batch", params)

    @mcp.tool()
    def lens_annotation_stats() -> str:
        """Get annotation coverage statistics for the codebase.

        Returns: total annotatable, annotated count, stale annotations,
        breakdown by type and role.
        """
        result = lenspr.handle_tool("lens_annotation_stats", {})
        return json.dumps(result, indent=2)

    # -- Git Integration Tools --

    @mcp.tool()
    def lens_blame(node_id: str) -> str:
        """Get git blame information for a node's source lines.

        Shows who wrote each line and when.

        Args:
            node_id: The node to get blame info for.
        """
        result = lenspr.handle_tool("lens_blame", {"node_id": node_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_node_history(node_id: str, limit: int = 10) -> str:
        """Get commit history for a specific node.

        Shows commits that modified the lines where this node is defined.

        Args:
            node_id: The node to get history for.
            limit: Max commits to return. Default: 10.
        """
        result = lenspr.handle_tool("lens_node_history", {
            "node_id": node_id,
            "limit": limit,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_commit_scope(commit: str) -> str:
        """Analyze what nodes were affected by a specific commit.

        Shows which functions/classes were modified.

        Args:
            commit: Commit hash (short or full).
        """
        result = lenspr.handle_tool("lens_commit_scope", {"commit": commit})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_recent_changes(limit: int = 5, file_path: str | None = None) -> str:
        """Get recently changed nodes based on git history.

        Useful for understanding what's been modified recently.

        Args:
            limit: Max commits to analyze. Default: 5.
            file_path: Filter to specific file path.
        """
        params: dict = {"limit": limit}
        if file_path is not None:
            params["file_path"] = file_path
        result = lenspr.handle_tool("lens_recent_changes", params)
        return json.dumps(result, indent=2)

    # -- Explanation Tool --

    @mcp.tool()
    def lens_explain(node_id: str, include_examples: bool = True) -> str:
        """Generate a human-readable explanation of what a function/class does.

        Provides rich context (callers, callees, usage examples) plus rule-based
        analysis. Use this to understand unfamiliar code.

        Args:
            node_id: The node to explain (e.g. 'app.utils.validate_email').
            include_examples: Include usage examples from callers. Default: true.
        """
        result = lenspr.handle_tool("lens_explain", {
            "node_id": node_id,
            "include_examples": include_examples,
        })
        return json.dumps(result, indent=2)

    # -- Architecture Metrics Tools --

    @mcp.tool()
    def lens_class_metrics(node_id: str) -> str:
        """Get pre-computed metrics for a class: method count, lines,
        public/private methods, dependencies, internal calls, method prefixes,
        and percentile rank compared to other classes.
        Metrics are computed during init/sync - this is O(1) read.

        Args:
            node_id: The class node ID to get metrics for.
        """
        return _tool_result("lens_class_metrics", {"node_id": node_id})

    @mcp.tool()
    def lens_project_metrics() -> str:
        """Get project-wide class metrics: total classes, avg/median/min/max methods,
        and percentiles (p90, p95). Use this to understand the distribution
        before interpreting individual class metrics.
        """
        return _tool_result("lens_project_metrics", {})

    @mcp.tool()
    def lens_largest_classes(limit: int = 10) -> str:
        """Get classes sorted by method count (descending).
        Returns the N largest classes with their metrics.
        Use this to identify potentially complex classes for review.

        Args:
            limit: Max classes to return. Default: 10.
        """
        return _tool_result("lens_largest_classes", {"limit": limit})

    @mcp.tool()
    def lens_compare_classes(node_ids: list[str]) -> str:
        """Compare metrics between multiple classes.
        Returns metrics side-by-side for easy comparison.

        Args:
            node_ids: List of class node IDs to compare.
        """
        return _tool_result("lens_compare_classes", {"node_ids": node_ids})

    @mcp.tool()
    def lens_components(
        path: str | None = None,
        min_cohesion: float = 0.0,
    ) -> str:
        """Analyze components (directory-based modules) with cohesion metrics.
        Components are directories containing related code. Returns cohesion score
        (internal edges / total edges), public API nodes, and internal nodes.

        Args:
            path: Filter to components under this path.
            min_cohesion: Minimum cohesion threshold (0.0-1.0). Default: 0.0.
        """
        params: dict = {}
        if path is not None:
            params["path"] = path
        if min_cohesion > 0:
            params["min_cohesion"] = min_cohesion
        return _tool_result("lens_components", params)

    # -- Session Memory Tools --

    @mcp.tool()
    def lens_session_write(key: str, value: str) -> str:
        """Write or overwrite a persistent session note by key.

        Notes survive context resets and are stored in .lens/session.db.
        Use to save task state, decisions, TODOs, and progress.

        Args:
            key: Note key (e.g. 'current_task', 'done', 'next_steps').
            value: Note content (markdown supported).
        """
        return _tool_result("lens_session_write", {"key": key, "value": value})

    @mcp.tool()
    def lens_session_read() -> str:
        """Read all persistent session notes.

        Call at the start of a new session to restore context from the previous one.
        """
        return _tool_result("lens_session_read", {})

    @mcp.tool()
    def lens_session_handoff(limit: int = 10) -> str:
        """Generate a handoff document combining recent changes and session notes.

        Combines recent LensPR changes (with reasoning) and all current session notes
        into a markdown document. Saves the result as the 'handoff' session note so
        the next session can restore full context with lens_session_read().

        Args:
            limit: Max recent changes to include. Default: 10.
        """
        return _tool_result("lens_session_handoff", {"limit": limit})

    @mcp.tool()
    def lens_run_tests(
        path: str = "",
        filter_k: str = "",
        timeout: int = 120,
        max_output_lines: int = 150,
    ) -> str:
        """Run pytest and return structured results.

        Args:
            path: Specific test file or directory (e.g. 'tests/test_auth.py').
                  If omitted, pytest auto-discovers all tests.
            filter_k: pytest -k expression to filter by test name.
            timeout: Max seconds to wait. Default: 120.
            max_output_lines: Max output lines to return. Default: 150.
        """
        params: dict = {"timeout": timeout, "max_output_lines": max_output_lines}
        if path:
            params["path"] = path
        if filter_k:
            params["filter_k"] = filter_k
        return _tool_result("lens_run_tests", params)

    # -- Safety & Vibecoding Tools --

    @mcp.tool()
    def lens_nfr_check(node_id: str) -> str:
        """Check a function for missing non-functional requirements (NFRs).

        Checks: error handling on IO/network/DB, structured logging,
        hardcoded secrets, input validation on handlers, auth on sensitive ops.

        Args:
            node_id: The node identifier to check.
        """
        return _tool_result("lens_nfr_check", {"node_id": node_id})

    @mcp.tool()
    def lens_test_coverage(file_path: str | None = None) -> str:
        """Report which functions/methods have test coverage (graph-based).

        Uses the call graph: a function is 'covered' if at least one test
        function calls it. Returns coverage %, grade (A–F), and lists of
        covered/uncovered functions.

        Args:
            file_path: Optional filter to a specific file or directory.
        """
        params: dict = {}
        if file_path is not None:
            params["file_path"] = file_path
        return _tool_result("lens_test_coverage", params)

    @mcp.tool()
    def lens_security_scan(file_path: str | None = None) -> str:
        """Run Bandit security scanner and map results to graph nodes.

        Returns issues grouped by the function/class that contains them,
        with severity (HIGH/MEDIUM/LOW) and CWE IDs where available.

        Requires: pip install bandit

        Args:
            file_path: Specific file or directory to scan. Defaults to full project.
        """
        params: dict = {}
        if file_path is not None:
            params["file_path"] = file_path
        return _tool_result("lens_security_scan", params)

    @mcp.tool()
    def lens_dep_audit() -> str:
        """Audit project dependencies for known vulnerabilities.

        Tries pip-audit (Python) then npm audit (Node.js).
        Returns vulnerable packages with CVE IDs and fix versions.

        Requires: pip install pip-audit  (or npm for JS projects)
        """
        return _tool_result("lens_dep_audit", {})

    @mcp.tool()
    def lens_arch_rule_add(
        rule_type: str,
        description: str = "",
        config: dict | None = None,
    ) -> str:
        """Add an architecture rule enforced on every code change.

        Rule types:
        - no_dependency: from_pattern + to_pattern (glob-style node ID patterns)
        - max_class_methods: threshold (int)
        - required_test: pattern (glob for function names)
        - no_circular_imports: no config needed

        Examples:
          rule_type="no_dependency", config={"from_pattern":"*.api.*","to_pattern":"*.database*"}
          rule_type="max_class_methods", config={"threshold": 20}
          rule_type="required_test", config={"pattern": "*_handler"}

        Args:
            rule_type: Type of rule (see above).
            description: Human-readable description of the rule.
            config: Rule-specific configuration dict.
        """
        params: dict = {"rule_type": rule_type, "description": description}
        if config is not None:
            params["config"] = config
        result = lenspr.handle_tool("lens_arch_rule_add", params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_arch_rule_list() -> str:
        """List all defined architecture rules with their IDs and config."""
        return _tool_result("lens_arch_rule_list", {})

    @mcp.tool()
    def lens_arch_rule_delete(rule_id: str) -> str:
        """Delete an architecture rule by ID.

        Args:
            rule_id: The rule ID (from lens_arch_rule_list).
        """
        result = lenspr.handle_tool("lens_arch_rule_delete", {"rule_id": rule_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_arch_check() -> str:
        """Check all architecture rules against the current codebase.

        Returns all violations grouped by rule. Run after refactoring or
        to audit an inherited vibecoded project.
        """
        return _tool_result("lens_arch_check", {})

    @mcp.tool()
    def lens_vibecheck() -> str:
        """Comprehensive vibecoding health score for the project (0-100, grade A–F).

        Aggregates: test coverage (25pts), dead code (20pts), circular imports (15pts),
        architecture rules compliance (15pts), documentation (10pts),
        graph confidence (15pts).

        Use this to track if the codebase is improving or degrading over time.
        Run lens_test_coverage, lens_security_scan, lens_arch_check for breakdowns.
        """
        return _tool_result("lens_vibecheck", {})

    logger.info("Starting LensPR MCP server for: %s", project_path)
    mcp.run(transport="stdio")
