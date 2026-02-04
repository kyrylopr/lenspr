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
    p_init.add_argument(
        "--install-deps", action="store_true",
        help="Auto-install npm dependencies for JS/TS packages"
    )

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

    # -- doctor --
    p_doctor = subparsers.add_parser(
        "doctor",
        help="Check project configuration and diagnose issues"
    )
    p_doctor.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")

    # -- annotate --
    p_annotate = subparsers.add_parser(
        "annotate",
        help="Show annotation coverage or auto-annotate nodes"
    )
    p_annotate.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_annotate.add_argument(
        "--auto", action="store_true",
        help="Auto-annotate all unannotated nodes (role/side_effects only, no summary)"
    )
    p_annotate.add_argument(
        "--node", metavar="NODE_ID",
        help="Annotate specific node by ID (e.g. app.models.User)"
    )
    p_annotate.add_argument(
        "--nodes", nargs="+", metavar="NODE_ID",
        help="Annotate multiple nodes by ID"
    )
    p_annotate.add_argument(
        "--file", metavar="FILE_PATH",
        help="Annotate all nodes in a specific file"
    )
    p_annotate.add_argument(
        "--force", action="store_true",
        help="Overwrite existing annotations"
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
        "doctor": cmd_doctor,
        "annotate": cmd_annotate,
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
    from lenspr.monorepo import find_packages, install_dependencies
    from lenspr.stats import format_stats_report

    path = Path(args.path).resolve()
    print(f"Initializing LensPR at {path}")
    print()

    # Detect and setup JS/TS packages
    monorepo = find_packages(path)
    if monorepo.packages:
        pkg_count = len(monorepo.packages)
        missing_count = len(monorepo.missing_node_modules)

        if monorepo.is_monorepo:
            print(f"Detected monorepo with {pkg_count} JS/TS packages")
        elif pkg_count == 1:
            print(f"Detected JS/TS package: {monorepo.packages[0].name or monorepo.packages[0].path.name}")

        if missing_count > 0:
            if args.install_deps:
                print(f"Installing dependencies for {missing_count} package(s)...")

                def npm_progress(current: int, total: int, pkg_path: str) -> None:
                    name = Path(pkg_path).name
                    print(f"  [{current}/{total}] npm install in {name}...")

                results = install_dependencies(
                    monorepo.missing_node_modules,
                    progress_callback=npm_progress,
                )
                success = sum(1 for v in results.values() if v)
                if success < missing_count:
                    print(f"  Warning: {missing_count - success} package(s) failed to install")
                print()
            else:
                # Show hint about --install-deps
                print(f"  ⚠ {missing_count} package(s) missing node_modules")
                for pkg_path in monorepo.missing_node_modules[:3]:
                    rel = pkg_path.relative_to(path) if pkg_path != path else Path(".")
                    print(f"    - {rel}")
                if missing_count > 3:
                    print(f"    ... and {missing_count - 3} more")
                print()
                print("  Tip: Use --install-deps to auto-install, or run:")
                for pkg_path in monorepo.missing_node_modules[:2]:
                    rel = pkg_path.relative_to(path) if pkg_path != path else Path(".")
                    print(f"    cd {rel} && npm install")
                print()

    try:
        ctx, stats = lenspr.init(
            str(path),
            force=args.force,
            progress_callback=_cli_progress,
            collect_stats=True,
        )
    except lenspr.LensError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

    print()  # After progress line

    # Show detailed stats if available
    if stats:
        print(format_stats_report(stats))

    # Final summary
    g = ctx.get_graph()
    db_size = (ctx.lens_dir / "graph.db").stat().st_size / 1024  # KB
    if db_size > 1024:
        db_size_str = f"{db_size / 1024:.1f} MB"
    else:
        db_size_str = f"{db_size:.0f} KB"

    print("=" * 50)
    print("Graph created successfully!")
    print()
    print(f"  Total nodes:  {g.number_of_nodes()}")
    print(f"  Total edges:  {g.number_of_edges()}")
    if stats:
        print(f"  Confidence:   {stats.overall_resolution_pct:.0f}%")
        print(f"  Parse time:   {stats.total_time_ms / 1000:.1f}s")
    print(f"  Database:     .lens/graph.db ({db_size_str})")
    print("=" * 50)
    print()
    print("Next steps:")
    print("  lenspr setup .     # Configure for Claude Code")
    print("  lenspr status .    # View detailed stats")
    print()
    print("In Claude Code, ask: \"Annotate my codebase\" for semantic annotations")


def cmd_sync(args: argparse.Namespace) -> None:
    import lenspr

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)  # Returns tuple now, but we don't need stats here
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
    from lenspr.parsers import is_supported_file

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path)
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    class SyncHandler(FileSystemEventHandler):  # type: ignore[misc]
        def __init__(self) -> None:
            self._pending = False

        def _should_track(self, file_path: str) -> bool:
            """Check if file should trigger a sync."""
            # Skip common non-project directories
            skip_parts = {
                "node_modules", "__pycache__", ".git", ".venv", "venv",
                ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
                ".next", ".nuxt", "coverage", ".lens",
            }
            p = Path(file_path)
            if any(part in skip_parts for part in p.parts):
                return False
            return is_supported_file(file_path)

        def on_modified(self, event: object) -> None:
            if hasattr(event, "src_path") and self._should_track(event.src_path):  # type: ignore[union-attr]
                self._pending = True

        def on_created(self, event: object) -> None:
            if hasattr(event, "src_path") and self._should_track(event.src_path):  # type: ignore[union-attr]
                self._pending = True

        def on_deleted(self, event: object) -> None:
            if hasattr(event, "src_path") and self._should_track(event.src_path):  # type: ignore[union-attr]
                self._pending = True

    handler = SyncHandler()
    observer = Observer()
    observer.schedule(handler, path, recursive=True)
    observer.start()

    # Show supported extensions
    from lenspr.parsers import get_supported_extensions
    exts = ", ".join(get_supported_extensions())
    print(f"Watching {path} for changes ({exts})... (Ctrl+C to stop)")

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
    lens_dir = path / ".lens"

    # Check if already initialized
    is_initialized = lens_dir.exists() and (lens_dir / "graph.db").exists()

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

    # Show appropriate next steps
    if not mcp_installed:
        print("Next steps:")
        print("  1. Install MCP: pip install 'lenspr[mcp]'")
        if not is_initialized:
            print("  2. Run: lenspr init .")
            print("  3. Restart Claude Code (Cmd+Q / Alt+F4, then reopen)")
        else:
            print("  2. Restart Claude Code (Cmd+Q / Alt+F4, then reopen)")
    elif not is_initialized:
        print("Next steps:")
        print("  1. Run: lenspr init .")
        print("  2. Restart Claude Code (Cmd+Q / Alt+F4, then reopen)")
    else:
        print("✓ Ready! Restart Claude Code (Cmd+Q / Alt+F4, then reopen)")
        print("  The lens_* tools will be available after restart.")


