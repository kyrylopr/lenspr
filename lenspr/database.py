"""SQLite operations for persisting and querying the code graph."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from lenspr.models import Edge, Node

logger = logging.getLogger(__name__)

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
    metadata TEXT NOT NULL DEFAULT '{}',
    -- Semantic annotation fields
    summary TEXT,
    role TEXT,
    side_effects TEXT,
    semantic_inputs TEXT,
    semantic_outputs TEXT,
    annotation_hash TEXT,
    -- Pre-computed metrics (stored as JSON)
    metrics TEXT
);

CREATE TABLE IF NOT EXISTS project_metrics (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    type TEXT NOT NULL,
    line_number INTEGER,
    column INTEGER,
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
    description TEXT NOT NULL DEFAULT '',
    reasoning TEXT NOT NULL DEFAULT ''
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

_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    """Create a connection with sensible defaults."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as e:
        raise sqlite3.OperationalError(
            f"Cannot open database at {db_path}: {e}"
        ) from e


def init_database(lens_dir: Path) -> None:
    """
    Initialize .lens/ directory with empty databases.

    Creates graph.db, history.db, resolve_cache.db, and session.db with proper schemas.
    """
    lens_dir.mkdir(parents=True, exist_ok=True)

    with _connect(lens_dir / "graph.db") as conn:
        conn.executescript(_GRAPH_SCHEMA)
        _migrate_annotations(conn)

    with _connect(lens_dir / "history.db") as conn:
        conn.executescript(_HISTORY_SCHEMA)
        _migrate_history(conn)

    with _connect(lens_dir / "resolve_cache.db") as conn:
        conn.executescript(_RESOLVE_CACHE_SCHEMA)

    with _connect(lens_dir / "session.db") as conn:
        conn.executescript(_SESSION_SCHEMA)


def _migrate_annotations(conn: sqlite3.Connection) -> None:
    """Add annotation columns to existing databases if missing."""
    cursor = conn.execute("PRAGMA table_info(nodes)")
    columns = {row["name"] for row in cursor.fetchall()}

    annotation_columns = [
        ("summary", "TEXT"),
        ("role", "TEXT"),
        ("side_effects", "TEXT"),
        ("semantic_inputs", "TEXT"),
        ("semantic_outputs", "TEXT"),
        ("annotation_hash", "TEXT"),
        ("metrics", "TEXT"),
    ]

    for col_name, col_type in annotation_columns:
        if col_name not in columns:
            conn.execute(f"ALTER TABLE nodes ADD COLUMN {col_name} {col_type}")

def _migrate_history(conn: sqlite3.Connection) -> None:
    """Add reasoning column to existing history.db if missing."""
    cursor = conn.execute("PRAGMA table_info(changes)")
    columns = {row["name"] for row in cursor.fetchall()}
    if "reasoning" not in columns:
        conn.execute("ALTER TABLE changes ADD COLUMN reasoning TEXT NOT NULL DEFAULT ''")



def save_graph(nodes: list[Node], edges: list[Edge], db_path: Path) -> None:
    """
    Persist nodes and edges to SQLite.

    Clears existing data and writes fresh — used during full reparse.
    """
    try:
        with _connect(db_path) as conn:
            # Preserve runtime-only edges across syncs.
            # Static/both edges are re-created from the fresh parse.
            # Runtime edges (source='runtime') are only produced by the tracer.
            conn.execute("DELETE FROM edges WHERE source != 'runtime'")
            conn.execute("DELETE FROM nodes")

            conn.executemany(
                """INSERT INTO nodes
                (id, type, name, qualified_name, file_path, start_line, end_line,
                 source_code, docstring, signature, hash, metadata,
                 summary, role, side_effects, semantic_inputs, semantic_outputs,
                 annotation_hash, metrics)
                VALUES (:id, :type, :name, :qualified_name, :file_path, :start_line,
                        :end_line, :source_code, :docstring, :signature, :hash, :metadata,
                        :summary, :role, :side_effects, :semantic_inputs, :semantic_outputs,
                        :annotation_hash, :metrics)""",
                [n.to_dict() for n in nodes],
            )

            conn.executemany(
                """INSERT INTO edges
                (id, from_node, to_node, type, line_number, column, confidence, source,
                 untracked_reason, metadata)
                VALUES (:id, :from_node, :to_node, :type, :line_number, :column,
                        :confidence, :source, :untracked_reason, :metadata)""",
                [e.to_dict() for e in edges],
            )
    except Exception as e:
        logger.error("save_graph failed for %s: %s", db_path, e)
        raise


