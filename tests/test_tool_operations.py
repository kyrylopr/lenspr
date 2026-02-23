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
    _handle_batch,
    _handle_context,
    _handle_delete_node,
    _handle_diff,
    _handle_explain,
    _handle_grep,
    _handle_health,
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
        from lenspr import database
        from lenspr.models import NodeType
        from lenspr.validator import validate_structure

        node = database.get_node("app.greet", project.graph_db)
        assert node is not None, "Node app.greet not found in database"
        assert node.type == NodeType.FUNCTION, f"Expected FUNCTION, got {node.type}"

        # Test the validator directly
        validation = validate_structure("class greet:\n    pass", node)
        assert not validation.valid, (
            f"Validation should fail but got valid=True. "
            f"node.type={node.type}, node.type.value={node.type.value}"
        )

        # Now test the full handler
        result = _handle_update_node(
            {"node_id": "app.greet", "new_source": "class greet:\n    pass"},
            project,
        )
        assert not result.success, f"Expected failure but got success. Error: {result.error}"

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


@pytest.fixture
def project_with_tests(tmp_path: Path) -> LensContext:
    """Create a project with source + test files for context/grep tests."""
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

    test_file = tmp_path / "test_app.py"
    test_file.write_text(
        "from app import greet, farewell\n"
        "\n"
        "\n"
        "def test_greet():\n"
        "    assert greet('World') == 'Hello, World'\n"
        "\n"
        "\n"
        "def test_farewell():\n"
        "    assert farewell('World') == 'Goodbye, World'\n"
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


class TestContext:
    def test_returns_target_node(self, project_with_tests: LensContext) -> None:
        result = _handle_context({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert result.data["target"]["id"] == "app.greet"
        assert "source_code" in result.data["target"]

    def test_includes_callers(self, project_with_tests: LensContext) -> None:
        result = _handle_context({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        caller_ids = [c["id"] for c in result.data["callers"]]
        assert "utils.welcome" in caller_ids

    def test_includes_callees(self, project_with_tests: LensContext) -> None:
        result = _handle_context({"node_id": "utils.welcome"}, project_with_tests)
        assert result.success
        assert result.data is not None
        callee_ids = [c["id"] for c in result.data["callees"]]
        assert "app.greet" in callee_ids

    def test_includes_tests(self, project_with_tests: LensContext) -> None:
        result = _handle_context({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        test_ids = [t["id"] for t in result.data["tests"]]
        assert "test_app.test_greet" in test_ids

    def test_excludes_callers(self, project_with_tests: LensContext) -> None:
        result = _handle_context(
            {"node_id": "app.greet", "include_callers": False},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        assert "callers" not in result.data

    def test_excludes_tests(self, project_with_tests: LensContext) -> None:
        result = _handle_context(
            {"node_id": "app.greet", "include_tests": False},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        assert "tests" not in result.data

    def test_nonexistent_node(self, project_with_tests: LensContext) -> None:
        result = _handle_context({"node_id": "app.nope"}, project_with_tests)
        assert not result.success

    def test_caller_metadata_included(self, project_with_tests: LensContext) -> None:
        result = _handle_context({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        for caller in result.data["callers"]:
            assert "source_code" not in caller
            assert "start_line" in caller
            assert "end_line" in caller
            assert "signature" in caller


class TestGrep:
    def test_finds_pattern(self, project_with_tests: LensContext) -> None:
        result = _handle_grep({"pattern": "Hello"}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert result.data["count"] > 0

    def test_returns_graph_context(self, project_with_tests: LensContext) -> None:
        result = _handle_grep({"pattern": "Hello"}, project_with_tests)
        assert result.success
        assert result.data is not None
        for match in result.data["results"]:
            assert "file" in match
            assert "line" in match
            # Should have node context for matches inside functions
            if match["file"] == "app.py":
                assert "node_id" in match

    def test_regex_pattern(self, project_with_tests: LensContext) -> None:
        result = _handle_grep({"pattern": r"def \w+greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert result.data["count"] > 0

    def test_file_glob_filter(self, project_with_tests: LensContext) -> None:
        result = _handle_grep(
            {"pattern": "greet", "file_glob": "test_*.py"},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        for match in result.data["results"]:
            assert match["file"].startswith("test_")

    def test_max_results(self, project_with_tests: LensContext) -> None:
        result = _handle_grep(
            {"pattern": "def", "max_results": 2},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        assert result.data["count"] <= 2
        assert result.data["truncated"]

    def test_no_results(self, project_with_tests: LensContext) -> None:
        result = _handle_grep(
            {"pattern": "zzz_nonexistent_zzz"},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        assert result.data["count"] == 0


class TestContextIncludeSource:
    def test_include_source_true(self, project_with_tests: LensContext) -> None:
        result = _handle_context(
            {"node_id": "app.greet", "include_source": True},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        # include_source=True affects only the target node
        assert result.data["target"]["source_code"] is not None
        # Callers always get metadata only
        for caller in result.data["callers"]:
            assert "source_code" not in caller
            assert "start_line" in caller
            assert "end_line" in caller

    def test_include_source_false(self, project_with_tests: LensContext) -> None:
        result = _handle_context(
            {"node_id": "app.greet", "include_source": False},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        # include_source=False suppresses target source
        assert result.data["target"]["source_code"] is None
        # Callers always get metadata only (same as include_source=True)
        for caller in result.data["callers"]:
            assert "source_code" not in caller
            assert "start_line" in caller
            assert "end_line" in caller


class TestDiff:
    def test_no_changes(self, project_with_tests: LensContext) -> None:
        result = _handle_diff({}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert result.data["total_changes"] == 0

    def test_detects_new_file(self, project_with_tests: LensContext) -> None:
        new_file = project_with_tests.project_root / "new_module.py"
        new_file.write_text("def new_fn():\n    pass\n")
        result = _handle_diff({}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert "new_module.py" in result.data["added_files"]

    def test_detects_deleted_file(self, project_with_tests: LensContext) -> None:
        (project_with_tests.project_root / "utils.py").unlink()
        result = _handle_diff({}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert "utils.py" in result.data["deleted_files"]
        # Should also list deleted nodes
        deleted_ids = [n["id"] for n in result.data["deleted_nodes"]]
        assert "utils.welcome" in deleted_ids

    def test_detects_modified_file(self, project_with_tests: LensContext) -> None:
        app_py = project_with_tests.project_root / "app.py"
        app_py.write_text(
            "def greet(name):\n"
            '    return f"Hi, {name}"\n'
        )
        result = _handle_diff({}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert "app.py" in result.data["modified_files"]


class TestBatch:
    def test_batch_update_two_nodes(self, project: LensContext) -> None:
        result = _handle_batch(
            {
                "updates": [
                    {
                        "node_id": "app.greet",
                        "new_source": (
                            "def greet(name):\n"
                            '    return f"Hi, {name}"\n'
                        ),
                    },
                    {
                        "node_id": "app.farewell",
                        "new_source": (
                            "def farewell(name):\n"
                            '    return f"Bye, {name}"\n'
                        ),
                    },
                ],
            },
            project,
        )
        assert result.success
        assert result.data is not None
        assert result.data["count"] == 2
        # Verify both changes applied
        content = (project.project_root / "app.py").read_text()
        assert "Hi, {name}" in content
        assert "Bye, {name}" in content

    def test_batch_rolls_back_on_invalid(self, project: LensContext) -> None:
        original = (project.project_root / "app.py").read_text()
        result = _handle_batch(
            {
                "updates": [
                    {
                        "node_id": "app.greet",
                        "new_source": "def greet(name):\n    return 'ok'\n",
                    },
                    {
                        "node_id": "app.farewell",
                        "new_source": "def farewell(name:\n",  # syntax error
                    },
                ],
            },
            project,
        )
        assert not result.success
        # File should be unchanged
        assert (project.project_root / "app.py").read_text() == original

    def test_batch_nonexistent_node(self, project: LensContext) -> None:
        result = _handle_batch(
            {
                "updates": [
                    {"node_id": "app.nope", "new_source": "def nope():\n    pass\n"},
                ],
            },
            project,
        )
        assert not result.success

    def test_batch_empty(self, project: LensContext) -> None:
        result = _handle_batch({"updates": []}, project)
        assert not result.success


class TestHealth:
    def test_returns_stats(self, project_with_tests: LensContext) -> None:
        result = _handle_health({}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert result.data["total_nodes"] > 0
        assert result.data["total_edges"] > 0
        assert "nodes_by_type" in result.data
        assert "edges_by_type" in result.data
        assert "edges_by_confidence" in result.data
        assert "confidence_pct" in result.data
        assert "docstring_pct" in result.data
        assert "circular_imports" in result.data

    def test_node_types_present(self, project_with_tests: LensContext) -> None:
        result = _handle_health({}, project_with_tests)
        assert result.data is not None
        types = result.data["nodes_by_type"]
        assert "function" in types
        assert types["function"] > 0


class TestExplain:
    def test_returns_explanation(self, project_with_tests: LensContext) -> None:
        result = _handle_explain({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert result.data["node_id"] == "app.greet"
        assert "explanation" in result.data
        assert "source_code" in result.data

    def test_returns_analysis(self, project_with_tests: LensContext) -> None:
        result = _handle_explain({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        analysis = result.data["analysis"]
        assert "purpose" in analysis
        assert "inputs" in analysis
        assert "outputs" in analysis
        assert "side_effects" in analysis
        assert "complexity" in analysis

    def test_returns_context(self, project_with_tests: LensContext) -> None:
        result = _handle_explain({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        context = result.data["context"]
        assert "callers" in context
        assert "callees" in context
        assert "caller_count" in context
        # greet is called by welcome
        assert context["caller_count"] >= 1

    def test_includes_usage_examples(self, project_with_tests: LensContext) -> None:
        result = _handle_explain(
            {"node_id": "app.greet", "include_examples": True},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        # Should have usage examples since greet is called
        assert "usage_examples" in result.data

    def test_excludes_usage_examples(self, project_with_tests: LensContext) -> None:
        result = _handle_explain(
            {"node_id": "app.greet", "include_examples": False},
            project_with_tests,
        )
        assert result.success
        assert result.data is not None
        # Examples should be empty when disabled
        assert result.data["usage_examples"] == []

    def test_nonexistent_node(self, project_with_tests: LensContext) -> None:
        result = _handle_explain({"node_id": "app.nope"}, project_with_tests)
        assert not result.success

    def test_no_llm_hint(self, project_with_tests: LensContext) -> None:
        result = _handle_explain({"node_id": "app.greet"}, project_with_tests)
        assert result.success
        assert result.data is not None
        assert "llm_hint" not in result.data
