"""Resolver-based tool handlers: API map, DB map, env map, FFI map, infra map."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lenspr import database
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext

__all__ = [
    "handle_api_map",
    "handle_db_map",
    "handle_env_map",
    "handle_ffi_map",
    "handle_infra_map",
]


def handle_api_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map API routes to frontend calls and create cross-language edges.

    Reads pre-computed calls_api edges from graph.db (created during init/sync
    by the ApiMapper pipeline). This is much more reliable than re-scanning
    source because the mapper has full router-prefix context during parse time.
    """
    import json

    ctx.ensure_synced()
    raw_edges = database.get_edges_by_types(["calls_api"], ctx.graph_db)

    # Group by backend handler (to_node) for a route-centric view
    route_map: dict[str, list[dict]] = {}
    matched_edges: list[dict] = []

    for edge in raw_edges:
        meta = {}
        if edge.get("metadata"):
            try:
                meta = json.loads(edge["metadata"]) if isinstance(edge["metadata"], str) else edge["metadata"]
            except (json.JSONDecodeError, TypeError):
                pass

        entry = {
            "from": edge["from_node"],
            "to": edge["to_node"],
            "http_method": meta.get("http_method", ""),
            "call_path": meta.get("path", ""),
            "route_path": meta.get("route_path", ""),
            "confidence": edge.get("confidence", ""),
        }
        matched_edges.append(entry)

        handler = edge["to_node"]
        route_map.setdefault(handler, []).append(edge["from_node"])

    return ToolResponse(
        success=True,
        data={
            "matched_edges": matched_edges,
            "route_map": {
                handler: {"handler": handler, "callers": callers}
                for handler, callers in route_map.items()
            },
            "summary": {
                "edges_total": len(matched_edges),
                "routes_with_callers": len(route_map),
            },
        },
    )


