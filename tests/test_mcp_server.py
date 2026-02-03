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
        """Modifying a .py file sets the _pending flag."""
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
            assert handler._pending is True

    def test_non_py_file_ignored(self) -> None:
        """Modifying a non-.py file does NOT set _pending."""
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
            assert handler._pending is False

    def test_created_event_sets_pending(self) -> None:
        """Creating a .py file sets the _pending flag."""
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
            assert handler._pending is True

    def test_deleted_event_sets_pending(self) -> None:
        """Deleting a .py file sets the _pending flag."""
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
            assert handler._pending is True


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
