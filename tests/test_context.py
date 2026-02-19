"""Tests for LensContext: version stamps, graph lifecycle, threading, rollback."""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lenspr import database
from lenspr.context import LensContext


@pytest.fixture
def lens_dir(tmp_path):
    """A .lens directory with an initialised graph DB."""
    from lenspr.database import init_database

    d = tmp_path / ".lens"
    init_database(d)
    return d


@pytest.fixture
def ctx(tmp_path, lens_dir):
    """A LensContext pointing at tmp_path with .lens already initialised."""
    return LensContext(project_root=tmp_path, lens_dir=lens_dir)


# ---------------------------------------------------------------------------
# _is_parser_version_stale
# ---------------------------------------------------------------------------


class TestIsParserVersionStale:
    def test_returns_false_when_no_config(self, tmp_path, lens_dir):
        """Fresh install: config.json does not exist → not stale."""
        assert not lens_dir.joinpath("config.json").exists()
        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._is_parser_version_stale() is False

    def test_returns_false_when_versions_match(self, tmp_path, lens_dir):
        """Config stores the current PARSER_VERSION → not stale."""
        config = {"parser_version": LensContext.PARSER_VERSION}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._is_parser_version_stale() is False

    def test_returns_true_when_version_mismatch(self, tmp_path, lens_dir):
        """Config stores an older version → stale, triggers full resync."""
        config = {"parser_version": "0"}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._is_parser_version_stale() is True

    def test_returns_false_on_corrupted_json(self, tmp_path, lens_dir):
        """Corrupted config.json → silently returns False (safe fallback)."""
        lens_dir.joinpath("config.json").write_text("{not valid json")

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._is_parser_version_stale() is False

    def test_returns_true_when_version_key_missing(self, tmp_path, lens_dir):
        """Config exists but has no parser_version key → defaults to '0' → stale."""
        config = {"last_sync": "2025-01-01T00:00:00"}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        # Default stored version is "0"; if current PARSER_VERSION != "0" it's stale
        expected = LensContext.PARSER_VERSION != "0"
        assert c._is_parser_version_stale() is expected


# ---------------------------------------------------------------------------
# __init__ sets _needs_full_sync correctly
# ---------------------------------------------------------------------------


class TestInitSetsNeedsFullSync:
    def test_false_when_no_config(self, tmp_path, lens_dir):
        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._needs_full_sync is False

    def test_false_when_version_current(self, tmp_path, lens_dir):
        config = {"parser_version": LensContext.PARSER_VERSION}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._needs_full_sync is False

    def test_true_when_version_mismatch(self, tmp_path, lens_dir):
        config = {"parser_version": "0"}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._needs_full_sync is True


# ---------------------------------------------------------------------------
# ensure_synced — version-mismatch path
# ---------------------------------------------------------------------------


class TestEnsureSyncedVersionMismatch:
    def test_calls_full_sync_and_clears_flag(self, tmp_path, lens_dir):
        """When _needs_full_sync is True, ensure_synced runs _full_sync_locked."""
        config = {"parser_version": "0"}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        assert c._needs_full_sync is True

        with patch.object(c, "_full_sync_locked") as mock_sync:
            c.ensure_synced()

        mock_sync.assert_called_once()
        assert c._needs_full_sync is False

    def test_raises_runtime_error_when_full_sync_fails(self, tmp_path, lens_dir):
        """Propagates sync failure as RuntimeError with descriptive message."""
        config = {"parser_version": "0"}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)

        with patch.object(c, "_full_sync_locked", side_effect=RuntimeError("disk full")):
            with pytest.raises(RuntimeError, match="Graph sync failed during parser upgrade"):
                c.ensure_synced()

        # Flag stays True when sync fails (so next call retries)
        assert c._needs_full_sync is True

    def test_skips_incremental_sync_when_version_mismatch(self, tmp_path, lens_dir):
        """ensure_synced returns early after full sync — does not run incremental."""
        config = {"parser_version": "0"}
        lens_dir.joinpath("config.json").write_text(json.dumps(config))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)

        with patch.object(c, "_full_sync_locked"):
            with patch.object(c, "incremental_sync") as mock_incremental:
                c.ensure_synced()

        mock_incremental.assert_not_called()


# ---------------------------------------------------------------------------
# ensure_synced — no-changes fast path
# ---------------------------------------------------------------------------


class TestEnsureSyncedNoPendingChanges:
    def test_returns_early_when_no_pending_changes(self, ctx):
        """No pending changes → neither sync method is called."""
        with patch.object(ctx, "has_pending_changes", return_value=False):
            with patch.object(ctx, "incremental_sync") as mock_inc:
                ctx.ensure_synced()

        mock_inc.assert_not_called()