def handle_db_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map database tables to the functions that read/write them.

    Reads pre-computed reads_table / writes_table / migrates edges from
    graph.db (created during init/sync by the SqlMapper pipeline).
    """
    ctx.ensure_synced()
    raw_edges = database.get_edges_by_types(
        ["reads_table", "writes_table", "migrates"], ctx.graph_db,
    )

    # Group by table name (to_node is the table identifier)
    table_usage: dict[str, dict[str, list[str]]] = {}
    edge_list: list[dict] = []

    for edge in raw_edges:
        table = edge["to_node"]
        func = edge["from_node"]
        etype = edge["type"]

        if table not in table_usage:
            table_usage[table] = {"reads": [], "writes": []}

        if etype in ("writes_table", "migrates"):
            table_usage[table]["writes"].append(func)
        else:
            table_usage[table]["reads"].append(func)

        edge_list.append({
            "from": func,
            "to": table,
            "type": etype,
            "confidence": edge.get("confidence", ""),
        })

    return ToolResponse(
        success=True,
        data={
            "table_usage": table_usage,
            "edges": edge_list,
            "summary": {
                "tables_found": len(table_usage),
                "edges_total": len(edge_list),
                "reads": sum(len(v["reads"]) for v in table_usage.values()),
                "writes": sum(len(v["writes"]) for v in table_usage.values()),
            },
        },
    )


def handle_env_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map environment variables and infrastructure dependencies.

    Detects env var definitions (.env, docker-compose) and usages
    (os.environ, os.getenv, process.env) across the codebase.

    Args:
        mode: "summary" (default) for counts per var, "full" for complete usage details.
        env_var: Drill into a specific env var by name to see full used_by list.
    """
    ctx.ensure_synced()
    all_nodes = database.get_nodes(ctx.graph_db)
    project_root = ctx.project_root

    mode = params.get("mode", "summary")
    env_var_filter = params.get("env_var")

    from lenspr.resolvers.infra_mapper import EnvVarDef, InfraMapper

    mapper = InfraMapper()

    skip_dirs = {
        "__pycache__", ".git", ".lens", ".venv", "venv", "env",
        "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build",
    }

    def _skip(path: Path) -> bool:
        return any(part in skip_dirs for part in path.parts)

    # Find and parse .env files (recursive — monorepo support)
    for env_file in sorted(project_root.rglob(".env*")):
        if env_file.is_file() and not _skip(env_file.relative_to(project_root)):
            mapper.parse_env_file(env_file)

    # Find and parse docker-compose files (recursive)
    compose_patterns = ["docker-compose*.yml", "docker-compose*.yaml", "compose.yml", "compose.yaml"]
    seen_compose: set[Path] = set()
    for pattern in compose_patterns:
        for compose_file in sorted(project_root.rglob(pattern)):
            if compose_file.is_file() and compose_file not in seen_compose and not _skip(compose_file.relative_to(project_root)):
                seen_compose.add(compose_file)
                mapper.parse_compose(compose_file)

    # Extract env var definitions from compose environment: sections
    for svc in mapper._services.values():
        for key, val in svc.environment.items():
            mapper._env_vars.append(EnvVarDef(
                name=key,
                value=val,
                source_file=svc.file_path,
                line=0,
            ))

    # Also scan CI workflow files for env definitions
    for wf_dir in [project_root / ".github" / "workflows", project_root / ".circleci"]:
        if not wf_dir.is_dir():
            continue
        for wf_file in sorted(wf_dir.rglob("*.yml")) + sorted(wf_dir.rglob("*.yaml")):
            if not wf_file.is_file():
                continue
            try:
                text = wf_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            import re as _re
            for m in _re.finditer(r"^\s+(\w+):\s*(.+)$", text, _re.MULTILINE):
                name, val = m.group(1), m.group(2).strip()
                # Only capture ALL_CAPS names (env vars, not YAML keys)
                if name.isupper() and not val.endswith(":"):
                    mapper._env_vars.append(EnvVarDef(
                        name=name,
                        value=val.strip("'\""),
                        source_file=str(wf_file),
                        line=text[:m.start()].count("\n") + 1,
                    ))

    usages = mapper.extract_env_usages(all_nodes)
    edges = mapper.match()

    # Group by env var name
    definitions = mapper._env_vars
    env_summary: dict[str, dict] = {}
    for d in definitions:
        env_summary[d.name] = {
            "defined_in": d.source_file,
            "default": d.value,
            "used_by": [],
        }
    for u in usages:
        entry = env_summary.setdefault(u.name, {"defined_in": None, "default": None, "used_by": []})
        entry["used_by"].append({"node_id": u.caller_node_id, "file": u.file_path, "line": u.line})

    # Find undefined env vars (used but not defined)
    defined_names = {d.name for d in definitions}
    undefined = [name for name in env_summary if name not in defined_names and env_summary[name]["used_by"]]

    summary_counts = {
        "definitions_found": len(definitions),
        "usages_found": len(usages),
        "undefined_vars": len(undefined),
        "edges_created": len(edges),
    }

    # Drill-down: single env var with full details
    if env_var_filter:
        entry = env_summary.get(env_var_filter)
        if entry is None:
            return ToolResponse(
                success=False,
                error=f"Environment variable '{env_var_filter}' not found.",
                hint="Use lens_env_map() to see all variable names.",
            )
        return ToolResponse(
            success=True,
            data={
                "env_var": env_var_filter,
                "defined_in": entry["defined_in"],
                "default": entry["default"],
                "used_by": entry["used_by"],
                "usage_count": len(entry["used_by"]),
            },
        )

    if mode == "summary":
        # Compact: replace used_by lists with counts, omit edges
        compact_vars = {}
        for name, info in env_summary.items():
            compact_vars[name] = {
                "defined_in": info["defined_in"],
                "usage_count": len(info["used_by"]),
            }
        return ToolResponse(
            success=True,
            data={
                "env_vars": compact_vars,
                "undefined_vars": undefined,
                "summary": summary_counts,
                "hint": "Use mode='full' for complete usage details, or env_var='NAME' to drill into one.",
            },
        )

    # mode == "full" — original behavior
    return ToolResponse(
        success=True,
        data={
            "env_vars": env_summary,
            "undefined_vars": undefined,
            "edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    "type": e.type.value,
                }
                for e in edges
            ],
            "summary": summary_counts,
        },
    )


