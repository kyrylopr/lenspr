"""LensPR command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lenspr",
        description="LensPR: Code-as-graph for safe LLM-assisted development",
    )
    subparsers = parser.add_subparsers(dest="command")

    # -- init --
    p_init = subparsers.add_parser("init", help="Initialize LensPR on a project")
    p_init.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_init.add_argument("--force", action="store_true", help="Reinitialize even if .lens/ exists")

    # -- sync --
    p_sync = subparsers.add_parser("sync", help="Resync graph with filesystem changes")
    p_sync.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_sync.add_argument("--full", action="store_true", help="Force full reparse")

    # -- status --
    p_status = subparsers.add_parser("status", help="Show graph statistics")
    p_status.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")

    # -- search --
    p_search = subparsers.add_parser("search", help="Search nodes by name or content")
    p_search.add_argument("path", help="Project root")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument(
        "--in",
        dest="search_in",
        default="all",
        choices=["name", "code", "docstring", "all"],
        help="Where to search (default: all)",
    )

    # -- impact --
    p_impact = subparsers.add_parser("impact", help="Check impact of changing a node")
    p_impact.add_argument("path", help="Project root")
    p_impact.add_argument("node_id", help="Node identifier (e.g. app.models.User)")
    p_impact.add_argument("--depth", type=int, default=2, help="Traversal depth (default: 2)")

    # -- watch --
    p_watch = subparsers.add_parser("watch", help="Watch for changes and auto-sync")
    p_watch.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")

    # -- serve --
    p_serve = subparsers.add_parser("serve", help="Start MCP server (stdio transport)")
    p_serve.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_serve.add_argument(
        "--dev", action="store_true",
        help="Enable hot-reload of lenspr modules (for development)"
    )

    # -- setup --
    p_setup = subparsers.add_parser(
        "setup",
        help="Configure MCP server for Claude Code / Claude Desktop"
    )
    p_setup.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_setup.add_argument(
        "--global", dest="global_config", action="store_true",
        help="Also update global Claude Desktop config"
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "init": cmd_init,
        "sync": cmd_sync,
        "status": cmd_status,
        "search": cmd_search,
        "impact": cmd_impact,
        "watch": cmd_watch,
        "serve": cmd_serve,
        "setup": cmd_setup,
    }
    handlers[args.command](args)


def _cli_progress(current: int, total: int, file_path: str) -> None:
    """Progress callback for CLI commands."""
    # Get just the filename for display
    name = Path(file_path).name
    # Truncate long names
    if len(name) > 30:
        name = name[:27] + "..."
    # Write progress on same line
    sys.stdout.write(f"\r  Parsing... {current}/{total} [{name:<30}]")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


def cmd_init(args: argparse.Namespace) -> None:
    import lenspr

    path = str(Path(args.path).resolve())
    print(f"Initializing LensPR at {path}")
    try:
        lenspr.init(path, force=args.force, progress_callback=_cli_progress)
    except lenspr.LensError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

    ctx = lenspr.get_context()
    g = ctx.get_graph()
    print("Done!")
    print(f"  Nodes: {g.number_of_nodes()}")
    print(f"  Edges: {g.number_of_edges()}")


def cmd_sync(args: argparse.Namespace) -> None:
    import lenspr

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)
        result = lenspr.sync(full=args.full)
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Sync complete: +{len(result.added)} ~{len(result.modified)} -{len(result.deleted)}")


def cmd_status(args: argparse.Namespace) -> None:
    import lenspr
    from lenspr.graph import get_structure

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    ctx = lenspr.get_context()
    g = ctx.get_graph()
    structure = get_structure(g)

    print(f"Project: {path}")
    print(f"  Nodes: {g.number_of_nodes()}")
    print(f"  Edges: {g.number_of_edges()}")
    print(f"  Files: {len(structure)}")


def cmd_search(args: argparse.Namespace) -> None:
    import lenspr

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)
        result = lenspr.handle_tool("lens_search", {
            "query": args.query,
            "search_in": args.search_in,
        })
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


def cmd_impact(args: argparse.Namespace) -> None:
    import lenspr

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)
        result = lenspr.handle_tool("lens_check_impact", {
            "node_id": args.node_id,
            "depth": args.depth,
        })
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


def cmd_watch(args: argparse.Namespace) -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "Watch dependencies not installed. Install with:\n"
            "  pip install lenspr[watch]",
            file=sys.stderr,
        )
        sys.exit(1)

    import time

    import lenspr

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    class SyncHandler(FileSystemEventHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            self._pending = False

        def on_modified(self, event: object) -> None:
            if hasattr(event, "src_path") and event.src_path.endswith(".py"):  # type: ignore[union-attr]
                self._pending = True

        def on_created(self, event: object) -> None:
            if hasattr(event, "src_path") and event.src_path.endswith(".py"):  # type: ignore[union-attr]
                self._pending = True

        def on_deleted(self, event: object) -> None:
            if hasattr(event, "src_path") and event.src_path.endswith(".py"):  # type: ignore[union-attr]
                self._pending = True

    handler = SyncHandler()
    observer = Observer()
    observer.schedule(handler, path, recursive=True)
    observer.start()

    print(f"Watching {path} for changes... (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
            if handler._pending:
                handler._pending = False
                try:
                    result = lenspr.sync()
                    print(
                        f"Synced: +{len(result.added)} "
                        f"~{len(result.modified)} "
                        f"-{len(result.deleted)}"
                    )
                except Exception as e:
                    print(f"Sync error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def cmd_serve(args: argparse.Namespace) -> None:
    try:
        from lenspr.mcp_server import run_server
    except ImportError:
        print(
            "MCP dependencies not installed. Install with:\n"
            "  pip install 'lenspr[mcp]'",
            file=sys.stderr,
        )
        sys.exit(1)

    path = str(Path(args.path).resolve())
    run_server(path, hot_reload=getattr(args, "dev", False))


def _check_mcp_dependencies() -> bool:
    """Check if MCP dependencies are installed."""
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def cmd_setup(args: argparse.Namespace) -> None:
    """Configure MCP server for Claude Code / Claude Desktop."""
    import shutil

    path = Path(args.path).resolve()
    mcp_config_path = path / ".mcp.json"

    # Check if MCP dependencies are installed
    mcp_installed = _check_mcp_dependencies()

    # Find lenspr executable
    lenspr_bin = shutil.which("lenspr")
    if not lenspr_bin:
        # Fallback: assume it's in the same location as python
        lenspr_bin = "lenspr"

    # Build MCP server configuration
    server_config = {
        "command": lenspr_bin,
        "args": ["serve", str(path)],
    }

    # Update or create .mcp.json
    if mcp_config_path.exists():
        try:
            config = json.loads(mcp_config_path.read_text())
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    if "lenspr" in config["mcpServers"]:
        print(f"✓ LensPR already configured in {mcp_config_path}")
    else:
        config["mcpServers"]["lenspr"] = server_config
        mcp_config_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"✓ Created {mcp_config_path}")

    # Optionally update global Claude Desktop config
    if args.global_config:
        _update_global_claude_config(str(path), lenspr_bin)

    print()
    if not mcp_installed:
        print("⚠️  MCP dependencies not installed!")
        print("   Run: pip install 'lenspr[mcp]'")
        print()
    print("Next steps:")
    if not mcp_installed:
        print("  1. Install MCP: pip install 'lenspr[mcp]'")
        print("  2. Run: lenspr init")
        print("  3. Restart Claude Code (or Claude Desktop)")
    else:
        print("  1. Run: lenspr init")
        print("  2. Restart Claude Code (or Claude Desktop)")
    print()
    print("The lens_* tools will be available after restart.")


def _update_global_claude_config(project_path: str, lenspr_bin: str) -> None:
    """Update global Claude Desktop configuration."""
    import platform

    # Claude Desktop config location varies by platform
    system = platform.system()
    if system == "Darwin":  # macOS
        config_path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    elif system == "Windows":
        config_path = Path.home() / "AppData/Roaming/Claude/claude_desktop_config.json"
    else:  # Linux
        config_path = Path.home() / ".config/claude/claude_desktop_config.json"

    if not config_path.parent.exists():
        print(f"  ⚠ Claude Desktop config directory not found: {config_path.parent}")
        print("    Claude Desktop may not be installed.")
        return

    # Load existing config
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Use project name as server key to allow multiple projects
    project_name = Path(project_path).name
    server_key = f"lenspr-{project_name}"

    server_config = {
        "command": lenspr_bin,
        "args": ["serve", project_path],
    }

    if server_key in config["mcpServers"]:
        print(f"  ✓ {server_key} already in global Claude Desktop config")
    else:
        config["mcpServers"][server_key] = server_config
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        print(f"  ✓ Added {server_key} to {config_path}")


if __name__ == "__main__":
    main()
