"""Analysis and safety tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lenspr import database, graph
from lenspr.models import ToolResponse
from lenspr.tools.helpers import get_proactive_warnings
from lenspr.validator import validate_full

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_check_impact(params: dict, ctx: LensContext) -> ToolResponse:
    """Analyze what would be affected by changing a node."""
    ctx.ensure_synced()
    nx_graph = ctx.get_graph()
    node_id = params["node_id"]
    depth = params.get("depth", 2)
    impact = graph.get_impact_zone(nx_graph, node_id, depth)

    # Calculate severity based on impact
    total_affected = impact.get("total_affected", 0)
    direct_callers = impact.get("direct_callers", [])
    inheritors = impact.get("inheritors", [])
    untracked = impact.get("untracked_warnings", [])

    # Determine severity level
    if total_affected > 20 or len(inheritors) > 0:
        severity = "CRITICAL"
        severity_reason = (
            f"{total_affected} affected nodes"
            + (f", {len(inheritors)} inheritors" if inheritors else "")
        )
    elif total_affected > 10 or len(untracked) > 0:
        severity = "HIGH"
        severity_reason = f"{total_affected} affected nodes"
        if untracked:
            severity_reason += f", {len(untracked)} untracked calls"
    elif total_affected > 5:
        severity = "MEDIUM"
        severity_reason = f"{total_affected} affected nodes"
    else:
        severity = "LOW"
        severity_reason = f"{total_affected} affected nodes"

    # Check for tests
    has_tests = False
    node_data = nx_graph.nodes.get(node_id, {})
    node_name = node_data.get("name", "")

    for pred_id in direct_callers:
        pred_data = nx_graph.nodes.get(pred_id, {})
        pred_name = pred_data.get("name", "")
        pred_file = pred_data.get("file_path", "")
        if pred_name.startswith("test_") or "test_" in pred_file:
            has_tests = True
            break

    if not has_tests:
        test_nodes = database.search_nodes(
            f"test_{node_name}", ctx.graph_db, search_in="name"
        )
        has_tests = len(test_nodes) > 0

    impact["severity"] = severity
    impact["severity_reason"] = severity_reason
    impact["has_tests"] = has_tests

    warnings: list[str] = []
    if severity == "CRITICAL":
        warnings.append(
            f"⚠️ CRITICAL: Changing this node affects {total_affected} nodes. "
            "Review carefully before proceeding."
        )
    elif severity == "HIGH":
        warnings.append(
            f"⚠️ HIGH IMPACT: This change affects {total_affected} nodes."
        )
    if not has_tests:
        warnings.append("⚠️ NO TESTS: Consider adding tests before modifying.")
    if untracked:
        warnings.append(
            f"⚠️ UNTRACKED CALLS: {len(untracked)} calls cannot be statically traced."
        )

    return ToolResponse(success=True, data=impact, warnings=warnings)


def handle_validate_change(params: dict, ctx: LensContext) -> ToolResponse:
    """Dry-run validation without applying changes."""
    ctx.ensure_synced()
    node_id = params["node_id"]
    new_source = params["new_source"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    proactive_warnings = get_proactive_warnings(node_id, new_source, ctx)
    validation = validate_full(new_source, node)
    nx_graph = ctx.get_graph()
    impact = graph.get_impact_zone(nx_graph, node_id, depth=2)
    all_warnings = proactive_warnings + validation.warnings

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "would_apply": validation.valid,
            "validation": {
                "valid": validation.valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            "proactive_warnings": proactive_warnings,
            "impact": {
                "direct_callers": impact.get("direct_callers", []),
                "indirect_callers": impact.get("indirect_callers", []),
                "inheritors": impact.get("inheritors", []),
                "total_affected": impact.get("total_affected", 0),
                "untracked_warnings": impact.get("untracked_warnings", []),
            },
        },
        warnings=all_warnings,
    )


def handle_diff(params: dict, ctx: LensContext) -> ToolResponse:
    """Compare current filesystem against graph DB without syncing."""
    parser = ctx._parser
    extensions = set(parser.get_file_extensions())
    skip_dirs = {
        "__pycache__", ".git", ".lens", ".venv", "venv", "env",
        "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", ".eggs", ".tox",
    }

    old_nodes, _ = database.load_graph(ctx.graph_db)
    old_by_file: dict[str, list[dict[str, Any]]] = {}
    for n in old_nodes:
        old_by_file.setdefault(n.file_path, []).append({
            "id": n.id, "name": n.name, "type": n.type.value, "hash": n.hash,
        })
    old_files = set(old_by_file.keys())

    current_files: set[str] = set()
    for file_path in sorted(ctx.project_root.rglob("*")):
        if not file_path.is_file():
            continue
        if any(part in skip_dirs for part in file_path.parts):
            continue
        if file_path.suffix not in extensions:
            continue
        current_files.add(str(file_path.relative_to(ctx.project_root)))

    fingerprints = ctx._load_fingerprints()

    added_files: list[str] = sorted(current_files - old_files)
    deleted_files: list[str] = sorted(old_files - current_files)
    modified_files: list[str] = []

    for rel in sorted(current_files & old_files):
        file_path = ctx.project_root / rel
        stat = file_path.stat()
        old_fp = fingerprints.get(rel, {})
        if (
            stat.st_mtime != old_fp.get("mtime")
            or stat.st_size != old_fp.get("size")
        ):
            modified_files.append(rel)

    return ToolResponse(
        success=True,
        data={
            "added_files": added_files,
            "deleted_files": deleted_files,
            "modified_files": modified_files,
            "total_changes": (
                len(added_files) + len(deleted_files) + len(modified_files)
            ),
            "deleted_nodes": [
                node_info
                for f in deleted_files
                for node_info in old_by_file.get(f, [])
            ],
        },
    )


def handle_health(params: dict, ctx: LensContext) -> ToolResponse:
    """Generate health report for the code graph."""
    ctx.ensure_synced()
    nx_graph = ctx.get_graph()

    project_nodes = 0
    external_refs = 0
    nodes_by_type: dict[str, int] = {}
    nodes_without_docstring = 0

    for _nid, data in nx_graph.nodes(data=True):
        ntype = data.get("type")
        if ntype is None:
            external_refs += 1
            continue
        project_nodes += 1
        nodes_by_type[ntype] = nodes_by_type.get(ntype, 0) + 1
        if ntype in ("function", "method", "class") and not data.get("docstring"):
            nodes_without_docstring += 1

    total_edges = nx_graph.number_of_edges()
    edges_by_type: dict[str, int] = {}
    edges_by_confidence: dict[str, int] = {}
    unresolved_edges: list[dict[str, str]] = []
    internal_resolved = 0
    internal_total = 0
    external_count = 0

    for u, v, data in nx_graph.edges(data=True):
        etype = data.get("type", "unknown")
        edges_by_type[etype] = edges_by_type.get(etype, 0) + 1
        conf = data.get("confidence", "unknown")
        edges_by_confidence[conf] = edges_by_confidence.get(conf, 0) + 1

        if conf == "external":
            external_count += 1
        else:
            internal_total += 1
            if conf == "resolved":
                internal_resolved += 1

        if conf == "unresolved":
            reason = data.get("untracked_reason", "")
            unresolved_edges.append({"from": u, "to": v, "reason": reason})

    cycles = graph.detect_circular_imports(nx_graph)

    internal_confidence_pct = (
        (internal_resolved / internal_total * 100) if internal_total > 0 else 100.0
    )

    documentable = (
        nodes_by_type.get("function", 0)
        + nodes_by_type.get("method", 0)
        + nodes_by_type.get("class", 0)
    )
    docstring_pct = (
        ((documentable - nodes_without_docstring) / documentable * 100)
        if documentable > 0
        else 100.0
    )

    return ToolResponse(
        success=True,
        data={
            "total_nodes": project_nodes,
            "external_refs": external_refs,
            "nodes_by_type": nodes_by_type,
            "total_edges": total_edges,
            "edges_by_type": edges_by_type,
            "edges_by_confidence": edges_by_confidence,
            "internal_edges": {
                "total": internal_total,
                "resolved": internal_resolved,
                "confidence_pct": round(internal_confidence_pct, 1),
            },
            "external_edges": external_count,
            "confidence_pct": round(internal_confidence_pct, 1),
            "nodes_without_docstring": nodes_without_docstring,
            "docstring_pct": round(docstring_pct, 1),
            "circular_imports": cycles,
            "unresolved_edges": unresolved_edges[:20],
            "unresolved_count": len(unresolved_edges),
        },
    )


def handle_dependencies(params: dict, ctx: LensContext) -> ToolResponse:
    """List all external dependencies (stdlib and third-party)."""
    ctx.ensure_synced()

    import sys
    from collections import defaultdict

    nx_graph = ctx.get_graph()
    group_by = params.get("group_by", "package")

    stdlib_names: set[str]
    try:
        stdlib_names = set(sys.stdlib_module_names)
    except AttributeError:
        from lenspr.parsers.python_parser import _STDLIB_MODULES
        stdlib_names = _STDLIB_MODULES

    from lenspr.parsers.python_parser import _BUILTINS

    deps_by_package: dict[str, dict] = defaultdict(
        lambda: {"usages": 0, "files": set()}
    )
    deps_by_file: dict[str, list] = defaultdict(list)

    # Collect all project module names (both root packages and submodules)
    project_modules: set[str] = set()
    for _, data in nx_graph.nodes(data=True):
        if data.get("type") == "module":
            qname = data.get("qualified_name", "")
            if qname:
                # Add the root package
                project_modules.add(qname.split(".")[0])
                # Also add all parts of the qualified name as potential imports
                # e.g., "backend.config" -> add "backend", "config"
                for part in qname.split("."):
                    project_modules.add(part)

    for u, v, data in nx_graph.edges(data=True):
        edge_type = data.get("type", "")
        conf = data.get("confidence", "")

        is_external_call = conf == "external"
        is_external_import = (
            edge_type == "imports" and v.split(".")[0] not in project_modules
        )

        if not (is_external_call or is_external_import):
            continue

        target = v
        package = target.split(".")[0] if target else ""
        if not package:
            continue

        if package in _BUILTINS:
            pkg_type = "builtin"
        elif package in stdlib_names:
            pkg_type = "stdlib"
        else:
            pkg_type = "third-party"

        source_node = nx_graph.nodes.get(u, {})
        source_file = source_node.get("file_path", "unknown")

        deps_by_package[package]["usages"] += 1
        deps_by_package[package]["files"].add(source_file)
        deps_by_package[package]["type"] = pkg_type

        deps_by_file[source_file].append({
            "package": package,
            "target": target,
            "type": pkg_type,
        })

    if group_by == "file":
        result = [
            {
                "file": fp,
                "dependencies": sorted(deps, key=lambda x: x["package"]),
                "count": len(deps),
            }
            for fp, deps in sorted(deps_by_file.items())
        ]
        return ToolResponse(
            success=True,
            data={"by_file": result, "total_files": len(result)},
        )
    else:
        builtin_deps = []
        stdlib_deps = []
        third_party_deps = []
        for pkg, info in sorted(deps_by_package.items()):
            entry = {
                "package": pkg,
                "type": info["type"],
                "usages": info["usages"],
                "used_in_files": len(info["files"]),
            }
            if info["type"] == "builtin":
                builtin_deps.append(entry)
            elif info["type"] == "stdlib":
                stdlib_deps.append(entry)
            else:
                third_party_deps.append(entry)

        return ToolResponse(
            success=True,
            data={
                "dependencies": builtin_deps + stdlib_deps + third_party_deps,
                "total_packages": len(deps_by_package),
                "builtin_count": len(builtin_deps),
                "stdlib_count": len(stdlib_deps),
                "third_party_count": len(third_party_deps),
            },
        )


def handle_dead_code(params: dict, ctx: LensContext) -> ToolResponse:
    """Find potentially dead code not reachable from entry points."""
    ctx.ensure_synced()
    nx_graph = ctx.get_graph()

    entry_points: list[str] = params.get("entry_points", [])

    if not entry_points:
        # Collect all module-level __all__ exports
        public_api: set[str] = set()
        for nid, data in nx_graph.nodes(data=True):
            if data.get("type") == "module":
                # Check if module has __all__ in its source
                source = data.get("source_code", "")
                if "__all__" in source:
                    # Mark all top-level functions/classes in this module as public
                    module_prefix = nid + "."
                    for other_nid in nx_graph.nodes():
                        if other_nid.startswith(module_prefix):
                            # Only direct children (no more dots after prefix)
                            remainder = other_nid[len(module_prefix):]
                            if "." not in remainder:
                                public_api.add(other_nid)

        # Use a set to avoid duplicates
        entry_set: set[str] = set()

        for nid, data in nx_graph.nodes(data=True):
            name = data.get("name", "")
            node_type = data.get("type", "")
            file_path = data.get("file_path", "")
            source = data.get("source_code", "")

            # === ALWAYS entry points (unconditional) ===

            # 1. Main entry points
            if name in ("main", "__main__"):
                entry_set.add(nid)

            # 2. Tests
            if name.startswith("test_") or file_path.startswith("tests/"):
                entry_set.add(nid)

            # 3. Module-level blocks (if __name__ == "__main__", etc.)
            if node_type == "block":
                entry_set.add(nid)

            # 4. All classes (their methods are reached through them)
            if node_type == "class":
                entry_set.add(nid)

            # 5. Public API (in __all__)
            if nid in public_api:
                entry_set.add(nid)

            # 6. Top-level functions in __init__.py (package public API)
            if file_path.endswith("__init__.py") and node_type == "function":
                # Check if it's a top-level function (only one dot after package name)
                parts = nid.split(".")
                # For "lenspr.init" -> parts = ["lenspr", "init"] -> top-level
                # For "lenspr.subpkg.func" -> parts = ["lenspr", "subpkg", "func"]
                if len(parts) == 2:  # package.function format
                    entry_set.add(nid)

            # === CLI entry points ===

            # 6. CLI module and functions
            if name in ("cli", "app", "run", "main_cli"):
                entry_set.add(nid)

            # 7. CLI commands (cmd_init, cmd_sync, etc.)
            if name.startswith("cmd_"):
                entry_set.add(nid)

            # === MCP and tool handlers ===

            # 8. MCP tool functions (lens_* inside mcp_server.py)
            if "mcp_server" in file_path and name.startswith("lens_"):
                entry_set.add(nid)

            # 9. Tool handlers (handle_* functions) - dynamically dispatched
            if name.startswith("handle_"):
                entry_set.add(nid)

            # 10. Tool call dispatcher
            if name == "handle_tool_call":
                entry_set.add(nid)

            # === Web/API handlers ===

            # 11. Web handler patterns
            if node_type == "function" and any(
                pattern in name
                for pattern in ["_handler", "_endpoint", "_view", "_route"]
            ):
                entry_set.add(nid)

            # 12. Web framework routes (FastAPI, Flask, etc.)
            # Functions in router/routes/views/api files are likely endpoints
            if node_type == "function" and any(
                pattern in file_path
                for pattern in [
                    "/router", "/routes", "/views", "/api/",
                    "router.py", "routes.py", "views.py", "endpoints.py",
                ]
            ):
                entry_set.add(nid)

            # 13. Functions with web framework decorators in source
            if node_type == "function":
                # Look for common web framework decorator patterns
                if any(
                    pattern in source
                    for pattern in [
                        "@app.", "@router.", "@bp.", "@api.",  # FastAPI, Flask, Quart
                        "@route", "@get", "@post", "@put", "@delete", "@patch",
                        "@websocket",
                        "Depends(",  # FastAPI dependency injection
                        "@st.cache", "@st.experimental",  # Streamlit
                        "st.button(", "st.form(",  # Streamlit callbacks
                    ]
                ):
                    entry_set.add(nid)

            # 13b. Streamlit app files (frontend/*.py)
            if node_type == "function" and "frontend" in file_path:
                entry_set.add(nid)

            # === Database migrations (Alembic, Django, etc.) ===

            # 14. Alembic migration functions
            if name in ("upgrade", "downgrade", "run_migrations_online",
                        "run_migrations_offline"):
                entry_set.add(nid)

            # 14b. Alembic env.py functions
            if "alembic" in file_path or file_path.endswith("env.py"):
                if node_type == "function":
                    entry_set.add(nid)

            # 14c. Migration version files (alembic/versions/*.py)
            if "/versions/" in file_path or "/migrations/" in file_path:
                if node_type == "function":
                    entry_set.add(nid)

            # === Task queues (Celery, RQ, etc.) ===

            # 15. Celery tasks
            if node_type == "function" and any(
                pattern in source
                for pattern in [
                    "@celery.task", "@app.task", "@shared_task",
                    "@celery_app.task", "celery.Task",
                ]
            ):
                entry_set.add(nid)

            # 15b. RQ tasks
            if node_type == "function" and "@job" in source:
                entry_set.add(nid)

            # === Pytest fixtures ===

            # 16. Pytest fixtures (called dynamically by pytest)
            if node_type == "function" and "@pytest.fixture" in source:
                entry_set.add(nid)

            # 16b. conftest.py functions are usually fixtures
            if file_path.endswith("conftest.py") and node_type == "function":
                entry_set.add(nid)

            # === Django patterns ===

            # 17. Django management commands
            if "/management/commands/" in file_path:
                entry_set.add(nid)

            # 17b. Django signals
            if node_type == "function" and any(
                pattern in source
                for pattern in ["@receiver", "pre_save", "post_save",
                                "pre_delete", "post_delete"]
            ):
                entry_set.add(nid)

            # 17c. Django admin
            if "admin.py" in file_path and node_type in ("class", "function"):
                entry_set.add(nid)

            # === SQLAlchemy events ===

            # 18. SQLAlchemy event listeners
            if node_type == "function" and any(
                pattern in source
                for pattern in ["@event.listens_for", "event.listen"]
            ):
                entry_set.add(nid)

            # === Methods that are called via protocols/conventions ===

            # 19. Dataclass/class special methods
            if node_type == "method" and name in (
                "__init__", "__post_init__", "__new__", "__del__",
                "__repr__", "__str__", "__hash__", "__eq__", "__ne__",
                "__lt__", "__le__", "__gt__", "__ge__",
                "__len__", "__iter__", "__next__", "__getitem__", "__setitem__",
                "__contains__", "__call__", "__enter__", "__exit__",
                "__get__", "__set__", "__delete__",
                "from_dict", "to_dict",  # Common serialization methods
            ):
                entry_set.add(nid)

            # 20. Property methods (is_*, has_*, get_*, set_*)
            if node_type == "method" and (
                name.startswith("is_") or name.startswith("has_") or
                name.startswith("get_") or name.startswith("set_")
            ):
                entry_set.add(nid)

            # 21. Private methods starting with underscore (internal use)
            if node_type == "method" and name.startswith("_") and not name.startswith("__"):
                entry_set.add(nid)

            # === Parser/visitor patterns ===

            # 22. AST visitor methods (visit_*, generic_visit)
            if name.startswith("visit_") or name == "generic_visit":
                entry_set.add(nid)

            # === Helper functions that are used internally ===

            # 23. Detection/analysis helpers (often called via getattr or dict)
            if name.startswith("_detect_") or name.startswith("_compute_"):
                entry_set.add(nid)

            # 24. Enum classes (their values are accessed)
            enum_suffixes = ("Enum", "Role", "Type", "Confidence", "Source")
            if node_type == "class" and name.endswith(enum_suffixes):
                entry_set.add(nid)

            # === Pydantic/dataclass validators ===

            # 25. Pydantic validators
            if node_type == "method" and any(
                pattern in source
                for pattern in ["@validator", "@field_validator",
                                "@root_validator", "@model_validator"]
            ):
                entry_set.add(nid)

            # === Click/Typer CLI commands ===

            # 26. Click/Typer commands
            if node_type == "function" and any(
                pattern in source
                for pattern in ["@click.command", "@click.group",
                                "@app.command", "@typer.command"]
            ):
                entry_set.add(nid)

        # Check for decorated functions
        for nid, data in nx_graph.nodes(data=True):
            if data.get("type") == "function":
                for pred in nx_graph.predecessors(nid):
                    edge_data = nx_graph.edges.get((pred, nid), {})
                    if edge_data.get("type") == "decorates":
                        entry_set.add(nid)
                        break

        # Include all methods of classes that are entry points
        # (Classes don't have edges to their methods in the graph)
        class_entry_points = {
            nid for nid in entry_set
            if nx_graph.nodes.get(nid, {}).get("type") == "class"
        }
        for nid, data in nx_graph.nodes(data=True):
            if data.get("type") == "method":
                # Method ID format: "module.Class.method" → class is "module.Class"
                class_id = nid.rsplit(".", 1)[0]
                if class_id in class_entry_points:
                    entry_set.add(nid)

        # Include nested functions of entry point functions
        # (Nested functions don't have incoming edges from parents)
        function_entry_points = {
            nid for nid in entry_set
            if nx_graph.nodes.get(nid, {}).get("type") == "function"
        }
        for nid, data in nx_graph.nodes(data=True):
            if data.get("type") in ("function", "class"):
                # Check if this is nested inside an entry point function
                parent_id = nid.rsplit(".", 1)[0] if "." in nid else None
                if parent_id and parent_id in function_entry_points:
                    entry_set.add(nid)

        entry_points = list(entry_set)

    dead_code = graph.find_dead_code(nx_graph, entry_points)

    dead_by_file: dict[str, list[dict]] = {}
    for nid in dead_code:
        node_data = nx_graph.nodes.get(nid, {})
        file_path = node_data.get("file_path", "unknown")
        if file_path not in dead_by_file:
            dead_by_file[file_path] = []
        dead_by_file[file_path].append({
            "id": nid,
            "name": node_data.get("name", ""),
            "type": node_data.get("type", ""),
            "start_line": node_data.get("start_line", 0),
        })

    return ToolResponse(
        success=True,
        data={
            "dead_code": dead_code,
            "count": len(dead_code),
            "by_file": dead_by_file,
            "entry_points_used": len(entry_points),
        },
        warnings=[
            "Verify with lens_grep before deleting. Possible false positives: "
            "dynamic dispatch (getattr/eval), string-based imports, "
            "and code used only via external entry points not in the graph."
        ] if dead_code else [],
    )


def _find_usages_for_node(
    node_id: str, nx_graph, db_path: str, include_tests: bool = True,
) -> dict | None:
    """Find usages for a single node. Returns dict or None if not found."""
    node = database.get_node(node_id, db_path)
    if not node:
        return None

    usages: list[dict] = []
    if node_id in nx_graph:
        for pred_id in nx_graph.predecessors(node_id):
            pred_data = nx_graph.nodes.get(pred_id, {})
            pred_file = pred_data.get("file_path", "")
            pred_name = pred_data.get("name", "")

            if not include_tests:
                if pred_name.startswith("test_") or "test_" in pred_file:
                    continue

            edge_data = nx_graph.edges.get((pred_id, node_id), {})
            usages.append({
                "id": pred_id,
                "name": pred_name,
                "type": pred_data.get("type", ""),
                "file_path": pred_file,
                "start_line": pred_data.get("start_line", 0),
                "edge_type": edge_data.get("type", "unknown"),
                "is_test": pred_name.startswith("test_") or "test_" in pred_file,
            })

    callers = [u for u in usages if u["edge_type"] == "calls"]
    importers = [u for u in usages if u["edge_type"] == "imports"]
    inheritors = [u for u in usages if u["edge_type"] == "inherits"]
    other = [
        u for u in usages
        if u["edge_type"] not in ("calls", "imports", "inherits")
    ]

    result = {
        "node_id": node_id,
        "node_name": node.name,
        "total_usages": len(usages),
        "callers": callers,
        "caller_count": len(callers),
        "importers": importers,
        "importer_count": len(importers),
        "inheritors": inheritors,
        "inheritor_count": len(inheritors),
        "other": other,
        "test_usages": len([u for u in usages if u["is_test"]]),
    }

    if len(usages) == 0:
        result["warning"] = (
            "0 usages found in the graph. Before concluding this is dead code, "
            "verify with lens_grep — dynamic dispatch (getattr/importlib), "
            "string-based references, and framework entry points may not appear "
            "in the static graph."
        )

    return result


def handle_find_usages(params: dict, ctx: LensContext) -> ToolResponse:
    """Find all usages of a node (or multiple nodes) across the codebase."""
    ctx.ensure_synced()
    include_tests = params.get("include_tests", True)
    nx_graph = ctx.get_graph()

    # Batch mode: node_ids parameter
    node_ids = params.get("node_ids")
    if node_ids:
        results = []
        not_found = []
        for nid in node_ids:
            result = _find_usages_for_node(nid, nx_graph, ctx.graph_db, include_tests)
            if result:
                results.append(result)
            else:
                not_found.append(nid)

        return ToolResponse(
            success=True,
            data={
                "results": results,
                "count": len(results),
                "not_found": not_found,
            },
        )

    # Single mode: node_id parameter
    node_id = params.get("node_id", "")
    if not node_id:
        return ToolResponse(
            success=False,
            error="Either node_id or node_ids is required.",
        )

    result = _find_usages_for_node(node_id, nx_graph, ctx.graph_db, include_tests)
    if not result:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    return ToolResponse(success=True, data=result)