def cmd_doctor(args: argparse.Namespace) -> None:
    """Check project configuration and diagnose issues."""
    from lenspr.doctor import format_doctor_report, run_doctor

    path = Path(args.path).resolve()
    report = run_doctor(path)
    print(format_doctor_report(report))


def cmd_annotate(args: argparse.Namespace) -> None:
    """Show annotation coverage or auto-annotate nodes."""
    import lenspr
    from lenspr import database
    from lenspr.tools.patterns import auto_annotate

    path = str(Path(args.path).resolve())
    try:
        ctx, _ = lenspr.init(path)
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Collect nodes to annotate
    nodes_to_annotate = []

    if args.node:
        # Single node
        node = database.get_node(args.node, ctx.graph_db)
        if not node:
            print(f"Error: Node not found: {args.node}", file=sys.stderr)
            sys.exit(1)
        nodes_to_annotate = [node]

    elif args.nodes:
        # Multiple nodes
        for node_id in args.nodes:
            node = database.get_node(node_id, ctx.graph_db)
            if not node:
                print(f"Warning: Node not found: {node_id}", file=sys.stderr)
                continue
            nodes_to_annotate.append(node)

    elif args.file:
        # All nodes in a file
        all_nodes = database.get_nodes(ctx.graph_db, file_filter=args.file)
        nodes_to_annotate = [
            n for n in all_nodes if n.type.value in ("function", "method", "class")
        ]

    elif args.auto:
        # All unannotated nodes
        all_nodes = database.get_nodes(ctx.graph_db)
        nodes_to_annotate = [
            n for n in all_nodes
            if n.type.value in ("function", "method", "class")
            and (not n.is_annotated or args.force)
        ]

    # If we have nodes to annotate, do it
    if nodes_to_annotate:
        print(f"Annotating {len(nodes_to_annotate)} nodes...")
        print()
        success_count = 0
        for node in nodes_to_annotate:
            # Auto-detect role and side_effects
            auto = auto_annotate(
                name=node.name,
                node_type=node.type.value,
                source_code=node.source_code or "",
            )

            # Save annotation (without summary - Claude provides that)
            result = database.save_annotation(
                node_id=node.id,
                db_path=ctx.graph_db,
                summary=None,  # Summary should be provided by Claude
                role=auto["role"],
                side_effects=auto["side_effects"],
            )

            if result:
                success_count += 1
                short_id = node.id[:50] + "..." if len(node.id) > 50 else node.id
                print(f"  ✓ {short_id:<53} role={auto['role']}")
            else:
                print(f"  ✗ {node.id} - failed to save")

        print()
        print(f"Annotated {success_count}/{len(nodes_to_annotate)} nodes")
        print()
        print("Note: Only role and side_effects were auto-detected.")
        print("      For summaries, use Claude Code: 'Annotate my codebase'")
        return

    # Default: show stats and instructions
    stats = lenspr.handle_tool("lens_annotation_stats", {})
    data = stats.get("data", {})
    total = data.get("total_annotatable", 0)
    annotated = data.get("annotated", 0)
    coverage = data.get("coverage_pct", 0)

    print("=" * 60)
    print("SEMANTIC ANNOTATIONS")
    print("=" * 60)
    print()
    print(f"Coverage: {annotated}/{total} nodes ({coverage:.1f}%)")
    print()
    print("CLI Commands:")
    print("  lenspr annotate .              # Show this help")
    print("  lenspr annotate . --auto       # Auto-annotate all (role/side_effects only)")
    print("  lenspr annotate . --node X     # Annotate specific node")
    print("  lenspr annotate . --nodes X Y  # Annotate multiple nodes")
    print("  lenspr annotate . --file F     # Annotate all nodes in file")
    print("  lenspr annotate . --force      # Overwrite existing annotations")
    print()
    print("For full annotations with summaries, use Claude Code:")
    print('  Ask: "Annotate my codebase" or "Аннотируй все функции"')
    print()
    print("=" * 60)


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
