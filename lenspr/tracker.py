"""Change history tracking for the code graph."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lenspr.models import Change


def record_change(
    node_id: str,
    action: str,
    old_source: Optional[str],
    new_source: Optional[str],
    old_hash: str,
    new_hash: str,
    affected_nodes: list[str],
    description: str,
    db_path: Path,
) -> int:
    """
    Record a change to history.db.

    Returns the change ID.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            """INSERT INTO changes
            (timestamp, node_id, action, old_source, new_source,
             old_hash, new_hash, affected_nodes, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp,
                node_id,
                action,
                old_source,
                new_source,
                old_hash,
                new_hash,
                json.dumps(affected_nodes),
                description,
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()


def get_history(
    db_path: Path,
    node_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 50,
) -> list[Change]:
    """
    Retrieve change history.

    Args:
        node_id: Filter by node ID.
        since: ISO 8601 timestamp — return changes after this time.
        limit: Maximum number of changes to return.
    """
    query = "SELECT * FROM changes WHERE 1=1"
    params: list = []

    if node_id:
        query += " AND node_id = ?"
        params.append(node_id)

    if since:
        query += " AND timestamp > ?"
        params.append(since)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, params).fetchall()
        return [
            Change(
                id=row["id"],
                timestamp=row["timestamp"],
                node_id=row["node_id"],
                action=row["action"],
                old_source=row["old_source"],
                new_source=row["new_source"],
                old_hash=row["old_hash"],
                new_hash=row["new_hash"],
                affected_nodes=json.loads(row["affected_nodes"]),
                description=row["description"],
            )
            for row in rows
        ]
    finally:
        conn.close()


def rollback(change_id: int, db_path: Path) -> Optional[str]:
    """
    Revert a specific change by restoring old_source.

    Returns the old_source if found, None otherwise.
    Note: This only returns the source — the caller is responsible
    for actually applying the rollback to the file and graph.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM changes WHERE id = ?", (change_id,)
        ).fetchone()

        if not row:
            return None

        return row["old_source"]
    finally:
        conn.close()
