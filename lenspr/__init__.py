"""
LensPR: Code-as-Graph for Safe LLM-Assisted Development

Usage:
    import lenspr

    # Initialize on a project
    lenspr.init("./my_project")

    # Get tools for Claude API
    tools = lenspr.get_claude_tools()
    prompt = lenspr.get_system_prompt()

    # Handle Claude tool calls
    result = lenspr.handle_tool("lens_check_impact", {"node_id": "app.main"})

    # Direct access (scripting without Claude)
    nodes = lenspr.list_nodes(type="function")
    impact = lenspr.check_impact("app.models.User")
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from lenspr.context import LensContext
from lenspr.models import (
    Change,
    Edge,
    LensError,
    Node,
    NodeNotFoundError,
    NotInitializedError,
    SyncResult,
)
from lenspr.models import (
    ToolResponse as ToolResponse,
)
from lenspr.parsers.base import ProgressCallback
from lenspr.stats import ParseStats

__version__ = "0.1.0"

# Module-level context — set by init()
_ctx: LensContext | None = None


def _require_ctx() -> LensContext:
    if _ctx is None:
        raise NotInitializedError(
            "LensPR is not initialized. Call lenspr.init(project_path) first."
        )
    return _ctx


def init(
    project_path: str,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    collect_stats: bool = False,
) -> tuple[LensContext, ParseStats | None]:
    """
    Initialize LensPR on a project.

    Creates .lens/ directory, parses code, and builds the graph.

    Args:
        project_path: Path to the Python project root.
        force: If True, reinitialize even if .lens/ already exists.
        progress_callback: Optional callback(current, total, file_path) for progress.
        collect_stats: If True, collect and return detailed parsing statistics.

    Returns:
        Tuple of (LensContext, ParseStats | None).
    """
    global _ctx

    root = Path(project_path).resolve()
    if not root.is_dir():
        raise LensError(f"Not a directory: {root}")

    lens_dir = root / ".lens"

    if lens_dir.exists() and not force:
        # Load existing context
        _ctx = LensContext(root, lens_dir)

        # Auto-reinitialize if database is empty (previous failed init)
        g = _ctx.get_graph()
        if g.number_of_nodes() == 0:
            force = True  # Fall through to reinitialize
        else:
            return _ctx, None

    # Initialize fresh
    from lenspr.database import init_database

    init_database(lens_dir)

    # Write config
    config = {
        "version": __version__,
        "initialized_at": datetime.now(UTC).isoformat(),
        "last_sync": datetime.now(UTC).isoformat(),
        "exclude_patterns": [
            "__pycache__", "*.pyc", ".git", "venv", ".venv", "node_modules",
        ],
    }
    config_path = lens_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))

    # Create context and do initial parse
    _ctx = LensContext(root, lens_dir)
    _, stats = _ctx.full_sync(progress_callback, collect_stats)

    return _ctx, stats


def sync(full: bool = False) -> SyncResult:
    """
    Resync graph with current file state.

    Uses incremental sync by default (only reparses changed files).
    Pass full=True to force a complete reparse.
    """
    ctx = _require_ctx()
    if full:
        result, _ = ctx.full_sync()
        return result
    return ctx.incremental_sync()


def get_system_prompt() -> str:
    """Generate system prompt for Claude with current project state."""
    ctx = _require_ctx()

    prompt_template = _load_prompt_template()

    g = ctx.get_graph()
    from lenspr.graph import get_structure
    result = get_structure(g, max_depth=1)

    # Format structure as readable text
    structure_text = _format_structure(result["structure"])

    node_count = g.number_of_nodes()
    edge_count = g.number_of_edges()
    file_count = result["pagination"]["total_files"]

    base_prompt = prompt_template.format(
        project_structure=structure_text,
        node_count=node_count,
        edge_count=edge_count,
        file_count=file_count,
    )

    # Append session context (recent changes + session notes) if available
    extra_sections: list[str] = []

    # 1. Recent changes from history.db (last 5)
    try:
        from lenspr.tracker import get_history
        if ctx.history_db.exists():
            recent = get_history(ctx.history_db, limit=5)
            if recent:
                lines = ["## Recent Changes (last session)"]
                for ch in recent:
                    when = ch.timestamp[:19].replace("T", " ")
                    line = f"- **{ch.action}** `{ch.node_id}` at {when}"
                    if ch.reasoning:
                        line += f" — {ch.reasoning}"
                    lines.append(line)
                extra_sections.append("\n".join(lines))
    except Exception:
        pass

    # 2. Session notes from session.db
    try:
        from lenspr import database
        if ctx.session_db.exists():
            notes = database.read_session_notes(ctx.session_db)
            if notes:
                lines = ["## Session Notes"]
                for note in notes:
                    lines.append(f"### {note['key']}")
                    lines.append(note["value"])
                extra_sections.append("\n".join(lines))
    except Exception:
        pass

    if extra_sections:
        return base_prompt + "\n\n" + "\n\n".join(extra_sections)

    return base_prompt


def get_claude_tools() -> list[dict]:
    """Get tool definitions for Claude API."""
    from lenspr.claude_tools import LENS_TOOLS
    return LENS_TOOLS


def handle_tool(name: str, parameters: dict) -> dict:
    """
    Handle a tool call from Claude.

    Returns dict with success, data, errors, and warnings.
    """
    ctx = _require_ctx()
    from lenspr.claude_tools import handle_tool_call
    response = handle_tool_call(name, parameters, ctx)
    return {
        "success": response.success,
        "data": response.data,
        "error": response.error,
        "hint": response.hint,
        "warnings": response.warnings,
        "affected_nodes": response.affected_nodes,
    }


# -- Direct access functions --


def list_nodes(type: str | None = None, file: str | None = None) -> list[Node]:
    """List nodes with optional filters."""
    ctx = _require_ctx()
    from lenspr.database import get_nodes
    return get_nodes(ctx.graph_db, type_filter=type, file_filter=file)


def get_node(node_id: str) -> Node:
    """Get a single node by ID."""
    ctx = _require_ctx()
    from lenspr.database import get_node as db_get_node
    node = db_get_node(node_id, ctx.graph_db)
    if not node:
        raise NodeNotFoundError(f"Node not found: {node_id}")
    return node


def get_connections(node_id: str, direction: str = "both") -> list[Edge]:
    """Get edges for a node."""
    ctx = _require_ctx()
    from lenspr.database import get_edges
    return get_edges(node_id, ctx.graph_db, direction)


def check_impact(node_id: str, depth: int = 2) -> dict:
    """Analyze impact of changing a node."""
    ctx = _require_ctx()
    g = ctx.get_graph()
    from lenspr.graph import get_impact_zone
    return get_impact_zone(g, node_id, depth)


def get_history(node_id: str | None = None) -> list[Change]:
    """Get change history."""
    ctx = _require_ctx()
    from lenspr.tracker import get_history as tracker_get_history
    return tracker_get_history(ctx.history_db, node_id=node_id)


def get_context() -> LensContext:
    """Get the current LensContext (for advanced usage)."""
    return _require_ctx()


# -- Internal helpers --


def _load_prompt_template() -> str:
    """Load system prompt template."""
    template_path = Path(__file__).parent / "prompts" / "system.md"
    if template_path.exists():
        return template_path.read_text()
    return _DEFAULT_PROMPT


def _format_structure(structure: dict) -> str:
    """Format project structure dict as readable text."""
    lines = []
    for file_path, info in sorted(structure.items()):
        lines.append(f"📄 {file_path}")
        for cls in info.get("classes", []):
            lines.append(f"  📦 class {cls['name']}")
            for method in cls.get("methods", []):
                sig = method.get("signature", method["name"])
                lines.append(f"    🔧 {sig}")
        for func in info.get("functions", []):
            sig = func.get("signature", func["name"])
            lines.append(f"  🔧 {sig}")
        for block in info.get("blocks", []):
            lines.append(f"  📋 {block['name']}")
    return "\n".join(lines)


_DEFAULT_PROMPT = """# LensPR: Code Graph Interface

