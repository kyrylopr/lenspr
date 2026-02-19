"""Runtime tracing tool handlers: lens_trace, lens_trace_stats."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from lenspr import database
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext

__all__ = [
    "handle_trace",
    "handle_trace_stats",
]


def handle_trace(params: dict, ctx: LensContext) -> ToolResponse:
    """Run tests with runtime call tracing and merge edges into the graph.

    Executes pytest with the LensPR tracer plugin (sys.monitoring),
    collects caller→callee edges observed at runtime, and merges them
    into the static graph as EdgeSource.RUNTIME or BOTH.

    Requires Python 3.12+. Falls back gracefully on older versions.
    """
    from lenspr.tracer import is_tracing_available

    if not is_tracing_available():
        return ToolResponse(
            success=False,
            error="Runtime tracing requires Python 3.12+ (sys.monitoring).",
            hint="Upgrade to Python 3.12 or later to use this feature.",
        )

    ctx.ensure_synced()

    test_path = params.get("path", "")
    filter_k = params.get("filter_k", "")
    timeout = params.get("timeout", 120)

    project_root = str(ctx.project_root)
    trace_output = ctx.project_root / ".lens" / "trace_edges.json"

    # Build pytest command with tracer plugin
    cmd = [
        "python", "-m", "pytest",
        "-p", "lenspr.pytest_tracer",
        "--tb=short", "-q", "--no-header",
    ]

    if test_path:
        cmd.append(test_path)
    if filter_k:
        cmd.extend(["-k", filter_k])

    # Set project root for the tracer plugin
    env = dict(__import__("os").environ)
    env["LENSPR_PROJECT_ROOT"] = project_root

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=project_root, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return ToolResponse(
            success=False,
            error=f"Tracing timed out after {timeout}s.",
        )

    # Parse test results
    test_passed = result.returncode == 0

    # Read trace edges
    if not trace_output.exists():
        return ToolResponse(
            success=False,
            error="Tracer did not produce output. Check pytest ran correctly.",
            data={"pytest_output": result.stdout[-500:] if result.stdout else ""},
        )

    try:
        trace_data = json.loads(trace_output.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return ToolResponse(
            success=False,
            error=f"Failed to read trace output: {e}",
        )

    raw_edges = trace_data.get("edges", [])
    if not raw_edges:
        return ToolResponse(
            success=True,
            data={
                "tests_passed": test_passed,
                "runtime_edges": 0,
                "new_runtime": 0,
                "upgraded_to_both": 0,
                "message": "No runtime edges collected (tests may not exercise project code).",
            },
        )

    # Merge into graph.db
    edge_tuples = [
        (e["from"], e["to"], e.get("count", 1))
        for e in raw_edges
    ]
    merge_result = database.save_runtime_edges(edge_tuples, ctx.graph_db)

    # Force graph reload so subsequent tools see runtime edges
    ctx._graph = None

    return ToolResponse(
        success=True,
        data={
            "tests_passed": test_passed,
            "runtime_edges": len(raw_edges),
            "total_runtime_calls": trace_data.get("total_calls", 0),
            "new_runtime": merge_result["new_runtime"],
            "upgraded_to_both": merge_result["upgraded_to_both"],
            "merged_total": merge_result["total"],
        },
    )


def handle_trace_stats(params: dict, ctx: LensContext) -> ToolResponse:
    """Show runtime tracing statistics from the current graph.

    Reports how many edges are static-only, runtime-only, or confirmed
    by both static analysis and runtime observation.
    """
    ctx.ensure_synced()
    nx_graph = ctx.get_graph()

    static_only = 0
    runtime_only = 0
    both = 0
    total_runtime_calls = 0

    # Top nodes by runtime-discovered connections
    runtime_connections: dict[str, int] = {}

    for u, v, data in nx_graph.edges(data=True):
        source = data.get("source", "static")
        if source == "static":
            static_only += 1
        elif source == "runtime":
            runtime_only += 1
            runtime_connections[u] = runtime_connections.get(u, 0) + 1
            runtime_connections[v] = runtime_connections.get(v, 0) + 1
            meta = data.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            total_runtime_calls += meta.get("runtime_calls", 0)
        elif source == "both":
            both += 1
            meta = data.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            total_runtime_calls += meta.get("runtime_calls", 0)

    # Top 10 nodes with most runtime-discovered connections
    top_runtime_nodes = sorted(
        runtime_connections.items(), key=lambda x: x[1], reverse=True,
    )[:10]

    total_edges = static_only + runtime_only + both

    # Trace file freshness
    trace_file = ctx.project_root / ".lens" / "trace_edges.json"
    trace_age = None
    if trace_file.exists():
        age_s = time.time() - trace_file.stat().st_mtime
        if age_s < 60:
            trace_age = "< 1 minute ago"
        elif age_s < 3600:
            trace_age = f"{int(age_s / 60)} minutes ago"
        elif age_s < 86400:
            trace_age = f"{int(age_s / 3600)} hours ago"
        else:
            trace_age = f"{int(age_s / 86400)} days ago"

    return ToolResponse(
        success=True,
        data={
            "edge_sources": {
                "static_only": static_only,
                "runtime_only": runtime_only,
                "both": both,
                "total": total_edges,
            },
            "runtime_confirmation_rate": (
                round((both / (static_only + both)) * 100, 1)
                if (static_only + both) > 0 else 0
            ),
            "total_runtime_calls": total_runtime_calls,
            "top_runtime_nodes": [
                {"node_id": nid, "runtime_connections": count}
                for nid, count in top_runtime_nodes
            ],
            "trace_file_age": trace_age,
        },
    )
