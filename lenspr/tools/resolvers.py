"""Resolver-based tool handlers: API map, DB map, env map."""

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
]


def handle_api_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map API routes to frontend calls and create cross-language edges.

    Scans backend code for route decorators (@app.get, @app.route) and
    frontend code for fetch/axios calls, then matches them by path.
    """
    ctx.ensure_synced()
    all_nodes = database.get_nodes(ctx.graph_db)

    from lenspr.resolvers.api_mapper import ApiMapper

    mapper = ApiMapper()
    routes = mapper.extract_routes(all_nodes)
    calls = mapper.extract_api_calls(all_nodes)
    edges = mapper.match()

    return ToolResponse(
        success=True,
        data={
            "routes": [
                {
                    "method": r.method,
                    "path": r.path,
                    "handler_node_id": r.handler_node_id,
                    "file": r.file_path,
                    "line": r.line,
                }
                for r in routes
            ],
            "api_calls": [
                {
                    "method": c.method,
                    "path": c.path,
                    "caller_node_id": c.caller_node_id,
                    "file": c.file_path,
                    "line": c.line,
                }
                for c in calls
            ],
            "matched_edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    "http_method": e.metadata.get("http_method", ""),
                    "call_path": e.metadata.get("path", ""),
                    "route_path": e.metadata.get("route_path", ""),
                }
                for e in edges
            ],
            "summary": {
                "routes_found": len(routes),
                "api_calls_found": len(calls),
                "edges_matched": len(edges),
            },
        },
    )


def handle_db_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map database tables to the functions that read/write them.

    Detects tables from SQLAlchemy __tablename__, Django models, and
    CREATE TABLE statements. Maps SQL queries (SELECT, INSERT, UPDATE,
    DELETE) to the containing functions.
    """
    ctx.ensure_synced()
    all_nodes = database.get_nodes(ctx.graph_db)

    from lenspr.resolvers.sql_mapper import SqlMapper

    mapper = SqlMapper()
    tables = mapper.extract_tables(all_nodes)
    ops = mapper.extract_operations(all_nodes)
    edges = mapper.match()

    # Group operations by table
    table_usage: dict[str, dict[str, list[str]]] = {}
    for op in ops:
        t = op.table_name
        if t not in table_usage:
            table_usage[t] = {"reads": [], "writes": []}
        key = "writes" if op.operation.upper() in ("INSERT", "UPDATE", "DELETE", "CREATE") else "reads"
        table_usage[t][key].append(op.node_id)

    return ToolResponse(
        success=True,
        data={
            "tables": [
                {
                    "name": t.table_name,
                    "node_id": t.node_id,
                    "file": t.file_path,
                    "source": t.source,
                }
                for t in tables
            ],
            "operations": [
                {
                    "table": op.table_name,
                    "operation": op.operation,
                    "node_id": op.node_id,
                    "file": op.file_path,
                    "line": op.line,
                }
                for op in ops
            ],
            "table_usage": table_usage,
            "edges": [
                {
                    "from": e.from_node,
                    "to": e.to_node,
                    "type": e.type.value,
                }
                for e in edges
            ],
            "summary": {
                "tables_found": len(tables),
                "operations_found": len(ops),
                "edges_created": len(edges),
            },
        },
    )


def handle_env_map(params: dict, ctx: LensContext) -> ToolResponse:
    """Map environment variables and infrastructure dependencies.

    Detects env var definitions (.env, docker-compose) and usages
    (os.environ, os.getenv, process.env) across the codebase.
    """
    ctx.ensure_synced()
    all_nodes = database.get_nodes(ctx.graph_db)
    project_root = ctx.project_root

    from lenspr.resolvers.infra_mapper import InfraMapper

    mapper = InfraMapper(project_root)
    definitions = mapper.extract_env_definitions()
    usages = mapper.extract_env_usages(all_nodes)
    edges = mapper.match()

    # Group by env var name
    env_summary: dict[str, dict] = {}
    for d in definitions:
        env_summary[d.name] = {
            "defined_in": d.source,
            "default": d.default_value,
            "used_by": [],
        }
    for u in usages:
        entry = env_summary.setdefault(u.env_name, {"defined_in": None, "default": None, "used_by": []})
        entry["used_by"].append({"node_id": u.node_id, "file": u.file_path, "line": u.line})

    # Find undefined env vars (used but not defined)
    defined_names = {d.name for d in definitions}
    undefined = [name for name in env_summary if name not in defined_names and env_summary[name]["used_by"]]

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
            "summary": {
                "definitions_found": len(definitions),
                "usages_found": len(usages),
                "undefined_vars": len(undefined),
                "edges_created": len(edges),
            },
        },
    )