You are working with a Python project through LensPR, a code-as-graph system.
Instead of editing text files directly, you interact with a structured graph
of code nodes and their relationships.

## Available Tools

### Navigation
- `lens_list_nodes` - See all functions, classes, modules
- `lens_get_node` - Get source code of a specific node
- `lens_get_connections` - See what calls/uses a node and what it calls/uses
- `lens_search` - Find nodes by name or content
- `lens_get_structure` - Overview of project organization

### Modification
- `lens_update_node` - Change a node's code
- `lens_add_node` - Create new function/class
- `lens_delete_node` - Remove a node
- `lens_rename` - Rename across the project

### Safety
- `lens_check_impact` - **ALWAYS call before modifying** - shows what will be affected

## Rules

1. **Before ANY modification**, call `lens_check_impact` to understand consequences
2. After modifying, verify the change is syntactically valid
3. Connections marked "unresolved" cannot be statically determined (dynamic dispatch, eval, getattr). Warn the user about these.
4. Prefer small, focused changes over large rewrites
5. When impact zone is large (>10 nodes), confirm with the user before proceeding

## Known Limitations

The graph is built from **static analysis**. It may miss:
- **Dynamic dispatch**: `getattr()`, `importlib.import_module()`, `eval()`
- **String-based references**: function names passed as strings to registries/routers
- **Framework magic**: decorators that register routes/commands/signals (e.g. Flask, Django, Click)
- **Monkey-patching**: runtime modifications to classes/modules

When `lens_find_usages` or `lens_dead_code` reports 0 usages, **always verify with `lens_grep`** before recommending deletion. A function with 0 graph usages may still be used dynamically.

## Current Project Structure

{project_structure}

## Statistics

- Total nodes: {node_count}
- Total edges: {edge_count}
- Files: {file_count}
"""