# ---------------------------------------------------------------------------
# _update_config writes parser_version
# ---------------------------------------------------------------------------


class TestUpdateConfigWritesParserVersion:
    def test_writes_parser_version_to_config(self, ctx):
        """_update_config must persist PARSER_VERSION to config.json."""
        ctx._update_config(fingerprints={})

        config = json.loads(ctx.config_path.read_text())
        assert config["parser_version"] == LensContext.PARSER_VERSION

    def test_overwrites_stale_parser_version(self, tmp_path, lens_dir):
        """Calling _update_config on a ctx with old stored version updates it."""
        lens_dir.joinpath("config.json").write_text(json.dumps({"parser_version": "0"}))

        c = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        c._update_config(fingerprints={})

        config = json.loads(lens_dir.joinpath("config.json").read_text())
        assert config["parser_version"] == LensContext.PARSER_VERSION


# ---------------------------------------------------------------------------
# Full project fixture for behavioral tests
# ---------------------------------------------------------------------------


@pytest.fixture
def full_project(tmp_path):
    """A project with two Python files, initialised and synced."""
    (tmp_path / "app.py").write_text(
        "def greet(name):\n"
        "    return f'hello {name}'\n"
    )
    (tmp_path / "utils.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
    )
    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)
    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# Graph lifecycle
# ---------------------------------------------------------------------------


class TestGraphLifecycle:
    def test_get_graph_builds_from_db(self, full_project):
        """get_graph returns a NetworkX graph with nodes from the project."""
        g = full_project.get_graph()
        node_ids = set(g.nodes)
        # Should contain the app module and greet function at minimum
        assert any("greet" in nid for nid in node_ids), (
            f"Expected 'greet' in graph nodes, got: {node_ids}"
        )

    def test_invalidate_graph_clears_cache(self, full_project):
        """After invalidate_graph, the cached _graph is None and get_graph rebuilds."""
        _ = full_project.get_graph()  # populate cache
        assert full_project._graph is not None

        full_project.invalidate_graph()
        assert full_project._graph is None

        # get_graph should rebuild from DB
        g = full_project.get_graph()
        assert g is not None
        assert any("greet" in nid for nid in g.nodes)

    def test_has_pending_changes_after_file_modification(self, full_project):
        """Modifying a tracked file makes has_pending_changes return True."""
        assert not full_project.has_pending_changes(), (
            "Freshly synced project should have no pending changes"
        )

        # Modify a file — change mtime
        app_path = full_project.project_root / "app.py"
        time.sleep(0.05)  # ensure mtime differs
        app_path.write_text(
            "def greet(name):\n"
            "    return f'hi {name}'\n"
        )

        assert full_project.has_pending_changes(), (
            "After modifying app.py, has_pending_changes should be True"
        )

    def test_has_pending_changes_detects_new_file(self, full_project):
        """Adding a new .py file is detected as a pending change."""
        (full_project.project_root / "new_module.py").write_text(
            "def new_func():\n    pass\n"
        )

        assert full_project.has_pending_changes()

    def test_incremental_sync_updates_only_changed_files(self, full_project):
        """After modifying one file, incremental_sync re-parses only that file."""
        # Modify only app.py
        time.sleep(0.05)
        app_path = full_project.project_root / "app.py"
        app_path.write_text(
            "def greet(name):\n"
            "    return f'hey {name}'\n"
            "\n"
            "def farewell(name):\n"
            "    return f'bye {name}'\n"
        )

        result = full_project.incremental_sync()

        # app.py should be in modified list
        modified_files = [str(p) for p in result.modified]
        assert any("app.py" in f for f in modified_files), (
            f"app.py should be in modified list, got: {modified_files}"
        )

        # utils.py should NOT be re-parsed
        assert not any("utils.py" in f for f in modified_files), (
            "utils.py was not changed and should not be in modified list"
        )

        # New function should now be in the graph
        g = full_project.get_graph()
        assert any("farewell" in nid for nid in g.nodes), (
            "farewell function should appear in graph after incremental sync"
        )


# ---------------------------------------------------------------------------
# Rollback / error resilience
# ---------------------------------------------------------------------------


