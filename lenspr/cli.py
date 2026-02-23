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
    p_setup.add_argument(
        "--no-interactive", dest="no_interactive", action="store_true",
        help="Skip interactive tool group selection"
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

    # -- architecture --
    p_arch = subparsers.add_parser(
        "architecture",
        help="Analyze codebase architecture: metrics, components, largest classes"
    )
    p_arch.add_argument("path", nargs="?", default=".", help="Project root (default: cwd)")
    p_arch.add_argument(
        "--metrics", action="store_true",
        help="Show project-wide class metrics"
    )
    p_arch.add_argument(
        "--components", action="store_true",
        help="Show only components with cohesion metrics"
    )
    p_arch.add_argument(
        "--explain", metavar="NODE_ID",
        help="Show class metrics for a specific class"
    )
    p_arch.add_argument(
        "--largest", type=int, metavar="N", default=0,
        help="Show N largest classes by method count"
    )
    p_arch.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )

    # -- tools --
    p_tools = subparsers.add_parser(
        "tools",
        help="Manage tool groups (enable/disable to save context window)"
    )
    p_tools.add_argument("--path", default=".", help="Project root (default: cwd)")
    tools_sub = p_tools.add_subparsers(dest="tools_action")

    tools_sub.add_parser("list", help="Show all tool groups with status")

    p_enable = tools_sub.add_parser("enable", help="Enable tool groups")
    p_enable.add_argument("groups", nargs="+", help="Group names to enable")

    p_disable = tools_sub.add_parser("disable", help="Disable tool groups")
    p_disable.add_argument("groups", nargs="+", help="Group names to disable")

    tools_sub.add_parser("reset", help="Re-enable all tool groups")

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
        "architecture": cmd_architecture,
        "tools": cmd_tools,
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
            pkg = monorepo.packages[0]
            print(f"Detected JS/TS package: {pkg.name or pkg.path.name}")

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

    _print_init_summary(ctx, stats, path)


