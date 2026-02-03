"""MCP server for LensPR — exposes code graph tools over Model Context Protocol."""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("lenspr.mcp")


def run_server(project_path: str) -> None:
    """Initialize LensPR and start the MCP server on stdio."""
    import lenspr

    lenspr.init(project_path)
    instructions = lenspr.get_system_prompt()

    mcp = FastMCP(
        name="lenspr",
        instructions=instructions,
    )

    @mcp.tool()
    def lens_list_nodes(
        type: str | None = None,
        file_path: str | None = None,
    ) -> str:
        """List all nodes in the codebase, optionally filtered by type or file.

        Args:
            type: Filter by node type (module, class, function, method, block).
            file_path: Filter by file path.
        """
        params: dict = {}
        if type is not None:
            params["type"] = type
        if file_path is not None:
            params["file_path"] = file_path
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
    def lens_get_structure(max_depth: int = 2) -> str:
        """Get compact overview of project structure (files, classes, functions).

        Args:
            max_depth: Maximum nesting depth to display.
        """
        result = lenspr.handle_tool("lens_get_structure", {"max_depth": max_depth})
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

    logger.info("Starting LensPR MCP server for: %s", project_path)
    mcp.run(transport="stdio")
