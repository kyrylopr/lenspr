"""Integration tests for tool operations (update, add, delete, rename).

These tests verify that tool handlers correctly modify files,
reparse the graph, and maintain consistency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.claude_tools import (
    _handle_add_node,
    _handle_delete_node,
    _handle_update_node,
)
from lenspr.context import LensContext


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Create a minimal project with initialized LensPR context."""
    # Create source files
    src = tmp_path / "app.py"
    src.write_text(
        "def greet(name):\n"
        '    return f"Hello, {name}"\n'
        "\n"
        "\n"
        "def farewell(name):\n"
        '    return f"Goodbye, {name}"\n'
    )

    helper = tmp_path / "utils.py"
    helper.write_text(
        "from app import greet\n"
        "\n"
        "\n"
        "def welcome(name):\n"
        "    return greet(name) + '!'\n"
    )

    # Initialize LensPR
    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


class TestUpdateNode:
    def test_update_modifies_file(self, project: LensContext) -> None:
        result = _handle_update_node(
            {"node_id": "app.greet", "new_source": "def greet(name):\n    return name.upper()"},
            project,
        )
        assert result.success
        content = (project.project_root / "app.py").read_text()
        assert "name.upper()" in content

    def test_update_reparses_graph(self, project: LensContext) -> None:
        """After update, the graph should reflect the new source code."""
        _handle_update_node(
            {"node_id": "app.greet", "new_source": "def greet(name):\n    return name.upper()"},
            project,
        )
        node = database.get_node("app.greet", project.graph_db)
        assert node is not None
        assert "name.upper()" in node.source_code

    def test_update_rejects_invalid_syntax(self, project: LensContext) -> None:
        result = _handle_update_node(
            {"node_id": "app.greet", "new_source": "def greet(\n    broken syntax"},
            project,
        )
        assert not result.success
        # File should be unchanged
        content = (project.project_root / "app.py").read_text()
        assert "Hello" in content

    def test_update_rejects_structure_change(self, project: LensContext) -> None:
        """Can't turn a function into a class."""
        result = _handle_update_node(
            {"node_id": "app.greet", "new_source": "class greet:\n    pass"},
            project,
        )
        assert not result.success

    def test_update_nonexistent_node(self, project: LensContext) -> None:
        result = _handle_update_node(
            {"node_id": "app.nonexistent", "new_source": "def x():\n    pass"},
            project,
        )
        assert not result.success

    def test_update_preserves_other_functions(self, project: LensContext) -> None:
        _handle_update_node(
            {"node_id": "app.greet", "new_source": "def greet(name):\n    return name.upper()"},
            project,
        )
        content = (project.project_root / "app.py").read_text()
        assert "Goodbye" in content  # farewell unchanged


class TestAddNode:
    def test_add_appends_to_file(self, project: LensContext) -> None:
        result = _handle_add_node(
            {
                "file_path": "app.py",
                "source_code": "def new_func():\n    return 42",
            },
            project,
        )
        assert result.success
        content = (project.project_root / "app.py").read_text()
        assert "def new_func():" in content

    def test_add_reparses_graph(self, project: LensContext) -> None:
        _handle_add_node(
            {
                "file_path": "app.py",
                "source_code": "def new_func():\n    return 42",
            },
            project,
        )
        node = database.get_node("app.new_func", project.graph_db)
        assert node is not None
        assert "return 42" in node.source_code

    def test_add_rejects_invalid_syntax(self, project: LensContext) -> None:
        result = _handle_add_node(
            {
                "file_path": "app.py",
                "source_code": "def broken(\n    ????",
            },
            project,
        )
        assert not result.success

    def test_add_to_nonexistent_file(self, project: LensContext) -> None:
        result = _handle_add_node(
            {
                "file_path": "nonexistent.py",
                "source_code": "def x():\n    pass",
            },
            project,
        )
        assert not result.success

    def test_add_after_specific_node(self, project: LensContext) -> None:
        result = _handle_add_node(
            {
                "file_path": "app.py",
                "source_code": "def middle():\n    return 'middle'",
                "after_node": "app.greet",
            },
            project,
        )
        assert result.success
        content = (project.project_root / "app.py").read_text()
        lines = content.splitlines()
        # middle should appear between greet and farewell
        middle_line = next(i for i, line in enumerate(lines) if "def middle" in line)
        farewell_line = next(i for i, line in enumerate(lines) if "def farewell" in line)
        assert middle_line < farewell_line


class TestDeleteNode:
    def test_delete_removes_from_file(self, project: LensContext) -> None:
        result = _handle_delete_node({"node_id": "app.farewell"}, project)
        assert result.success
        content = (project.project_root / "app.py").read_text()
        assert "farewell" not in content

    def test_delete_removes_from_graph(self, project: LensContext) -> None:
        _handle_delete_node({"node_id": "app.farewell"}, project)
        node = database.get_node("app.farewell", project.graph_db)
        assert node is None

    def test_delete_preserves_other_functions(self, project: LensContext) -> None:
        _handle_delete_node({"node_id": "app.farewell"}, project)
        content = (project.project_root / "app.py").read_text()
        assert "def greet" in content

    def test_delete_nonexistent_node(self, project: LensContext) -> None:
        result = _handle_delete_node({"node_id": "app.nonexistent"}, project)
        assert not result.success
