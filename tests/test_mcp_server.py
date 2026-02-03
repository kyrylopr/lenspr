"""Tests for MCP server: watcher logic and tool wrappers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import lenspr
from lenspr import database
from lenspr.context import LensContext
from lenspr.mcp_server import _start_watcher


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Create a minimal project with initialized LensPR context."""
    src = tmp_path / "app.py"
    src.write_text(
        "def greet(name):\n"
        '    return f"Hello, {name}"\n'
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


class TestWatcherStartup:
    """Test that watcher functions start without errors."""

    def test_start_watcher_with_watchdog(self, project: LensContext) -> None:
        """Watcher starts without crashing when watchdog is available."""
        # Initialize lenspr module-level context so sync() works
        lenspr._ctx = project

        # Should not raise
        _start_watcher(str(project.project_root))

        # Give daemon threads a moment to start
        time.sleep(0.1)

    def test_start_watcher_falls_back_to_polling(
        self, project: LensContext
    ) -> None:
        """When watchdog is not available, falls back to polling."""
        lenspr._ctx = project

        with patch(
            "lenspr.mcp_server._start_watcher",
            wraps=_start_watcher,
        ):
            # Simulate missing watchdog by patching the import inside
            import builtins

            real_import = builtins.__import__

            def mock_import(name: str, *args: object, **kwargs: object) -> object:
                if "watchdog" in name:
                    raise ImportError("mocked")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                _start_watcher(str(project.project_root))

        time.sleep(0.1)


class TestWatchdogHandler:
    """Test the watchdog event handler logic."""

    def test_py_file_sets_pending(self) -> None:
        """Modifying a .py file sets the _pending_sync flag."""
        pytest.importorskip("watchdog")
        from watchdog.events import FileSystemEventHandler

        from lenspr.mcp_server import _start_watchdog_watcher

        # We need to extract the handler class. Patch observer to not start.
        mock_observer_cls = MagicMock()
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        with patch("lenspr.sync"):
            _start_watchdog_watcher(
                "/fake/path",
                FileSystemEventHandler,
                mock_observer_cls,
            )

        # The handler was passed to observer.schedule()
        handler = mock_observer.schedule.call_args[0][0]

        # Simulate a .py file modification
        event = SimpleNamespace(src_path="/fake/path/test.py", is_directory=False)
        handler.on_modified(event)

        with handler._lock:
            assert handler._pending_sync is True

    def test_non_py_file_ignored(self) -> None:
        """Modifying a non-.py file does NOT set _pending_sync."""
        pytest.importorskip("watchdog")
        from watchdog.events import FileSystemEventHandler

        from lenspr.mcp_server import _start_watchdog_watcher

        mock_observer_cls = MagicMock()
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        with patch("lenspr.sync"):
            _start_watchdog_watcher(
                "/fake/path",
                FileSystemEventHandler,
                mock_observer_cls,
            )

        handler = mock_observer.schedule.call_args[0][0]

        event = SimpleNamespace(src_path="/fake/path/data.json", is_directory=False)
        handler.on_modified(event)

        with handler._lock:
            assert handler._pending_sync is False

    def test_created_event_sets_pending(self) -> None:
        """Creating a .py file sets the _pending_sync flag."""
        pytest.importorskip("watchdog")
        from watchdog.events import FileSystemEventHandler

        from lenspr.mcp_server import _start_watchdog_watcher

        mock_observer_cls = MagicMock()
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        with patch("lenspr.sync"):
            _start_watchdog_watcher(
                "/fake/path",
                FileSystemEventHandler,
                mock_observer_cls,
            )

        handler = mock_observer.schedule.call_args[0][0]

        event = SimpleNamespace(src_path="/fake/path/new.py", is_directory=False)
        handler.on_created(event)

        with handler._lock:
            assert handler._pending_sync is True

    def test_deleted_event_sets_pending(self) -> None:
        """Deleting a .py file sets the _pending_sync flag."""
        pytest.importorskip("watchdog")
        from watchdog.events import FileSystemEventHandler

        from lenspr.mcp_server import _start_watchdog_watcher

        mock_observer_cls = MagicMock()
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        with patch("lenspr.sync"):
            _start_watchdog_watcher(
                "/fake/path",
                FileSystemEventHandler,
                mock_observer_cls,
            )

        handler = mock_observer.schedule.call_args[0][0]

        event = SimpleNamespace(src_path="/fake/path/old.py", is_directory=False)
        handler.on_deleted(event)

        with handler._lock:
            assert handler._pending_sync is True


class TestAutoSync:
    """Test that file changes trigger graph re-sync."""

    def test_file_change_triggers_sync(self, project: LensContext) -> None:
        """Adding a new function to a file is picked up by incremental sync."""
        lenspr._ctx = project

        # Verify initial state
        g = project.get_graph()
        assert "app.greet" in g

        # Modify file
        app_py = project.project_root / "app.py"
        app_py.write_text(
            "def greet(name):\n"
            '    return f"Hello, {name}"\n'
            "\n"
            "\n"
            "def farewell(name):\n"
            '    return f"Goodbye, {name}"\n'
        )

        # Manual sync (what the watcher would do)
        lenspr.sync()

        # Graph should now have the new function
        project.invalidate_graph()
        g = project.get_graph()
        assert "app.farewell" in g

    def test_file_deletion_triggers_sync(self, project: LensContext) -> None:
        """Deleting a file removes its nodes from the graph."""
        lenspr._ctx = project

        # Add a second file
        helper = project.project_root / "helper.py"
        helper.write_text("def helper_fn():\n    pass\n")
        lenspr.sync()
        project.invalidate_graph()
        g = project.get_graph()
        assert "helper.helper_fn" in g

        # Delete the file
        helper.unlink()
        lenspr.sync(full=True)
        project.invalidate_graph()
        g = project.get_graph()
        assert "helper.helper_fn" not in g

    def test_incremental_sync_resolves_local_calls(self, project: LensContext) -> None:
        """New file with local function calls gets resolved edges via incremental sync."""
        from lenspr import database

        lenspr._ctx = project

        # Create a new file with local function calls
        new_file = project.project_root / "module_with_helpers.py"
        new_file.write_text(
            "def _helper():\n"
            "    return 42\n"
            "\n"
            "\n"
            "def main_func():\n"
            "    return _helper() + 1\n"
        )

        # Incremental sync (not full)
        lenspr.sync(full=False)
        project.invalidate_graph()

        # Check that the local call edge is RESOLVED, not INFERRED
        edges = database.get_edges(
            "module_with_helpers.main_func", project.graph_db, direction="outgoing"
        )

        # Find the edge to _helper
        helper_edges = [e for e in edges if "_helper" in e.to_node]
        assert len(helper_edges) >= 1, "Expected edge from main_func to _helper"

        # The edge should be RESOLVED (jedi confirmed) not INFERRED
        helper_edge = helper_edges[0]
        assert helper_edge.confidence.value == "resolved", (
            f"Expected 'resolved' confidence for local call, got '{helper_edge.confidence.value}'"
        )


class TestHotReload:
    """Test hot-reload functionality."""

    def test_lenspr_file_detection(self) -> None:
        """Correctly identifies lenspr package files."""
        from lenspr.mcp_server import _is_lenspr_file

        assert _is_lenspr_file("/project/lenspr/tools/analysis.py") is True
        assert _is_lenspr_file("/project/lenspr/mcp_server.py") is True
        assert _is_lenspr_file("/project/app.py") is False
        assert _is_lenspr_file("/project/lenspr/README.md") is False

    def test_hot_reload_flag_triggers_pending_reload(self) -> None:
        """When hot_reload=True, lenspr file changes set _pending_reload."""
        pytest.importorskip("watchdog")
        from watchdog.events import FileSystemEventHandler

        from lenspr.mcp_server import _start_watchdog_watcher

        mock_observer_cls = MagicMock()
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer

        with patch("lenspr.sync"):
            _start_watchdog_watcher(
                "/project",
                FileSystemEventHandler,
                mock_observer_cls,
                hot_reload=True,
            )

        handler = mock_observer.schedule.call_args[0][0]

        # Simulate modifying a lenspr file
        event = SimpleNamespace(
            src_path="/project/lenspr/tools/analysis.py", is_directory=False
        )
        handler.on_modified(event)

        with handler._lock:
            assert handler._pending_sync is True
            assert handler._pending_reload is True

    def test_enable_hot_reload(self) -> None:
        """Test hot-reload enable/disable function."""
        from lenspr.tools import _hot_reload_enabled, enable_hot_reload

        # Store initial state
        initial = _hot_reload_enabled

        try:
            enable_hot_reload(True)
            from lenspr import tools
            assert tools._hot_reload_enabled is True

            enable_hot_reload(False)
            assert tools._hot_reload_enabled is False
        finally:
            # Restore initial state
            enable_hot_reload(initial)


class TestMCPToolWrappers:
    """Test that MCP tool wrappers produce valid JSON responses."""

    def test_handle_tool_list_nodes(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_list_nodes", {})
        assert result["success"]
        assert result["data"]["count"] > 0
        # Verify it serializes to JSON without error
        json.dumps(result)

    def test_handle_tool_get_node(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_get_node", {"node_id": "app.greet"})
        assert result["success"]
        assert "source_code" in result["data"]
        json.dumps(result)

    def test_handle_tool_context(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_context", {"node_id": "app.greet"})
        assert result["success"]
        assert "target" in result["data"]
        json.dumps(result)

    def test_handle_tool_grep(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_grep", {"pattern": "Hello"})
        assert result["success"]
        assert result["data"]["count"] > 0
        json.dumps(result)

    def test_handle_tool_search(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_search", {"query": "greet"})
        assert result["success"]
        json.dumps(result)

    def test_handle_tool_structure(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_get_structure", {"max_depth": 2})
        assert result["success"]
        json.dumps(result)

    def test_handle_tool_check_impact(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool(
            "lens_check_impact", {"node_id": "app.greet", "depth": 1}
        )
        assert result["success"]
        json.dumps(result)

    def test_handle_tool_unknown(self, project: LensContext) -> None:
        lenspr._ctx = project
        result = lenspr.handle_tool("lens_nonexistent", {})
        assert not result["success"]
        assert "Unknown tool" in result["error"]