def _print_init_summary(ctx, stats, path: Path) -> None:
    """Print clean summary after lenspr init."""
    from lenspr.database import load_graph

    g = ctx.get_graph()
    all_nodes, all_edges = load_graph(str(ctx.graph_db))

    # ── Section 1: Project files ──────────────────────────────────────

    code_files = stats.total_files if stats else 0
    unparsed_count = sum(stats.unparsed_extensions.values()) if stats else 0
    skipped_in_dirs = stats.total_skipped if stats else 0
    total_project = stats.total_project_files if stats else 0
    other_files = total_project - code_files - skipped_in_dirs

    # Per-language file counts
    lang_files: dict[str, int] = {}
    py_exts = {".py"}
    ts_exts = {".ts", ".tsx"}
    js_exts = {".js", ".jsx"}
    for node in all_nodes:
        if not node.file_path:
            continue
        ext = Path(node.file_path).suffix.lower()
        if ext in py_exts:
            lang_files.setdefault("Python (.py)", set()).add(node.file_path)
        elif ext in ts_exts:
            lang_files.setdefault("TypeScript (.ts/.tsx)", set()).add(node.file_path)
        elif ext in js_exts:
            lang_files.setdefault("JavaScript (.js/.jsx)", set()).add(node.file_path)

    print("  ── Project files ──────────────────────────────────")
    print()
    print(f"  Total files found:        {total_project:,}")
    print()

    code_pct = round(code_files / total_project * 100) if total_project else 0
    print(f"  Code files (parseable):   {code_files:,}  ({code_pct}%)")
    for lang_name in ["Python (.py)", "TypeScript (.ts/.tsx)", "JavaScript (.js/.jsx)"]:
        fset = lang_files.get(lang_name)
        if fset:
            print(f"    {lang_name}:{' ' * max(1, 25 - len(lang_name))}{len(fset):>5}")
    print()

    # Infrastructure files (processed by mappers, not AST parsers)
    if stats and stats.infra_files:
        infra_total = sum(stats.infra_files.values())
        infra_pct = round(infra_total / total_project * 100) if total_project else 0
        print(f"  Infrastructure files:      {infra_total:,}  ({infra_pct}%)")
        parts = [f"{label}: {cnt}" for label, cnt in sorted(stats.infra_files.items())]
        print(f"    {', '.join(parts)}")
        print()

    # Filter infra-tracked extensions from "Other files"
    infra_exts: set[str] = set()
    if stats and stats.infra_files:
        _ext_map = {
            "SQL files (.sql)": {".sql"},
            "Docker Compose": {".yml", ".yaml"},
            "CI workflows (.yml)": {".yml", ".yaml"},
        }
        for infra_label in stats.infra_files:
            infra_exts |= _ext_map.get(infra_label, set())

    remaining_unparsed = {
        ext: cnt for ext, cnt in (stats.unparsed_extensions if stats else {}).items()
        if ext not in infra_exts
    } if stats else {}

    remaining_count = sum(remaining_unparsed.values())
    other_adjusted = max(0, other_files) - (unparsed_count - remaining_count)
    other_pct = round(max(0, other_adjusted) / total_project * 100) if total_project else 0
    if remaining_unparsed:
        print(f"  Other files (not parsed): {max(0, other_adjusted):,}  ({other_pct}%)")
        sorted_exts = sorted(remaining_unparsed.items(), key=lambda x: -x[1])
        top = sorted_exts[:6]
        parts = [f"{ext}: {cnt}" for ext, cnt in top]
        remaining = len(sorted_exts) - len(top)
        line = "    " + "  ".join(parts)
        if remaining > 0:
            line += f"  +{remaining} more"
        print(line)
        print()

    if stats and stats.skipped_dirs:
        sorted_dirs = sorted(stats.skipped_dirs.items(), key=lambda x: -x[1])
        parts = [f"{name} ({cnt:,})" for name, cnt in sorted_dirs[:5]]
        remaining = len(sorted_dirs) - 5
        line = ", ".join(parts)
        if remaining > 0:
            line += f", +{remaining} more"
        print(f"  Skipped directories:      {skipped_in_dirs:,} files")
        print(f"    {line}")
        print()

    # ── Section 2: Graph ──────────────────────────────────────────────

    # Count nodes by type
    node_type_counts: dict[str, int] = {}
    for node in all_nodes:
        ntype = node.type.value
        node_type_counts[ntype] = node_type_counts.get(ntype, 0) + 1

    # Count edges by type
    edge_type_counts: dict[str, int] = {}
    for edge in all_edges:
        etype = edge.type.value
        edge_type_counts[etype] = edge_type_counts.get(etype, 0) + 1

    print("  ── Graph ──────────────────────────────────────────")
    print()

    # Nodes
    print(f"  Nodes:  {g.number_of_nodes():,}")
    node_parts = []
    for ntype in ["function", "class", "method", "module", "block"]:
        count = node_type_counts.get(ntype, 0)
        if count > 0:
            node_parts.append(f"{ntype}: {count:,}")
    # Print in rows of 2
    for i in range(0, len(node_parts), 2):
        pair = node_parts[i:i + 2]
        print(f"    {'    '.join(f'{p:<22}' for p in pair)}")
    print()

    # Edges — grouped by category
    total_edges = g.number_of_edges()
    print(f"  Edges:  {total_edges:,}")

    edge_categories = [
        ("Code", ["calls", "imports", "uses", "inherits", "decorates", "contains", "mocks"]),
        ("Cross-language", ["calls_api", "handles_route", "calls_native"]),
        ("Database", ["reads_table", "writes_table", "migrates"]),
        ("Infrastructure", ["depends_on", "uses_env", "exposes_port"]),
    ]
    for cat_name, types in edge_categories:
        cat_edges = [(t, edge_type_counts.get(t, 0)) for t in types if edge_type_counts.get(t, 0) > 0]
        if not cat_edges:
            continue
        first = True
        for etype, count in cat_edges:
            label = f"{cat_name}:" if first else ""
            print(f"    {label:<20} {etype} {count:,}")
            first = False
    print()

    # Confidence — overall + per-language breakdown
    if stats:
        overall_pct = stats.overall_resolution_pct
        print(f"  Confidence: {overall_pct:.0f}%")
        print("  (= resolved edges / total internal edges)")

        # Per-language breakdown
        for lang_name, lang_stats in sorted(stats.languages.items()):
            total_lang = lang_stats.total_edges
            if total_lang == 0:
                continue
            resolved = lang_stats.resolved_edges
            external = lang_stats.external_edges
            resolved_total = resolved + external
            pct = round(resolved_total / total_lang * 100) if total_lang else 0
            warn = " ⚠" if pct < 80 else ""
            print(
                f"    {lang_name + ':':<16} {pct:>3}% resolved"
                f" ({resolved_total:,} / {total_lang:,}){warn}"
            )
        print()

    # ── Section 3: Footer ─────────────────────────────────────────────

    db_size = (ctx.lens_dir / "graph.db").stat().st_size / 1024
    db_size_str = f"{db_size / 1024:.1f} MB" if db_size > 1024 else f"{db_size:.0f} KB"

    if stats:
        print(f"  Parse time: {stats.total_time_ms / 1000:.1f}s")
    print(f"  Database:   .lens/graph.db ({db_size_str})")
    print()

    # Warnings
    if stats and stats.warnings:
        print("  Warnings:")
        for w in stats.warnings:
            print(f"    {w}")
        print()

    print("  Next steps:")
    print("    lenspr setup .     Configure for Claude Code")
    print("    lenspr status .    View detailed stats")


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
    result = get_structure(g)

    print(f"Project: {path}")
    print(f"  Nodes: {g.number_of_nodes()}")
    print(f"  Edges: {g.number_of_edges()}")
    print(f"  Files: {result['pagination']['total_files']}")


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

    # Interactive tool group selection
    if not args.no_interactive and sys.stdin.isatty():
        print()
        _configure_tools_interactive(path / ".lens" / "config.json")

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