def save_runtime_edges(
    edges: list[tuple[str, str, int]], db_path: Path,
) -> dict[str, int]:
    """Upsert runtime edges without wiping static edges.

    For each runtime edge:
    - If a matching static edge exists (same from_node, to_node) → upgrade source to 'both'
    - If no matching edge → insert with source='runtime', confidence='resolved'

    Args:
        edges: List of (from_node_id, to_node_id, call_count) tuples.
        db_path: Path to graph.db.

    Returns:
        Dict with counts: {"new_runtime": N, "upgraded_to_both": N, "total": N}
    """
    import json
    import uuid

    new_count = 0
    upgraded_count = 0

    with _connect(db_path) as conn:
        for from_node, to_node, call_count in edges:
            # Check if a static edge already exists
            existing = conn.execute(
                "SELECT id, source FROM edges WHERE from_node = ? AND to_node = ? LIMIT 1",
                (from_node, to_node),
            ).fetchone()

            if existing:
                eid, source = existing
                if source == "static":
                    # Upgrade static → both
                    conn.execute(
                        "UPDATE edges SET source = 'both', "
                        "metadata = json_set(COALESCE(metadata, '{}'), '$.runtime_calls', ?) "
                        "WHERE id = ?",
                        (call_count, eid),
                    )
                    upgraded_count += 1
                elif source == "runtime":
                    # Update call count
                    conn.execute(
                        "UPDATE edges SET "
                        "metadata = json_set(COALESCE(metadata, '{}'), '$.runtime_calls', ?) "
                        "WHERE id = ?",
                        (call_count, eid),
                    )
            else:
                # Insert new runtime-only edge
                edge_id = f"rt_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """INSERT INTO edges
                    (id, from_node, to_node, type, line_number, column,
                     confidence, source, untracked_reason, metadata)
                    VALUES (?, ?, ?, 'calls', NULL, NULL,
                            'resolved', 'runtime', '', ?)""",
                    (edge_id, from_node, to_node,
                     json.dumps({"runtime_calls": call_count})),
                )
                new_count += 1

    total = new_count + upgraded_count
    logger.info(
        "save_runtime_edges: %d new, %d upgraded to 'both' (%d total)",
        new_count, upgraded_count, total,
    )
    return {"new_runtime": new_count, "upgraded_to_both": upgraded_count, "total": total}


def get_all_node_ids(db_path: Path) -> set[str]:
    """Return all node IDs from the graph. Cheap — loads IDs only, not full nodes."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM nodes").fetchall()
        return {row[0] for row in rows}


