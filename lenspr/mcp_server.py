"""MCP server for LensPR — exposes code graph tools over Model Context Protocol."""

from __future__ import annotations

import json
import logging
import threading
import time

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("lenspr.mcp")


def _start_watcher(project_path: str) -> None:
    """Start a background file watcher that auto-syncs on changes.

    Uses watchdog if available, otherwise falls back to a simple
    polling watcher.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        _start_watchdog_watcher(project_path, FileSystemEventHandler, Observer)  # type: ignore[arg-type]
    except ImportError:
        logger.info("watchdog not installed, using polling watcher")
        _start_polling_watcher(project_path)


def _start_watchdog_watcher(
    project_path: str,
    handler_cls: type,
    observer_cls: type,
) -> None:
    """Watchdog-based file watcher running in a daemon thread."""
    import lenspr

    class _SyncHandler(handler_cls):  # type: ignore[misc]
        def __init__(self) -> None:
            self._pending = False
            self._lock = threading.Lock()

        def on_modified(self, event: object) -> None:
            if (
                hasattr(event, "src_path")
                and event.src_path.endswith(".py")  # type: ignore[union-attr]
            ):
                with self._lock:
                    self._pending = True

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
            time.sleep(2)
            should_sync = False
            with handler._lock:
                if handler._pending:
                    handler._pending = False
                    should_sync = True
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
                            "Auto-sync: +%d ~%d -%d",
                            len(result.added),
                            len(result.modified),
                            len(result.deleted),
                        )
                except Exception:
                    logger.exception("Auto-sync failed")

    t = threading.Thread(target=_sync_loop, daemon=True)
    t.start()
    logger.info("Watchdog file watcher started for: %s", project_path)


def _start_polling_watcher(project_path: str) -> None:
    """Simple polling watcher as fallback when watchdog is unavailable."""
    import lenspr

    def _poll_loop() -> None:
        while True:
            time.sleep(5)
            try:
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
    logger.info(
        "Polling file watcher started for: %s (every 5s)", project_path
    )


def run_server(project_path: str) -> None:
    """Initialize LensPR and start the MCP server on stdio."""
    import lenspr

    lenspr.init(project_path)
    instructions = lenspr.get_system_prompt()

    # Start background file watcher for auto-sync
    _start_watcher(project_path)

    mcp = FastMCP(
        name="lenspr",
        instructions=instructions,
    )

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
        result = lenspr.handle_tool("lens_list_nodes", params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_get_node(node_id: str) -> str:
        """Get full details of a specific node including its source code.

        Args:
            node_id: The node identifier (e.g. app.models.User).
        """
        result = lenspr.handle_tool("lens_get_node", {"node_id": node_id})
        return json.dumps(result, indent=2)

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
        result = lenspr.handle_tool("lens_check_impact", {
            "node_id": node_id,
            "depth": depth,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_update_node(
        node_id: str,
        new_source: str,
    ) -> str:
        """Update the source code of a node. Validates syntax and structure before applying.

        Args:
            node_id: The node identifier.
            new_source: The new source code for the node.
        """
        result = lenspr.handle_tool("lens_update_node", {
            "node_id": node_id,
            "new_source": new_source,
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
        result = lenspr.handle_tool("lens_search", {
            "query": query,
            "search_in": search_in,
        })
        return json.dumps(result, indent=2)

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
            limit: Max files to return (for pagination).
            offset: Skip first N files.
            path_prefix: Filter to files starting with this path.
        """
        params: dict = {"max_depth": max_depth, "mode": mode, "limit": limit, "offset": offset}
        if path_prefix is not None:
            params["path_prefix"] = path_prefix
        result = lenspr.handle_tool("lens_get_structure", params)
        return json.dumps(result, indent=2)

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
        result = lenspr.handle_tool("lens_context", {
            "node_id": node_id,
            "include_callers": include_callers,
            "include_callees": include_callees,
            "include_tests": include_tests,
            "depth": depth,
            "include_source": include_source,
        })
        return json.dumps(result, indent=2)

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
        result = lenspr.handle_tool("lens_grep", {
            "pattern": pattern,
            "file_glob": file_glob,
            "max_results": max_results,
        })
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_diff() -> str:
        """Show what changed since last sync without syncing.

        Returns lists of added, modified, and deleted files compared
        to the current graph state.
        """
        result = lenspr.handle_tool("lens_diff", {})
        return json.dumps(result, indent=2)

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
    def lens_find_usages(node_id: str, include_tests: bool = True) -> str:
        """Find all usages of a node across the codebase.

        Returns callers, importers, and string references.

        Args:
            node_id: The node to find usages of.
            include_tests: Include usages from test files. Default: true.
        """
        result = lenspr.handle_tool("lens_find_usages", {
            "node_id": node_id,
            "include_tests": include_tests,
        })
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
        result = lenspr.handle_tool("lens_annotate_batch", params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def lens_annotation_stats() -> str:
        """Get annotation coverage statistics for the codebase.

        Returns: total annotatable, annotated count, stale annotations,
        breakdown by type and role.
        """
        result = lenspr.handle_tool("lens_annotation_stats", {})
        return json.dumps(result, indent=2)

    logger.info("Starting LensPR MCP server for: %s", project_path)
    mcp.run(transport="stdio")