class TestRollbackBehavior:
    def test_reparse_preserves_graph_on_parse_failure(self, full_project):
        """If parser.parse_file raises, the DB should retain the old graph."""
        # Get node count before
        nodes_before, _ = database.load_graph(full_project.graph_db)
        count_before = len(nodes_before)
        assert count_before > 0

        app_path = full_project.project_root / "app.py"

        # Mock parse_file to simulate a parser crash
        with patch.object(
            full_project._parser, "parse_file", side_effect=RuntimeError("parse crash")
        ):
            with pytest.raises(RuntimeError, match="parse crash"):
                full_project.reparse_file(app_path)

        # DB should still have the old graph (save_graph was never called)
        nodes_after, _ = database.load_graph(full_project.graph_db)
        assert len(nodes_after) == count_before, (
            f"Graph should be unchanged after parse failure. "
            f"Before: {count_before}, after: {len(nodes_after)}"
        )

    def test_full_sync_propagates_error_without_corrupting_db(self, full_project):
        """If save_graph fails during full_sync, existing data remains intact."""
        nodes_before, _ = database.load_graph(full_project.graph_db)
        count_before = len(nodes_before)

        with patch(
            "lenspr.context.database.save_graph",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                full_project.full_sync()

        # DB still has old data (the failed save_graph didn't write)
        nodes_after, _ = database.load_graph(full_project.graph_db)
        assert len(nodes_after) == count_before


# ---------------------------------------------------------------------------
# Threading safety
# ---------------------------------------------------------------------------


class TestMetricsComputation:
    """Metrics must be computed in ALL sync paths, not just full_sync."""

    @pytest.fixture
    def class_project(self, tmp_path):
        """Project with a class that has methods."""
        (tmp_path / "models.py").write_text(
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "    def greet(self):\n"
            "        return f'hi {self.name}'\n"
            "\n"
            "    def farewell(self):\n"
            "        return f'bye {self.name}'\n"
        )
        lens_dir = tmp_path / ".lens"
        lens_dir.mkdir()
        database.init_database(lens_dir)
        ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        ctx.full_sync()
        return ctx

    def test_reparse_file_computes_class_metrics(self, class_project):
        """After reparse_file, class should have method_count > 0."""
        ctx = class_project
        ctx.reparse_file(ctx.project_root / "models.py")

        nodes, _ = database.load_graph(ctx.graph_db)
        user_cls = [n for n in nodes if n.name == "User" and n.type.value == "class"]
        assert len(user_cls) == 1
        assert user_cls[0].metrics is not None, "metrics should not be None after reparse"
        assert user_cls[0].metrics.get("method_count", 0) >= 2, (
            f"User class should have >=2 methods, got: {user_cls[0].metrics}"
        )

    def test_incremental_sync_computes_class_metrics(self, class_project):
        """After incremental_sync, class should have method_count > 0."""
        ctx = class_project

        # Modify models.py to trigger incremental sync
        time.sleep(0.05)
        (ctx.project_root / "models.py").write_text(
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "    def greet(self):\n"
            "        return f'hello {self.name}'\n"  # changed
            "\n"
            "    def farewell(self):\n"
            "        return f'bye {self.name}'\n"
            "\n"
            "    def status(self):\n"
            "        return 'active'\n"  # new method
        )

        result = ctx.incremental_sync()
        assert len(result.modified) > 0

        nodes, _ = database.load_graph(ctx.graph_db)
        user_cls = [n for n in nodes if n.name == "User" and n.type.value == "class"]
        assert len(user_cls) == 1
        assert user_cls[0].metrics is not None, "metrics should not be None after incremental sync"
        assert user_cls[0].metrics.get("method_count", 0) >= 3, (
            f"User class should have >=3 methods after adding status(), got: {user_cls[0].metrics}"
        )


# ---------------------------------------------------------------------------
# Edge preservation (incoming edges survive reparse)
# ---------------------------------------------------------------------------


class TestEdgePreservation:
    """Incoming cross-file edges must survive reparse/incremental sync.

    Regression tests for the bug where _reparse_file_locked and
    _incremental_sync_locked removed ALL edges where to_node was in the
    reparsed file, permanently destroying incoming edges from other files.
    """

    @pytest.fixture
    def cross_dep_project(self, tmp_path):
        """Project where app.py calls utils.helper()."""
        (tmp_path / "utils.py").write_text(
            "def helper():\n"
            "    return 42\n"
        )
        (tmp_path / "app.py").write_text(
            "from utils import helper\n"
            "\n"
            "def main():\n"
            "    return helper()\n"
        )
        lens_dir = tmp_path / ".lens"
        lens_dir.mkdir()
        database.init_database(lens_dir)
        ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        ctx.full_sync()
        return ctx

    def _count_incoming_edges(self, ctx, target_fn):
        """Count edges TO target_fn from other files."""
        _, edges = database.load_graph(ctx.graph_db)
        return [
            e for e in edges
            if target_fn in e.to_node
            and not e.from_node.startswith(e.to_node.rsplit(".", 1)[0].rsplit(".", 1)[0])
        ]

    def test_reparse_target_preserves_incoming_edges(self, cross_dep_project):
        """Reparsing utils.py must NOT destroy the app.main -> utils.helper edge."""
        ctx = cross_dep_project

        # Verify edge exists after full sync
        _, edges_before = database.load_graph(ctx.graph_db)
        calls_to_helper = [
            e for e in edges_before
            if "helper" in e.to_node and "main" in e.from_node
        ]
        assert len(calls_to_helper) > 0, "app.main -> utils.helper edge should exist"

        # Reparse the TARGET file (utils.py) — this used to kill incoming edges
        ctx.reparse_file(ctx.project_root / "utils.py")

        # Edge from app.main -> utils.helper must survive
        _, edges_after = database.load_graph(ctx.graph_db)
        calls_after = [
            e for e in edges_after
            if "helper" in e.to_node and "main" in e.from_node
        ]
        assert len(calls_after) > 0, (
            "app.main -> utils.helper edge was destroyed by reparse of utils.py"
        )

    def test_incremental_sync_preserves_incoming_edges(self, cross_dep_project):
        """Incremental sync of utils.py must NOT destroy incoming edges."""
        ctx = cross_dep_project

        # Modify only utils.py
        time.sleep(0.05)
        (ctx.project_root / "utils.py").write_text(
            "def helper():\n"
            "    return 99\n"  # changed return value
        )

        result = ctx.incremental_sync()
        assert len(result.modified) > 0, "utils.py should be in modified list"

        # Edge from app.main -> utils.helper must survive
        _, edges_after = database.load_graph(ctx.graph_db)
        calls_after = [
            e for e in edges_after
            if "helper" in e.to_node and "main" in e.from_node
        ]
        assert len(calls_after) > 0, (
            "app.main -> utils.helper edge was destroyed by incremental sync of utils.py"
        )

    def test_reparse_removes_edges_to_deleted_nodes(self, cross_dep_project):
        """If a function is REMOVED from a file, incoming edges to it are cleaned up."""
        ctx = cross_dep_project

        # Rewrite utils.py WITHOUT helper()
        (ctx.project_root / "utils.py").write_text(
            "def other_func():\n"
            "    return 0\n"
        )
        ctx.reparse_file(ctx.project_root / "utils.py")

        # Stale edge to deleted helper should be gone
        _, edges_after = database.load_graph(ctx.graph_db)
        stale = [e for e in edges_after if "helper" in e.to_node]
        assert len(stale) == 0, (
            f"Stale edges to deleted helper() should be removed, found: "
            f"{[(e.from_node, e.to_node) for e in stale]}"
        )

    def test_reparse_source_recreates_outgoing_edges(self, cross_dep_project):
        """Reparsing app.py (the CALLER) should recreate its outgoing edges."""
        ctx = cross_dep_project

        # Reparse app.py — outgoing edges should be recreated
        ctx.reparse_file(ctx.project_root / "app.py")

        _, edges_after = database.load_graph(ctx.graph_db)
        calls_after = [
            e for e in edges_after
            if "helper" in e.to_node and "main" in e.from_node
        ]
        assert len(calls_after) > 0, (
            "app.main -> utils.helper edge should be recreated when app.py is reparsed"
        )


# ---------------------------------------------------------------------------
# Threading safety
# ---------------------------------------------------------------------------


class TestThreadingSafety:
    def test_concurrent_full_sync_does_not_corrupt_graph(self, tmp_path):
        """Two threads calling full_sync simultaneously should not corrupt the graph.

        The threading.Lock in full_sync serializes access. We verify that
        after both complete, the graph is consistent (no missing nodes, no
        duplicates beyond what the parser produces).
        """
        (tmp_path / "a.py").write_text("def func_a():\n    return 1\n")
        (tmp_path / "b.py").write_text("def func_b():\n    return 2\n")
        lens_dir = tmp_path / ".lens"
        lens_dir.mkdir()
        database.init_database(lens_dir)
        ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)

        errors = []

        def sync_thread():
            try:
                ctx.full_sync()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=sync_thread)
        t2 = threading.Thread(target=sync_thread)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"Threads raised errors: {errors}"

        # Graph should be consistent
        g = ctx.get_graph()
        node_names = [g.nodes[n].get("name", "") for n in g.nodes]
        assert "func_a" in node_names, "func_a missing from graph after concurrent sync"
        assert "func_b" in node_names, "func_b missing from graph after concurrent sync"

    def test_concurrent_ensure_synced_serializes(self, full_project):
        """Multiple threads calling ensure_synced don't cause race conditions.

        Even if has_pending_changes is True, the lock should serialize access.
        """
        # Modify a file to trigger pending changes
        time.sleep(0.05)
        (full_project.project_root / "app.py").write_text(
            "def greet(name):\n    return f'yo {name}'\n"
        )

        errors = []
        results = []

        def syncer():
            try:
                full_project.ensure_synced()
                results.append("ok")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=syncer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Threads raised errors: {errors}"
        assert len(results) == 4, "All threads should complete successfully"