def sync_file(
    file_path: str,
    new_nodes: list[Node],
    new_edges: list[Edge],
    db_path: Path,
) -> dict[str, int]:
    """Granular sync: update nodes/edges for a single file using node-hash diffing.

    Only outgoing edges from nodes whose hash changed are refreshed.
    Unchanged nodes keep all their edges (including runtime edges).
    Node annotations are preserved via ON CONFLICT DO UPDATE (annotation
    columns are excluded from the UPDATE SET).

    Args:
        file_path: Relative file path (e.g. "lenspr/context.py").
        new_nodes: Freshly parsed nodes for this file.
        new_edges: Freshly parsed edges for this file.
        db_path: Path to graph.db.

    Returns:
        Dict with counts: added, modified, deleted, unchanged, edges_refreshed.
    """
    new_index = {n.id: n for n in new_nodes}
    new_node_ids = set(new_index.keys())

    with _connect(db_path) as conn:
        # 1. Load old node IDs and hashes for this file
        old_rows = conn.execute(
            "SELECT id, hash FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchall()
        old_hashes = {row[0]: row[1] for row in old_rows}
        old_node_ids = set(old_hashes.keys())

        # 2. Classify nodes by hash diff
        added_ids = new_node_ids - old_node_ids
        deleted_ids = old_node_ids - new_node_ids
        modified_ids = {
            nid for nid in (old_node_ids & new_node_ids)
            if old_hashes[nid] != new_index[nid].hash
        }
        unchanged_ids = (old_node_ids & new_node_ids) - modified_ids
        changed_ids = added_ids | modified_ids  # need edge refresh

        # 3. Delete outgoing edges from changed/deleted nodes.
        #    Preserve mapper-produced cross-language edges (calls_api,
        #    reads_table, writes_table, etc.) — these are only recreated
        #    during full sync when the mappers run.
        _MAPPER_EDGE_TYPES = (
            "calls_api", "handles_route",
            "reads_table", "writes_table", "migrates",
            "depends_on", "exposes_port", "uses_env",
        )
        ids_to_clear = changed_ids | deleted_ids
        if ids_to_clear:
            placeholders = ",".join("?" * len(ids_to_clear))
            type_placeholders = ",".join("?" * len(_MAPPER_EDGE_TYPES))
            conn.execute(
                f"DELETE FROM edges WHERE from_node IN ({placeholders})"
                f" AND type NOT IN ({type_placeholders})",
                list(ids_to_clear) + list(_MAPPER_EDGE_TYPES),
            )

        # 4. Delete stale incoming edges to deleted nodes
        if deleted_ids:
            placeholders = ",".join("?" * len(deleted_ids))
            conn.execute(
                f"DELETE FROM edges WHERE to_node IN ({placeholders})",
                list(deleted_ids),
            )

        # 5. Delete removed nodes
        if deleted_ids:
            placeholders = ",".join("?" * len(deleted_ids))
            conn.execute(
                f"DELETE FROM nodes WHERE id IN ({placeholders})",
                list(deleted_ids),
            )

        # 6. Upsert nodes — INSERT new, UPDATE existing.
        #    ON CONFLICT preserves annotation columns (summary, role,
        #    side_effects, semantic_inputs, semantic_outputs, annotation_hash).
        if new_nodes:
            conn.executemany(
                """INSERT INTO nodes
                (id, type, name, qualified_name, file_path, start_line, end_line,
                 source_code, docstring, signature, hash, metadata,
                 summary, role, side_effects, semantic_inputs, semantic_outputs,
                 annotation_hash, metrics)
                VALUES (:id, :type, :name, :qualified_name, :file_path, :start_line,
                        :end_line, :source_code, :docstring, :signature, :hash, :metadata,
                        :summary, :role, :side_effects, :semantic_inputs, :semantic_outputs,
                        :annotation_hash, :metrics)
                ON CONFLICT(id) DO UPDATE SET
                    type = excluded.type,
                    name = excluded.name,
                    qualified_name = excluded.qualified_name,
                    file_path = excluded.file_path,
                    start_line = excluded.start_line,
                    end_line = excluded.end_line,
                    source_code = excluded.source_code,
                    docstring = excluded.docstring,
                    signature = excluded.signature,
                    hash = excluded.hash,
                    metadata = excluded.metadata,
                    metrics = excluded.metrics""",
                [n.to_dict() for n in new_nodes],
            )

        # 7. Insert edges for changed/added nodes only
        changed_edges = [e for e in new_edges if e.from_node in changed_ids]
        if changed_edges:
            conn.executemany(
                """INSERT INTO edges
                (id, from_node, to_node, type, line_number, column, confidence, source,
                 untracked_reason, metadata)
                VALUES (:id, :from_node, :to_node, :type, :line_number, :column,
                        :confidence, :source, :untracked_reason, :metadata)""",
                [e.to_dict() for e in changed_edges],
            )

    return {
        "added": len(added_ids),
        "modified": len(modified_ids),
        "deleted": len(deleted_ids),
        "unchanged": len(unchanged_ids),
        "edges_refreshed": len(changed_edges),
    }


def load_graph(db_path: Path) -> tuple[list[Node], list[Edge]]:
    """Load full graph from database."""
    with _connect(db_path) as conn:
        # Ensure annotation columns exist (migration for existing DBs)
        _migrate_annotations(conn)

        rows = conn.execute("SELECT * FROM nodes").fetchall()
        nodes = [Node.from_dict(dict(r)) for r in rows]

        rows = conn.execute("SELECT * FROM edges").fetchall()
        edges = [Edge.from_dict(dict(r)) for r in rows]

    return nodes, edges


def get_node(node_id: str, db_path: Path) -> Node | None:
    """Retrieve a single node by ID."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row:
            return Node.from_dict(dict(row))
    return None


def get_nodes(
    db_path: Path,
    type_filter: str | None = None,
    file_filter: str | None = None,
    name_filter: str | None = None,
) -> list[Node]:
    """List nodes with optional filters."""
    query = "SELECT * FROM nodes WHERE 1=1"
    params: list = []

    if type_filter:
        query += " AND type = ?"
        params.append(type_filter)
    if file_filter:
        query += " AND file_path LIKE ?"
        params.append(f"{file_filter}%")
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


def save_annotation(
    node_id: str,
    db_path: Path,
    summary: str | None = None,
    role: str | None = None,
    side_effects: list[str] | None = None,
    semantic_inputs: list[str] | None = None,
    semantic_outputs: list[str] | None = None,
) -> bool:
    """Save semantic annotations for a node. Also stores current hash as annotation_hash."""
    import json

    with _connect(db_path) as conn:
        # Get current node hash
        row = conn.execute("SELECT hash FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return False

        current_hash = row["hash"]

        # Update annotations
        cursor = conn.execute(
            """UPDATE nodes SET
                summary = ?,
                role = ?,
                side_effects = ?,
                semantic_inputs = ?,
                semantic_outputs = ?,
                annotation_hash = ?
            WHERE id = ?""",
            (
                summary,
                role,
                json.dumps(side_effects) if side_effects else None,
                json.dumps(semantic_inputs) if semantic_inputs else None,
                json.dumps(semantic_outputs) if semantic_outputs else None,
                current_hash,
                node_id,
            ),
        )
        return cursor.rowcount > 0


def get_annotation_stats(db_path: Path) -> dict:
    """Get annotation coverage statistics for the codebase."""
    with _connect(db_path) as conn:
        # Total annotatable nodes (functions, methods, classes)
        total = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE type IN ('function', 'method', 'class')"
        ).fetchone()[0]

        # Annotated nodes
        annotated = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE summary IS NOT NULL OR role IS NOT NULL"
        ).fetchone()[0]

        # Stale annotations (hash != annotation_hash)
        stale = conn.execute(
            """SELECT COUNT(*) FROM nodes
               WHERE annotation_hash IS NOT NULL AND hash != annotation_hash"""
        ).fetchone()[0]

        # By type
        by_type = {}
        for node_type in ("function", "method", "class"):
            type_total = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE type = ?", (node_type,)
            ).fetchone()[0]
            type_annotated = conn.execute(
                """SELECT COUNT(*) FROM nodes
                   WHERE type = ? AND (summary IS NOT NULL OR role IS NOT NULL)""",
                (node_type,),
            ).fetchone()[0]
            by_type[node_type] = {"total": type_total, "annotated": type_annotated}

        # By role
        by_role: dict[str, int] = {}
        rows = conn.execute(
            "SELECT role, COUNT(*) FROM nodes WHERE role IS NOT NULL GROUP BY role"
        ).fetchall()
        for row in rows:
            by_role[row[0]] = row[1]

        return {
            "total_annotatable": total,
            "annotated": annotated,
            "coverage_pct": round((annotated / total * 100) if total > 0 else 0, 1),
            "stale_annotations": stale,
            "by_type": by_type,
            "by_role": by_role,
        }


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





# -- Project metrics --

def save_project_metrics(metrics: dict, db_path: Path) -> None:
    """Save project-wide metrics to the database."""
    import json

    with _connect(db_path) as conn:
        # Ensure table exists (for migrations)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_metrics (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("DELETE FROM project_metrics")
        for key, value in metrics.items():
            conn.execute(
                "INSERT INTO project_metrics (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


def get_project_metrics(db_path: Path) -> dict:
    """Load project-wide metrics from the database."""
    import json

    with _connect(db_path) as conn:
        # Ensure table exists (for migrations)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_metrics (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        rows = conn.execute("SELECT key, value FROM project_metrics").fetchall()
        result = {}
        for row in rows:
            val = row["value"]
            # Handle both JSON strings and raw values
            if isinstance(val, str):
                try:
                    result[row["key"]] = json.loads(val)
                except json.JSONDecodeError:
                    result[row["key"]] = val
            else:
                result[row["key"]] = val
        return result



def write_session_note(key: str, value: str, session_db: Path) -> None:
    """Write or overwrite a session note by key."""
    from datetime import datetime, timezone

    updated_at = datetime.now(timezone.utc).isoformat()
    with _connect(session_db) as conn:
        conn.execute(_SESSION_SCHEMA)  # ensure table exists
        conn.execute(
            """INSERT INTO notes (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, updated_at),
        )


def read_session_notes(session_db: Path) -> list[dict]:
    """Read all session notes, sorted by updated_at descending."""
    if not session_db.exists():
        return []
    with _connect(session_db) as conn:
        conn.execute(_SESSION_SCHEMA)  # ensure table exists
        rows = conn.execute(
            "SELECT key, value, updated_at FROM notes ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]





