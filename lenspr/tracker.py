"""Change history tracking for the code graph."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from lenspr.models import Change


def record_change(
    node_id: str,
    action: str,
    old_source: str | None,
    new_source: str | None,
    old_hash: str,
    new_hash: str,
    affected_nodes: list[str],
    description: str,
    db_path: Path,
    reasoning: str = "",
) -> int:
    """
    Record a change to history.db.

    Args:
        reasoning: Why this change was made (optional but recommended).

    Returns the change ID.
    """
    timestamp = datetime.now(UTC).isoformat()

    conn = sqlite3.connect(str(db_path))
    try:
        # Auto-migrate: ensure reasoning column exists (added in a later version)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(changes)")}
        if "reasoning" not in cols:
            conn.execute("ALTER TABLE changes ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")
            conn.commit()

        cursor = conn.execute(
            """INSERT INTO changes
            (timestamp, node_id, action, old_source, new_source,
             old_hash, new_hash, affected_nodes, description, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                reasoning,
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()


def get_history(
    db_path: Path,
    node_id: str | None = None,
    since: str | None = None,
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
                reasoning=row["reasoning"] if "reasoning" in row.keys() else "",
            )
            for row in rows
        ]
    finally:
        conn.close()


