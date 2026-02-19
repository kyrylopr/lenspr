"""LensContext: central state manager for a LensPR session."""

from __future__ import annotations

import fcntl
import json
import logging
import threading
from datetime import UTC
from pathlib import Path

import networkx as nx

from lenspr import database
from lenspr import graph as graph_ops
from lenspr.architecture import compute_all_metrics
from lenspr.models import Node, SyncResult
from lenspr.parsers.base import ProgressCallback
from lenspr.parsers.multi import MultiParser, normalize_edge_targets
from lenspr.patcher import PatchBuffer
from lenspr.stats import ParseStats

logger = logging.getLogger(__name__)


class LensContext:
    """
    Central access point for all LensPR operations on a project.

    Manages:
    - Database paths (graph.db, history.db, resolve_cache.db)
    - Lazy-loaded NetworkX graph (invalidated on mutation)
    - Patch buffer for batched file modifications
    - Parser instance
    """

    # Increment when parser changes affect edge generation (e.g. jedi resolver fixes).
    # On mismatch with config.json, ensure_synced() auto-triggers a full resync.
    PARSER_VERSION = "2"

    def __init__(self, project_root: Path, lens_dir: Path | None = None) -> None:
        self.project_root = project_root.resolve()
        self.lens_dir = lens_dir or (self.project_root / ".lens")
        self.graph_db = self.lens_dir / "graph.db"
        self.history_db = self.lens_dir / "history.db"
        self.session_db = self.lens_dir / "session.db"
        self.resolve_cache_db = self.lens_dir / "resolve_cache.db"
        self.config_path = self.lens_dir / "config.json"
        self.patch_buffer = PatchBuffer()

        self._graph: nx.DiGraph | None = None
        self._parser = MultiParser()
        self._lock = threading.Lock()
        self._lock_path = self.lens_dir / ".lock"

        # Detect parser version mismatch — triggers full resync in ensure_synced()
        self._needs_full_sync = self._is_parser_version_stale()

    def _is_parser_version_stale(self) -> bool:
        """Return True if config.json has a different parser_version than current."""
        if not self.config_path.exists():
            return False  # Fresh install — no graph yet, nothing to resync
        try:
            config = json.loads(self.config_path.read_text())
            stored = config.get("parser_version", "0")
            if stored != LensContext.PARSER_VERSION:
                logger.warning(
                    "Parser version mismatch: stored=%s current=%s — "
                    "graph will be rebuilt on next tool call",
                    stored, LensContext.PARSER_VERSION,
                )
                return True
        except (json.JSONDecodeError, OSError):
            pass
        return False

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

    def has_pending_changes(self) -> bool:
        """Check if any files have changed since last sync."""
        old_fingerprints = self._load_fingerprints()
        if not old_fingerprints:
            return False  # No fingerprints = no way to compare

        extensions = set(self._parser.get_file_extensions())
        skip_dirs = {
            "__pycache__", ".git", ".lens", ".venv", "venv", "env",
            "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        }

        for file_path in self.project_root.rglob("*"):
            if not file_path.is_file():
                continue
            if any(part in skip_dirs for part in file_path.parts):
                continue
            if file_path.suffix not in extensions:
                continue

            rel = str(file_path.relative_to(self.project_root))
            stat = file_path.stat()

            if rel not in old_fingerprints:
                return True  # New file
            old = old_fingerprints[rel]
            if stat.st_mtime != old.get("mtime") or stat.st_size != old.get("size"):
                return True  # Changed file

        return False

    def ensure_synced(self) -> None:
        """
        Ensure graph is in sync with files. Call before read operations.

        Raises:
            RuntimeError: If sync fails (parser error, etc.)
        """
        if self._needs_full_sync:
            logger.warning(
                "Parser version changed — forcing full resync to rebuild graph"
            )
            try:
                self._full_sync_locked()
                self._needs_full_sync = False
            except Exception as e:
                raise RuntimeError(
                    f"Graph sync failed during parser upgrade migration: {e}"
                ) from e
            return

        if not self.has_pending_changes():
            return

        try:
            result = self.incremental_sync()
            total = len(result.added) + len(result.modified) + len(result.deleted)
            if total > 0:
                self.invalidate_graph()
                logger.info(
                    "Auto-synced before read: +%d ~%d -%d",
                    len(result.added), len(result.modified), len(result.deleted)
                )
        except Exception as e:
            raise RuntimeError(
                f"Graph sync failed. Cannot proceed with stale data: {e}"
            ) from e

    def reparse_file(self, file_path: Path) -> None:
        """
        Reparse a single file and update the database.

        Removes old nodes/edges for this file, parses fresh, and saves.
        Thread-safe and process-safe via locking.
        """
        with self._lock:
            self._reparse_file_locked(file_path)

    def _reparse_file_locked(self, file_path: Path) -> None:
        rel_path = str(file_path.relative_to(self.project_root))

        with open(self._lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                # Load current graph data
                all_nodes, all_edges = database.load_graph(self.graph_db)

                # Remove nodes from this file
                old_node_ids = {n.id for n in all_nodes if n.file_path == rel_path}
                all_nodes = [n for n in all_nodes if n.file_path != rel_path]

                # Remove only OUTGOING edges from this file's nodes.
                # Keep incoming edges (from other files) — they'll be
                # cleaned up below if the target node was deleted.
                all_edges = [
                    e for e in all_edges
                    if e.from_node not in old_node_ids
                ]

                # Parse fresh
                new_node_ids: set[str] = set()
                if file_path.exists():
                    new_nodes, new_edges = self._parser.parse_file(
                        file_path, self.project_root
                    )
                    new_node_ids = {n.id for n in new_nodes}
                    all_nodes.extend(new_nodes)
                    all_edges.extend(new_edges)

                # Remove stale incoming edges to nodes that were deleted
                # (existed before but not after reparse)
                deleted_node_ids = old_node_ids - new_node_ids
                if deleted_node_ids:
                    all_edges = [
                        e for e in all_edges
                        if e.to_node not in deleted_node_ids
                    ]

                # Normalize edge targets (fixes root != package root mismatch)
                normalize_edge_targets(all_nodes, all_edges)

                # Recompute metrics (class method counts, etc.)
                node_metrics, project_metrics = compute_all_metrics(
                    all_nodes, all_edges
                )
                for node in all_nodes:
                    if node.id in node_metrics:
                        node.metrics = node_metrics[node.id]

                # Save
                database.save_graph(all_nodes, all_edges, self.graph_db)
                database.save_project_metrics(project_metrics, self.graph_db)
                self.invalidate_graph()
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def full_sync(
        self,
        progress_callback: ProgressCallback | None = None,
        collect_stats: bool = False,
    ) -> tuple[SyncResult, ParseStats | None]:
        """
        Full reparse of the project + hash-based diff.

        Args:
            progress_callback: Optional callback(current, total, file_path) for progress.
            collect_stats: If True, collect and return detailed parsing statistics.

        Returns:
            Tuple of (SyncResult, ParseStats | None).
        Thread-safe and process-safe via locking.
        """
        with self._lock:
            return self._full_sync_locked(progress_callback, collect_stats)

    def _full_sync_locked(
        self,
        progress_callback: ProgressCallback | None = None,
        collect_stats: bool = False,
    ) -> tuple[SyncResult, ParseStats | None]:
        with open(self._lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                # Load old graph
                old_nodes, _ = database.load_graph(self.graph_db)
                old_index = {n.id: n for n in old_nodes}

                # Full reparse
                new_nodes, new_edges, stats = self._parser.parse_project(
                    self.project_root, progress_callback, collect_stats
                )

                # Deduplicate nodes by ID (keep last occurrence)
                # This handles edge cases like symlinks or files generating same module ID
                seen_ids: set[str] = set()
                unique_nodes: list[Node] = []
                for node in reversed(new_nodes):
                    if node.id not in seen_ids:
                        seen_ids.add(node.id)
                        unique_nodes.append(node)
                unique_nodes.reverse()

                if len(unique_nodes) != len(new_nodes):
                    logger.warning(
                        "Deduplicated %d duplicate node IDs",
                        len(new_nodes) - len(unique_nodes)
                    )

                new_index = {n.id: n for n in unique_nodes}

                # Compute metrics for all nodes (stored in graph)
                node_metrics, project_metrics = compute_all_metrics(
                    unique_nodes, new_edges
                )

                # Assign metrics to each node
                for node in unique_nodes:
                    if node.id in node_metrics:
                        node.metrics = node_metrics[node.id]

                # Compute diff
                added = [n for nid, n in new_index.items() if nid not in old_index]
                deleted = [n for nid, n in old_index.items() if nid not in new_index]
                modified = [
                    n for nid, n in new_index.items()
                    if nid in old_index and n.hash != old_index[nid].hash
                ]

                # Save new graph
                database.save_graph(unique_nodes, new_edges, self.graph_db)

                # Save project metrics
                database.save_project_metrics(project_metrics, self.graph_db)
                self.invalidate_graph()

                # Update config
                self._update_config()

                return SyncResult(added=added, modified=modified, deleted=deleted), stats
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def incremental_sync(self) -> SyncResult:
        """
        Sync only files that changed since last sync (by mtime + size).

        Falls back to full_sync if no previous fingerprints exist.
        """
        with self._lock:
            return self._incremental_sync_locked()

    def _incremental_sync_locked(self) -> SyncResult:
        # Load previous fingerprints
        old_fingerprints = self._load_fingerprints()
        if not old_fingerprints:
            logger.info("No previous fingerprints, falling back to full sync")
            return self._full_sync_locked()

        # Scan current files
        extensions = set(self._parser.get_file_extensions())
        current_files: dict[str, dict[str, float | int]] = {}
        skip_dirs = {
            "__pycache__", ".git", ".lens", ".venv", "venv", "env",
            "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
            "dist", "build", ".eggs", ".tox", "site-packages", "lib",
        }
        venv_suffixes = ("-env", "-venv", "_env", "_venv")

        def should_skip(path: Path) -> bool:
            for part in path.parts:
                if part in skip_dirs:
                    return True
                if any(part.endswith(s) for s in venv_suffixes):
                    return True
            return False

        for file_path in sorted(self.project_root.rglob("*")):
            if not file_path.is_file():
                continue
            if should_skip(file_path):
                continue
            if file_path.suffix not in extensions:
                continue
            rel = str(file_path.relative_to(self.project_root))
            stat = file_path.stat()
            current_files[rel] = {"mtime": stat.st_mtime, "size": stat.st_size}

        # Find changed, added, deleted files
        changed_files: list[str] = []
        added_files: list[str] = []
        deleted_files: list[str] = []

        for rel, fp in current_files.items():
            if rel not in old_fingerprints:
                added_files.append(rel)
            elif (
                fp["mtime"] != old_fingerprints[rel].get("mtime")
                or fp["size"] != old_fingerprints[rel].get("size")
            ):
                changed_files.append(rel)

        for rel in old_fingerprints:
            if rel not in current_files:
                deleted_files.append(rel)

        files_to_reparse = changed_files + added_files
        if not files_to_reparse and not deleted_files:
            logger.info("No files changed, nothing to sync")
            return SyncResult(added=[], modified=[], deleted=[])

        logger.info(
            "Incremental sync: %d changed, %d added, %d deleted",
            len(changed_files), len(added_files), len(deleted_files),
        )

        with open(self._lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                all_nodes, all_edges = database.load_graph(self.graph_db)
                old_index = {n.id: n for n in all_nodes}

                # Remove nodes from changed/deleted files
                files_to_remove = set(changed_files + added_files + deleted_files)
                removed_node_ids = {
                    n.id for n in all_nodes if n.file_path in files_to_remove
                }
                all_nodes = [n for n in all_nodes if n.file_path not in files_to_remove]

                # Remove only OUTGOING edges from removed nodes.
                # Keep incoming edges (from other files) — they'll be
                # cleaned up below if the target node was deleted.
                all_edges = [
                    e for e in all_edges
                    if e.from_node not in removed_node_ids
                ]

                # Refresh jedi project to pick up new/changed files
                # This ensures full name resolution even for incremental syncs
                if hasattr(self._parser, "set_project_root"):
                    self._parser.set_project_root(self.project_root)

                # Reparse changed/added files
                new_node_ids: set[str] = set()
                for rel in files_to_reparse:
                    file_path = self.project_root / rel
                    if file_path.exists():
                        new_nodes, new_edges = self._parser.parse_file(
                            file_path, self.project_root
                        )
                        new_node_ids.update(n.id for n in new_nodes)
                        all_nodes.extend(new_nodes)
                        all_edges.extend(new_edges)

                # Remove stale incoming edges to nodes that were deleted
                # (existed before but not after reparse)
                deleted_node_ids = removed_node_ids - new_node_ids
                if deleted_node_ids:
                    all_edges = [
                        e for e in all_edges
                        if e.to_node not in deleted_node_ids
                    ]

                # Normalize edge targets (fixes root != package root mismatch)
                normalize_edge_targets(all_nodes, all_edges)

                # Recompute metrics (class method counts, etc.)
                node_metrics, project_metrics = compute_all_metrics(
                    all_nodes, all_edges
                )
                for node in all_nodes:
                    if node.id in node_metrics:
                        node.metrics = node_metrics[node.id]

                database.save_graph(all_nodes, all_edges, self.graph_db)
                database.save_project_metrics(project_metrics, self.graph_db)
                self.invalidate_graph()
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

        # Compute diff
        new_index = {n.id: n for n in all_nodes}
        added = [n for nid, n in new_index.items() if nid not in old_index]
        deleted = [n for nid, n in old_index.items() if nid not in new_index]
        modified = [
            n for nid, n in new_index.items()
            if nid in old_index and n.hash != old_index[nid].hash
        ]

        self._update_config(current_files)
        return SyncResult(added=added, modified=modified, deleted=deleted)

    def _load_fingerprints(self) -> dict[str, dict[str, float | int]]:
        """Load file fingerprints from config."""
        if not self.config_path.exists():
            return {}
        try:
            config = json.loads(self.config_path.read_text())
            result: dict[str, dict[str, float | int]] = config.get(
                "file_fingerprints", {}
            )
            return result
        except (json.JSONDecodeError, KeyError):
            return {}

    def _update_config(
        self, fingerprints: dict[str, dict[str, float | int]] | None = None
    ) -> None:
        """Update config.json with current state and file fingerprints."""
        from datetime import datetime

        config: dict[str, object] = {}
        if self.config_path.exists():
            try:
                config = json.loads(self.config_path.read_text())
            except json.JSONDecodeError:
                config = {}

        config["last_sync"] = datetime.now(UTC).isoformat()
        config["parser_version"] = LensContext.PARSER_VERSION

        if fingerprints is not None:
            config["file_fingerprints"] = fingerprints
        else:
            # Always rebuild fingerprints from current files (full_sync)
            extensions = set(self._parser.get_file_extensions())
            fp: dict[str, dict[str, float | int]] = {}
            skip_dirs = {
                "__pycache__", ".git", ".lens", ".venv", "venv", "env",
                "node_modules", "site-packages", "lib",
            }
            venv_suffixes = ("-env", "-venv", "_env", "_venv")
            for file_path in self.project_root.rglob("*"):
                if not file_path.is_file():
                    continue
                skip = False
                for part in file_path.parts:
                    if part in skip_dirs or any(part.endswith(s) for s in venv_suffixes):
                        skip = True
                        break
                if skip:
                    continue
                if file_path.suffix not in extensions:
                    continue
                rel = str(file_path.relative_to(self.project_root))
                stat = file_path.stat()
                fp[rel] = {"mtime": stat.st_mtime, "size": stat.st_size}
            config["file_fingerprints"] = fp

        self.config_path.write_text(json.dumps(config, indent=2))
