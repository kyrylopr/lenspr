"""LensContext: central state manager for a LensPR session."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import networkx as nx

from lenspr import database
from lenspr import graph as graph_ops
from lenspr.models import SyncResult
from lenspr.parsers.python_parser import PythonParser
from lenspr.patcher import PatchBuffer


class LensContext:
    """
    Central access point for all LensPR operations on a project.

    Manages:
    - Database paths (graph.db, history.db, resolve_cache.db)
    - Lazy-loaded NetworkX graph (invalidated on mutation)
    - Patch buffer for batched file modifications
    - Parser instance
    """

    def __init__(self, project_root: Path, lens_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.lens_dir = lens_dir or (self.project_root / ".lens")
        self.graph_db = self.lens_dir / "graph.db"
        self.history_db = self.lens_dir / "history.db"
        self.resolve_cache_db = self.lens_dir / "resolve_cache.db"
        self.config_path = self.lens_dir / "config.json"
        self.patch_buffer = PatchBuffer()

        self._graph: nx.DiGraph | None = None
        self._parser = PythonParser()

    @property
    def is_initialized(self) -> bool:
        return self.lens_dir.exists() and self.graph_db.exists()

    def get_graph(self) -> nx.DiGraph:
        """Get NetworkX graph, building from SQLite if needed."""
        if self._graph is None:
            nodes, edges = database.load_graph(self.graph_db)
            self._graph = graph_ops.build_graph(nodes, edges)
        return self._graph

    def invalidate_graph(self) -> None:
        """Clear cached NetworkX graph. Called after any mutation."""
        self._graph = None

    def reparse_file(self, file_path: Path) -> None:
        """
        Reparse a single file and update the database.

        Removes old nodes/edges for this file, parses fresh, and saves.
        """
        rel_path = str(file_path.relative_to(self.project_root))

        # Load current graph data
        all_nodes, all_edges = database.load_graph(self.graph_db)

        # Remove nodes from this file
        old_node_ids = {n.id for n in all_nodes if n.file_path == rel_path}
        all_nodes = [n for n in all_nodes if n.file_path != rel_path]
        all_edges = [
            e for e in all_edges
            if e.from_node not in old_node_ids and e.to_node not in old_node_ids
        ]

        # Parse fresh
        if file_path.exists():
            new_nodes, new_edges = self._parser.parse_file(file_path, self.project_root)
            all_nodes.extend(new_nodes)
            all_edges.extend(new_edges)

        # Save
        database.save_graph(all_nodes, all_edges, self.graph_db)
        self.invalidate_graph()

    def full_sync(self) -> SyncResult:
        """
        Full reparse of the project + hash-based diff.

        Returns SyncResult describing what changed.
        """
        # Load old graph
        old_nodes, _ = database.load_graph(self.graph_db)
        old_index = {n.id: n for n in old_nodes}

        # Full reparse
        new_nodes, new_edges = self._parser.parse_project(self.project_root)
        new_index = {n.id: n for n in new_nodes}

        # Compute diff
        added = [n for nid, n in new_index.items() if nid not in old_index]
        deleted = [n for nid, n in old_index.items() if nid not in new_index]
        modified = [
            n for nid, n in new_index.items()
            if nid in old_index and n.hash != old_index[nid].hash
        ]

        # Save new graph
        database.save_graph(new_nodes, new_edges, self.graph_db)
        self.invalidate_graph()

        # Update config
        self._update_config()

        return SyncResult(added=added, modified=modified, deleted=deleted)

    def _update_config(self) -> None:
        """Update config.json with current state."""
        from datetime import datetime

        config = {}
        if self.config_path.exists():
            config = json.loads(self.config_path.read_text())

        config["last_sync"] = datetime.now(UTC).isoformat()

        self.config_path.write_text(json.dumps(config, indent=2))
