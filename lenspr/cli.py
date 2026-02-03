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
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_search.add_argument(
        "--in",
        dest="search_in",
        default="all",
        choices=["name", "code", "docstring", "all"],
        help="Where to search (default: all)",
    )

    # -- impact --
    p_impact = subparsers.add_parser("impact", help="Check impact of changing a node")
    p_impact.add_argument("node_id", help="Node identifier (e.g. app.models.User)")
    p_impact.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_impact.add_argument("--depth", type=int, default=2, help="Traversal depth (default: 2)")

    # -- watch --
    p_watch = subparsers.add_parser("watch", help="Watch for changes and auto-sync")
    p_watch.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")

    # -- serve --
    p_serve = subparsers.add_parser("serve", help="Start MCP server (stdio transport)")
    p_serve.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")

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
    }
    handlers[args.command](args)


def cmd_init(args: argparse.Namespace) -> None:
    import lenspr

    path = str(Path(args.path).resolve())
    try:
        lenspr.init(path, force=args.force)
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    ctx = lenspr.get_context()
    g = ctx.get_graph()
    print(f"Initialized LensPR at {path}")
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
            "  pip install lenspr[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    path = str(Path(args.path).resolve())
    run_server(path)


if __name__ == "__main__":
    main()
