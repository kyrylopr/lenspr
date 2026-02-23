"""Temporal analysis tool handlers: hotspots, node timeline."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import UTC
from typing import TYPE_CHECKING

from lenspr import database
from lenspr.models import ToolResponse
from lenspr.tools.helpers import resolve_or_fail

if TYPE_CHECKING:
    from lenspr.context import LensContext

__all__ = [
    "handle_hotspots",
    "handle_node_timeline",
]


def _parse_since(since: str | None) -> str | None:
    """Convert human-friendly 'since' values to ISO 8601.

    Accepts:
      - None → None (no filter)
      - "30d" / "7d" / "90d" → ISO timestamp N days ago
      - "2026-01-15" → passed through as-is
      - ISO 8601 string → passed through as-is
    """
    if not since:
        return None
    since = since.strip()
    if since.endswith("d") and since[:-1].isdigit():
        from datetime import datetime, timedelta

        days = int(since[:-1])
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return cutoff.isoformat()
    return since


def handle_hotspots(params: dict, ctx: LensContext) -> ToolResponse:
    """Find code hotspots — functions that change most frequently.

    Primary data source: LensPR's history.db (changes table).
    Git-independent — works even in projects without git.

    Optionally enriched with git data when available.
    """
    ctx.ensure_synced()

    limit = params.get("limit", 20)
    since = _parse_since(params.get("since"))
    file_filter = params.get("file_path")

    # Query changes from history.db
    conn = sqlite3.connect(str(ctx.history_db))
    conn.row_factory = sqlite3.Row
    try:
        # Ensure columns added in later versions exist (Phase 7 migration)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(changes)")}
        if "file_path" not in cols:
            conn.execute("ALTER TABLE changes ADD COLUMN file_path TEXT NOT NULL DEFAULT ''")
            conn.commit()
        if "reasoning" not in cols:
            conn.execute("ALTER TABLE changes ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
            conn.commit()

        query = "SELECT node_id, action, timestamp, file_path, reasoning FROM changes WHERE 1=1"
        qparams: list = []

        if since:
            query += " AND timestamp > ?"
            qparams.append(since)

        if file_filter:
            query += " AND file_path LIKE ?"
            qparams.append(f"%{file_filter}%")

        query += " ORDER BY timestamp DESC"
        rows = conn.execute(query, qparams).fetchall()
    finally:
        conn.close()

    if not rows:
        # Fallback: try git if no history.db data
        return _hotspots_from_git(ctx, limit, since, file_filter)

    # Count changes per node_id
    change_counts: Counter[str] = Counter()
    last_changed: dict[str, str] = {}
    node_files: dict[str, str] = {}
    actions_per_node: dict[str, Counter[str]] = {}

    for row in rows:
        nid = row["node_id"]
        change_counts[nid] += 1
        if nid not in last_changed:
            last_changed[nid] = row["timestamp"]
        fp = row["file_path"] if "file_path" in row.keys() else ""
        if fp:
            node_files[nid] = fp
        action = row["action"]
        if nid not in actions_per_node:
            actions_per_node[nid] = Counter()
        actions_per_node[nid][action] += 1

    # Build hotspot list
    nx_graph = ctx.get_graph()
    hotspots = []
    for nid, count in change_counts.most_common(limit):
        node_data = nx_graph.nodes.get(nid, {})
        name = node_data.get("name", nid.rsplit(".", 1)[-1] if "." in nid else nid)
        file_path = node_files.get(nid, node_data.get("file_path", ""))

        # Check if node has test coverage (any test predecessor)
        has_tests = False
        if nx_graph.has_node(nid):
            for pred in nx_graph.predecessors(nid):
                pred_data = nx_graph.nodes.get(pred, {})
                pred_file = pred_data.get("file_path", "")
                if "test" in pred_file:
                    has_tests = True
                    break

        risk_score = round(count * (1.0 if not has_tests else 0.3), 1)

        hotspots.append({
            "node_id": nid,
            "name": name,
            "file_path": file_path,
            "change_count": count,
            "last_changed": last_changed.get(nid, ""),
            "actions": dict(actions_per_node.get(nid, {})),
            "has_tests": has_tests,
            "risk_score": risk_score,
        })

    return ToolResponse(
        success=True,
        data={
            "hotspots": hotspots,
            "total_changes": len(rows),
            "unique_nodes": len(change_counts),
            "source": "history.db",
        },
    )


def _hotspots_from_git(
    ctx: LensContext, limit: int, since: str | None, file_filter: str | None,
) -> ToolResponse:
    """Fallback: compute hotspots from git log when history.db is empty."""
    import subprocess

    project_root = str(ctx.project_root)
    cmd = ["git", "log", "--format=%H", "--name-only"]
    if since:
        cmd.extend(["--since", since])
    if file_filter:
        cmd.extend(["--", file_filter])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=project_root, timeout=30,
        )
        if result.returncode != 0:
            return ToolResponse(
                success=True,
                data={"hotspots": [], "total_changes": 0, "source": "none",
                       "message": "No history.db data and git not available."},
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ToolResponse(
            success=True,
            data={"hotspots": [], "total_changes": 0, "source": "none",
                   "message": "No history.db data and git not available."},
        )

    # Parse git log output: commits separated by blank lines, files listed after hash
    file_counts: Counter[str] = Counter()
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line or len(line) == 40:  # skip blank lines and commit hashes
            continue
        file_counts[line] += 1

    # Map files to nodes
    nx_graph = ctx.get_graph()
    node_counts: Counter[str] = Counter()
    for node_id, data in nx_graph.nodes(data=True):
        fp = data.get("file_path", "")
        if fp in file_counts:
            node_counts[node_id] += file_counts[fp]

    hotspots = []
    for nid, count in node_counts.most_common(limit):
        data = nx_graph.nodes.get(nid, {})
        hotspots.append({
            "node_id": nid,
            "name": data.get("name", nid),
            "file_path": data.get("file_path", ""),
            "change_count": count,
            "last_changed": "",
            "actions": {},
            "has_tests": False,
            "risk_score": float(count),
        })

    return ToolResponse(
        success=True,
        data={
            "hotspots": hotspots,
            "total_changes": sum(file_counts.values()),
            "unique_nodes": len(node_counts),
            "source": "git",
        },
    )


def handle_node_timeline(params: dict, ctx: LensContext) -> ToolResponse:
    """Show unified timeline of changes for a specific node.

    Merges two data sources:
    1. LensPR history.db (with reasoning and impact data)
    2. Git commit history (with author and message)

    Returns events sorted newest-first.
    """
    ctx.ensure_synced()

    node_id, err = resolve_or_fail(params["node_id"], ctx)
    if err:
        return err
    limit = params.get("limit", 20)

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    events: list[dict] = []

    # Source 1: LensPR history.db
    from lenspr.tracker import get_history

    changes = get_history(ctx.history_db, node_id=node_id, limit=limit)
    for change in changes:
        events.append({
            "type": "lenspr_change",
            "date": change.timestamp,
            "action": change.action,
            "description": change.description,
            "reasoning": change.reasoning,
            "affected_count": len(change.affected_nodes),
        })

    # Source 2: Git history (optional — may not be available)
    git_events = _git_node_history(ctx, node)
    events.extend(git_events)

    # Sort by date descending, deduplicate close timestamps
    events.sort(key=lambda e: e.get("date", ""), reverse=True)

    # Trim to limit
    events = events[:limit]

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "name": node.name,
            "file_path": node.file_path,
            "events": events,
            "total_events": len(events),
            "sources": {
                "lenspr_changes": len(changes),
                "git_commits": len(git_events),
            },
        },
    )


def _git_node_history(ctx: LensContext, node) -> list[dict]:
    """Get git commit history for a node's line range."""
    import subprocess

    cmd = [
        "git", "log",
        f"-L{node.start_line},{node.end_line}:{node.file_path}",
        "-10",
        "--format=%H|%an|%at|%s",
        "--no-patch",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(ctx.project_root), timeout=15,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    events = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        from datetime import datetime

        try:
            ts = datetime.fromtimestamp(int(parts[2]), tz=UTC)
        except (ValueError, OSError):
            continue

        events.append({
            "type": "git_commit",
            "date": ts.isoformat(),
            "author": parts[1],
            "commit": parts[0][:8],
            "message": parts[3],
        })

    return events