def handle_ffi_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map FFI bridges (NAPI, koffi, ffi-napi, WASM) between TS/JS and native code.

    Reads pre-computed calls_native edges from graph.db (created during init/sync
    by the FfiMapper pipeline).
    """
    import json

    ctx.ensure_synced()
    raw_edges = database.get_edges_by_types(["calls_native"], ctx.graph_db)

    # Group by bridge type
    bridge_map: dict[str, list[dict]] = {}
    edge_list: list[dict] = []

    for edge in raw_edges:
        meta = {}
        if edge.get("metadata"):
            try:
                meta = json.loads(edge["metadata"]) if isinstance(edge["metadata"], str) else edge["metadata"]
            except (json.JSONDecodeError, TypeError):
                pass

        entry = {
            "from": edge["from_node"],
            "to": edge["to_node"],
            "bridge_type": meta.get("bridge_type", "unknown"),
            "bound_functions": meta.get("bound_functions", []),
            "confidence": edge.get("confidence", ""),
        }
        edge_list.append(entry)

        bridge_type = meta.get("bridge_type", "unknown")
        bridge_map.setdefault(bridge_type, []).append(entry)

    return ToolResponse(
        success=True,
        data={
            "bridges": bridge_map,
            "edges": edge_list,
            "summary": {
                "edges_total": len(edge_list),
                "bridge_types": {bt: len(edges) for bt, edges in bridge_map.items()},
            },
        },
    )


def handle_infra_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map infrastructure: Dockerfiles, CI/CD workflows, compose services.

    Reads pre-computed infrastructure nodes and edges from graph.db
    (created during init/sync by InfraMapper and CiMapper).

    Args:
        mode: "summary" (default) for edge counts by type, "full" for complete edge list.
        focus: Filter to "ci", "docker", or "compose" subsystem with edges.
    """
    import json

    ctx.ensure_synced()

    mode = params.get("mode", "summary")
    focus = params.get("focus")

    # Collect infrastructure edges
    infra_edge_types = ["depends_on", "exposes_port", "uses_env"]
    raw_edges = database.get_edges_by_types(infra_edge_types, ctx.graph_db)

    # Get infrastructure nodes
    all_nodes = database.get_nodes(ctx.graph_db)
    infra_nodes = [
        n for n in all_nodes
        if n.id.startswith(("infra.", "ci."))
    ]

    # Group nodes by category
    dockerfiles: list[dict] = []
    ci_workflows: list[dict] = []
    compose_services: list[dict] = []
    edge_list: list[dict] = []

    for node in infra_nodes:
        entry = {
            "id": node.id,
            "name": node.name,
            "type": node.type.value,
            "file_path": node.file_path,
        }
        if node.id.startswith("infra.dockerfile."):
            dockerfiles.append(entry)
        elif node.id.startswith("ci.github."):
            ci_workflows.append(entry)
        elif node.id.startswith("infra.service."):
            compose_services.append(entry)

    for edge in raw_edges:
        meta = {}
        if edge.get("metadata"):
            try:
                meta = json.loads(edge["metadata"]) if isinstance(edge["metadata"], str) else edge["metadata"]
            except (json.JSONDecodeError, TypeError):
                pass

        edge_list.append({
            "from": edge["from_node"],
            "to": edge["to_node"],
            "type": edge["type"],
            "relation": meta.get("ci_relation", meta.get("relation", "")),
            "confidence": edge.get("confidence", ""),
        })

    summary_counts = {
        "dockerfiles": len(dockerfiles),
        "ci_workflows": len(ci_workflows),
        "compose_services": len(compose_services),
        "edges_total": len(edge_list),
    }

    # Focus filter: return specific subsystem with relevant edges
    if focus:
        focus_lower = focus.lower()
        if focus_lower == "ci":
            ci_ids = {w["id"] for w in ci_workflows}
            focused_edges = [e for e in edge_list if e["from"] in ci_ids or e["to"] in ci_ids]
            return ToolResponse(success=True, data={
                "focus": "ci", "ci_workflows": ci_workflows,
                "edges": focused_edges, "summary": summary_counts,
            })
        elif focus_lower == "docker":
            docker_ids = {d["id"] for d in dockerfiles}
            focused_edges = [e for e in edge_list if e["from"] in docker_ids or e["to"] in docker_ids]
            return ToolResponse(success=True, data={
                "focus": "docker", "dockerfiles": dockerfiles,
                "edges": focused_edges, "summary": summary_counts,
            })
        elif focus_lower == "compose":
            svc_ids = {s["id"] for s in compose_services}
            focused_edges = [e for e in edge_list if e["from"] in svc_ids or e["to"] in svc_ids]
            return ToolResponse(success=True, data={
                "focus": "compose", "compose_services": compose_services,
                "edges": focused_edges, "summary": summary_counts,
            })

    if mode == "summary":
        # Compact: edge counts by type instead of full list
        edge_type_counts: dict[str, int] = {}
        for e in edge_list:
            edge_type_counts[e["type"]] = edge_type_counts.get(e["type"], 0) + 1

        return ToolResponse(
            success=True,
            data={
                "dockerfiles": dockerfiles,
                "ci_workflows": ci_workflows,
                "compose_services": compose_services,
                "edge_summary": edge_type_counts,
                "summary": summary_counts,
                "hint": "Use mode='full' for complete edge list, or focus='ci'|'docker'|'compose' for subsystem detail.",
            },
        )

    # mode == "full" — original behavior
    return ToolResponse(
        success=True,
        data={
            "dockerfiles": dockerfiles,
            "ci_workflows": ci_workflows,
            "compose_services": compose_services,
            "edges": edge_list,
            "summary": summary_counts,
        },
    )
