"""SQLite operations for persisting and querying the code graph."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from lenspr.models import Change, Edge, Node

# -- Schema definitions --

_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source_code TEXT NOT NULL,
    docstring TEXT,
    signature TEXT,
    hash TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    type TEXT NOT NULL,
    line_number INTEGER,
    confidence TEXT NOT NULL DEFAULT 'resolved',
    source TEXT NOT NULL DEFAULT 'static',
    untracked_reason TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
"""

_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    old_source TEXT,
    new_source TEXT,
    old_hash TEXT NOT NULL DEFAULT '',
    new_hash TEXT NOT NULL DEFAULT '',
    affected_nodes TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_changes_node ON changes(node_id);
CREATE INDEX IF NOT EXISTS idx_changes_time ON changes(timestamp);
"""

_RESOLVE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS resolutions (
    file_path TEXT NOT NULL,
    line INTEGER NOT NULL,
    column INTEGER NOT NULL,
    node_id TEXT,
    confidence TEXT NOT NULL,
    PRIMARY KEY (file_path, line, column)
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    """Create a connection with sensible defaults."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_database(lens_dir: Path) -> None:
    """
    Initialize .lens/ directory with empty databases.

    Creates graph.db, history.db, and resolve_cache.db with proper schemas.
    """
    lens_dir.mkdir(parents=True, exist_ok=True)

    with _connect(lens_dir / "graph.db") as conn:
        conn.executescript(_GRAPH_SCHEMA)

    with _connect(lens_dir / "history.db") as conn:
        conn.executescript(_HISTORY_SCHEMA)

    with _connect(lens_dir / "resolve_cache.db") as conn:
        conn.executescript(_RESOLVE_CACHE_SCHEMA)


def save_graph(nodes: list[Node], edges: list[Edge], db_path: Path) -> None:
    """
    Persist nodes and edges to SQLite.

    Clears existing data and writes fresh — used during full reparse.
    """
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM nodes")

        conn.executemany(
            """INSERT INTO nodes
            (id, type, name, qualified_name, file_path, start_line, end_line,
             source_code, docstring, signature, hash, metadata)
            VALUES (:id, :type, :name, :qualified_name, :file_path, :start_line,
                    :end_line, :source_code, :docstring, :signature, :hash, :metadata)""",
            [n.to_dict() for n in nodes],
        )

        conn.executemany(
            """INSERT INTO edges
            (id, from_node, to_node, type, line_number, confidence, source,
             untracked_reason, metadata)
            VALUES (:id, :from_node, :to_node, :type, :line_number, :confidence,
                    :source, :untracked_reason, :metadata)""",
            [e.to_dict() for e in edges],
        )


def load_graph(db_path: Path) -> tuple[list[Node], list[Edge]]:
    """Load full graph from database."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM nodes").fetchall()
        nodes = [Node.from_dict(dict(r)) for r in rows]

        rows = conn.execute("SELECT * FROM edges").fetchall()
        edges = [Edge.from_dict(dict(r)) for r in rows]

    return nodes, edges


def get_node(node_id: str, db_path: Path) -> Optional[Node]:
    """Retrieve a single node by ID."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row:
            return Node.from_dict(dict(row))
    return None


def get_nodes(
    db_path: Path,
    type_filter: Optional[str] = None,
    file_filter: Optional[str] = None,
    name_filter: Optional[str] = None,
) -> list[Node]:
    """List nodes with optional filters."""
    query = "SELECT * FROM nodes WHERE 1=1"
    params: list = []

    if type_filter:
        query += " AND type = ?"
        params.append(type_filter)
    if file_filter:
        query += " AND file_path = ?"
        params.append(file_filter)
    if name_filter:
        query += " AND name LIKE ?"
        params.append(f"%{name_filter}%")

    query += " ORDER BY file_path, start_line"

    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [Node.from_dict(dict(r)) for r in rows]


def get_edges(
    node_id: str, db_path: Path, direction: str = "both"
) -> list[Edge]:
    """
    Get edges connected to a node.

    Args:
        direction: "incoming" | "outgoing" | "both"
    """
    with _connect(db_path) as conn:
        if direction == "incoming":
            rows = conn.execute(
                "SELECT * FROM edges WHERE to_node = ?", (node_id,)
            ).fetchall()
        elif direction == "outgoing":
            rows = conn.execute(
                "SELECT * FROM edges WHERE from_node = ?", (node_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM edges WHERE from_node = ? OR to_node = ?",
                (node_id, node_id),
            ).fetchall()

        return [Edge.from_dict(dict(r)) for r in rows]


def update_node_source(node_id: str, new_source: str, new_hash: str, db_path: Path) -> bool:
    """Update a node's source code and hash in the database."""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE nodes SET source_code = ?, hash = ? WHERE id = ?",
            (new_source, new_hash, node_id),
        )
        return cursor.rowcount > 0


def delete_node(node_id: str, db_path: Path) -> bool:
    """Delete a node and its connected edges."""
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM edges WHERE from_node = ? OR to_node = ?",
            (node_id, node_id),
        )
        cursor = conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        return cursor.rowcount > 0


def search_nodes(query: str, db_path: Path, search_in: str = "all") -> list[Node]:
    """Search nodes by name, code, or docstring."""
    conditions = []
    params = []
    pattern = f"%{query}%"

    if search_in in ("name", "all"):
        conditions.append("name LIKE ?")
        params.append(pattern)
    if search_in in ("code", "all"):
        conditions.append("source_code LIKE ?")
        params.append(pattern)
    if search_in in ("docstring", "all"):
        conditions.append("docstring LIKE ?")
        params.append(pattern)

    where = " OR ".join(conditions)
    sql = f"SELECT * FROM nodes WHERE {where} ORDER BY file_path, start_line"

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [Node.from_dict(dict(r)) for r in rows]


# -- Resolution cache --

def cache_resolution(
    file_path: str, line: int, column: int, node_id: Optional[str],
    confidence: str, db_path: Path
) -> None:
    """Cache a name resolution result."""
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO resolutions
            (file_path, line, column, node_id, confidence)
            VALUES (?, ?, ?, ?, ?)""",
            (file_path, line, column, node_id, confidence),
        )


def get_cached_resolution(
    file_path: str, line: int, column: int, db_path: Path
) -> Optional[tuple[Optional[str], str]]:
    """Get a cached resolution result. Returns (node_id, confidence) or None."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT node_id, confidence FROM resolutions WHERE file_path = ? AND line = ? AND column = ?",
            (file_path, line, column),
        ).fetchone()
        if row:
            return row["node_id"], row["confidence"]
    return None