def cmd_tools(args: argparse.Namespace) -> None:
    """Manage tool groups (enable/disable to save context window)."""
    from lenspr.tool_groups import (
        ALL_GROUPS,
        ALWAYS_ON,
        TOOL_GROUPS,
        load_tool_config,
        save_tool_config,
    )

    path = Path(args.path).resolve()
    config_path = path / ".lens" / "config.json"

    action = args.tools_action
    if action is None:
        action = "list"

    if action == "list":
        enabled = load_tool_config(config_path)
        if enabled is None:
            enabled = ALL_GROUPS
        enabled_set = set(enabled) | ALWAYS_ON

        print("Tool Groups")
        print("=" * 70)
        total_enabled = 0
        total_tools = 0
        for name, info in TOOL_GROUPS.items():
            count = len(info["tools"])
            total_tools += count
            is_on = name in enabled_set
            if is_on:
                total_enabled += count
            always = " (always on)" if name in ALWAYS_ON else ""
            status = "ON " if is_on else "OFF"
            tool_word = "tool" if count == 1 else "tools"
            print(f"  [{status}] {name:<16} ({count:>2} {tool_word})  {info['description']}{always}")
        print()
        print(f"  {total_enabled}/{total_tools} tools enabled")
        print()
        print("  lenspr tools enable <group>   — enable a group")
        print("  lenspr tools disable <group>  — disable a group")
        print("  lenspr tools reset            — re-enable all groups")

    elif action == "enable":
        enabled = load_tool_config(config_path)
        if enabled is None:
            enabled = list(ALL_GROUPS)
        enabled_set = set(enabled) | ALWAYS_ON
        for g in args.groups:
            if g not in TOOL_GROUPS:
                print(f"Unknown group: {g}")
                print(f"Available: {', '.join(ALL_GROUPS)}")
                sys.exit(1)
            enabled_set.add(g)
        save_tool_config(config_path, sorted(enabled_set))
        print(f"✓ Enabled: {', '.join(args.groups)}")
        print("  Restart MCP server to apply.")

    elif action == "disable":
        enabled = load_tool_config(config_path)
        if enabled is None:
            enabled = list(ALL_GROUPS)
        enabled_set = set(enabled) | ALWAYS_ON
        for g in args.groups:
            if g not in TOOL_GROUPS:
                print(f"Unknown group: {g}")
                print(f"Available: {', '.join(ALL_GROUPS)}")
                sys.exit(1)
            if g in ALWAYS_ON:
                print(f"Cannot disable '{g}' — it is always on.")
                continue
            enabled_set.discard(g)
        save_tool_config(config_path, sorted(enabled_set))
        disabled = sorted(set(ALL_GROUPS) - enabled_set)
        if disabled:
            print(f"✓ Disabled: {', '.join(disabled)}")
        total_on = sum(len(TOOL_GROUPS[g]["tools"]) for g in enabled_set if g in TOOL_GROUPS)
        total_all = sum(len(info["tools"]) for info in TOOL_GROUPS.values())
        print(f"  {total_on}/{total_all} tools enabled")
        print("  Restart MCP server to apply.")

    elif action == "reset":
        save_tool_config(config_path, list(ALL_GROUPS))
        total = sum(len(info["tools"]) for info in TOOL_GROUPS.values())
        print(f"✓ All {total} tools re-enabled.")
        print("  Restart MCP server to apply.")


def _configure_tools_interactive(config_path: Path) -> None:
    """Interactive tool group selection during setup."""
    from lenspr.tool_groups import ALL_GROUPS, ALWAYS_ON, TOOL_GROUPS, save_tool_config

    total = sum(len(info["tools"]) for info in TOOL_GROUPS.values())
    print(f"LensPR has {total} tools organized into {len(TOOL_GROUPS)} groups.")
    print("All groups are enabled by default. Disable unneeded groups to save context window.")
    print()

    group_list = list(TOOL_GROUPS.keys())
    for i, name in enumerate(group_list, 1):
        info = TOOL_GROUPS[name]
        count = len(info["tools"])
        tool_word = "tool" if count == 1 else "tools"
        suffix = "  [always on]" if name in ALWAYS_ON else "  [ON]"
        print(f"  [{i:>2}] {name:<16} ({count:>2} {tool_word})  {info['description']}{suffix}")
    print()

    try:
        answer = input("Enter numbers to toggle OFF (comma-separated), 'all' to keep all, 'q' to skip: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not answer or answer.lower() in ("q", "skip"):
        return
    if answer.lower() == "all":
        print("  All groups enabled.")
        return

    # Parse numbers
    to_disable: set[str] = set()
    for part in answer.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        idx = int(part) - 1
        if 0 <= idx < len(group_list):
            name = group_list[idx]
            if name in ALWAYS_ON:
                print(f"  Cannot disable '{name}' — it is always on.")
            else:
                to_disable.add(name)

    if not to_disable:
        print("  No groups disabled.")
        return

    enabled = sorted(set(ALL_GROUPS) - to_disable)
    save_tool_config(config_path, enabled)

    disabled_count = sum(len(TOOL_GROUPS[g]["tools"]) for g in to_disable)
    enabled_count = total - disabled_count
    print(f"\n  Disabled: {', '.join(sorted(to_disable))}")
    print(f"  Enabled: {enabled_count}/{total} tools ({disabled_count} disabled)")
    print(f"  Saved to {config_path}")


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


def cmd_architecture(args: argparse.Namespace) -> None:
    """Analyze codebase architecture using pre-computed metrics."""
    import lenspr
    from lenspr import database
    from lenspr.tools.arch import (
        handle_class_metrics,
        handle_components,
        handle_largest_classes,
        handle_project_metrics,
    )

    path = Path(args.path).resolve()
    try:
        ctx, _ = lenspr.init(str(path))
    except lenspr.LensError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Handle --explain flag (class metrics)
    if args.explain:
        result = handle_class_metrics({"node_id": args.explain}, ctx)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)

        data = result.data or {}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print(f"CLASS METRICS: {args.explain}")
            print(f"{'=' * 60}\n")

            print(f"Name: {data.get('name', 'unknown')}")
            print(f"Methods: {data.get('method_count', 0)}")
            print(f"Lines: {data.get('lines', 0)}")
            print(f"Public/Private: {data.get('public_methods', 0)}/{data.get('private_methods', 0)}")
            print(f"Dependencies: {data.get('dependency_count', 0)}")
            print(f"Internal calls: {data.get('internal_calls', 0)}")
            print(f"Percentile rank: {data.get('percentile_rank', 0):.1f}%")
            print()

            prefixes = data.get("method_prefixes", {})
            if prefixes:
                print("Method prefixes:")
                for prefix, count in list(prefixes.items())[:5]:
                    print(f"  {prefix}_*: {count}")
            print()

            deps = data.get("dependencies", [])
            if deps:
                print(f"Dependencies ({len(deps)}):")
                for dep in deps[:10]:
                    print(f"  → {dep}")

        return

    # Handle --largest flag
    if args.largest:
        result = handle_largest_classes({"limit": args.largest}, ctx)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)

        data = result.data or {}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print(f"LARGEST CLASSES (top {args.largest})")
            print(f"{'=' * 60}\n")

            for cls in data.get("classes", []):
                print(f"  {cls['name']}: {cls['method_count']} methods")
                print(f"    {cls['lines']} lines, {cls['dependency_count']} deps, p{cls['percentile_rank']:.0f}")
                print()

        return

    # Handle --components flag
    if args.components:
        result = handle_components({}, ctx)
        if not result.success:
            print(f"Error: {result.error}", file=sys.stderr)
            sys.exit(1)

        data = result.data or {}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print("COMPONENTS")
            print(f"{'=' * 60}\n")

            components = data.get("components", [])
            if not components:
                print("  No components detected.")
            else:
                for c in components:
                    print(f"  {c['name']}")
                    print(f"    Path: {c['path']}")
                    print(f"    Cohesion: {c['cohesion']:.0%}")
                    print(f"    Classes: {len(c.get('classes', []))}")
                    print()

            print(f"Total: {data.get('count', 0)} components, avg cohesion: {data.get('avg_cohesion', 0):.0%}")

        return

    # Default: show project metrics + largest classes
    proj_result = handle_project_metrics({}, ctx)
    largest_result = handle_largest_classes({"limit": 10}, ctx)

    if args.json:
        output = {
            "project_metrics": proj_result.data if proj_result.success else None,
            "largest_classes": largest_result.data if largest_result.success else None,
        }
        print(json.dumps(output, indent=2))
        return

    print(f"\n{'=' * 60}")
    print("ARCHITECTURE METRICS")
    print(f"{'=' * 60}\n")

    if proj_result.success and proj_result.data:
        pm = proj_result.data
        print("Project-wide class statistics:")
        print(f"  Total classes: {pm.get('total_classes', 0)}")
        print(f"  Methods: avg={pm.get('avg_methods', 0)}, median={pm.get('median_methods', 0)}")
        print(f"  Range: {pm.get('min_methods', 0)} - {pm.get('max_methods', 0)}")
        print(f"  Percentiles: p90={pm.get('p90_methods', 0)}, p95={pm.get('p95_methods', 0)}")
        print()
    else:
        print("No project metrics found. Run 'lenspr init --force' to compute.")
        print()

    if largest_result.success and largest_result.data:
        classes = largest_result.data.get("classes", [])
        if classes:
            print("Largest classes:")
            for cls in classes[:10]:
                print(f"  {cls['name']}: {cls['method_count']} methods (p{cls['percentile_rank']:.0f})")
        print()

    print("Use --explain <class> to see detailed metrics for a class.")
    print("Use --components to see component cohesion metrics.")


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
