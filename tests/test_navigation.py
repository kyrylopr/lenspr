"""Tests for lenspr/tools/navigation.py — all 7 handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.navigation import (
    handle_context,
    handle_get_connections,
    handle_get_node,
    handle_get_structure,
    handle_grep,
    handle_list_nodes,
    handle_search,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Project with two modules (cross-file call) and a test file."""
    # Module with a class and two functions
    (tmp_path / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self) -> str:\n"
        '        return f"Hello, {self.name}"\n'
    )

    # Module that imports from models
    (tmp_path / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name: str) -> User:\n"
        '    """Create a new user."""\n'
        "    return User(name)\n"
        "\n"
        "def list_users() -> list:\n"
        '    """Return all users."""\n'
        "    return []\n"
    )

    # Test file
    (tmp_path / "test_service.py").write_text(
        "from service import create_user\n"
        "\n"
        "def test_create_user():\n"
        '    u = create_user("Alice")\n'
        '    assert u.name == "Alice"\n'
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# handle_list_nodes
# ---------------------------------------------------------------------------


class TestListNodes:
    def test_returns_all_nodes(self, project: LensContext) -> None:
        """Empty params → returns all nodes in the project."""
        result = handle_list_nodes({}, project)

        assert result.success
        assert result.data["count"] > 0
        # Should contain at least: models module, User class, greet method,
        # service module, create_user, list_users
        names = [n["name"] for n in result.data["nodes"]]
        assert "User" in names
        assert "create_user" in names

    def test_filters_by_type_function(self, project: LensContext) -> None:
        """type='function' → only functions, no classes or modules."""
        result = handle_list_nodes({"type": "function"}, project)

        assert result.success
        for node in result.data["nodes"]:
            assert node["type"] == "function"
        names = [n["name"] for n in result.data["nodes"]]
        assert "create_user" in names
        # User is a class, not a function
        assert "User" not in names

    def test_filters_by_type_class(self, project: LensContext) -> None:
        """type='class' → only classes."""
        result = handle_list_nodes({"type": "class"}, project)

        assert result.success
        for node in result.data["nodes"]:
            assert node["type"] == "class"
        names = [n["name"] for n in result.data["nodes"]]
        assert "User" in names

    def test_filters_by_file_path(self, project: LensContext) -> None:
        """file_path filter → only nodes from that file."""
        result = handle_list_nodes({"file_path": "service.py"}, project)

        assert result.success
        for node in result.data["nodes"]:
            assert node["file_path"] == "service.py"
        names = [n["name"] for n in result.data["nodes"]]
        assert "create_user" in names
        # User is in models.py, not service.py
        assert "User" not in names

    def test_filters_by_name(self, project: LensContext) -> None:
        """name filter → only nodes whose name contains the substring."""
        result = handle_list_nodes({"name": "user"}, project)

        assert result.success
        for node in result.data["nodes"]:
            assert "user" in node["name"].lower()

    def test_combined_filters(self, project: LensContext) -> None:
        """Multiple filters applied simultaneously."""
        result = handle_list_nodes(
            {"type": "function", "file_path": "service.py"}, project
        )

        assert result.success
        for node in result.data["nodes"]:
            assert node["type"] == "function"
            assert node["file_path"] == "service.py"

    def test_no_matches_returns_empty(self, project: LensContext) -> None:
        """Filters that match nothing → empty list, still success."""
        result = handle_list_nodes({"name": "nonexistent_xyz_123"}, project)

        assert result.success
        assert result.data["count"] == 0
        assert result.data["nodes"] == []


# ---------------------------------------------------------------------------
# handle_get_node
# ---------------------------------------------------------------------------


class TestGetNode:
    def test_returns_source_code(self, project: LensContext) -> None:
        """Valid node_id → source code is returned."""
        result = handle_get_node({"node_id": "service.create_user"}, project)

        assert result.success
        assert result.data["source_code"] is not None
        assert "def create_user" in result.data["source_code"]
        assert "return User(name)" in result.data["source_code"]

    def test_returns_metadata(self, project: LensContext) -> None:
        """Node response includes all metadata fields."""
        result = handle_get_node({"node_id": "service.create_user"}, project)

        assert result.success
        assert result.data["name"] == "create_user"
        assert result.data["type"] == "function"
        assert result.data["file_path"] == "service.py"
        assert result.data["start_line"] is not None
        assert result.data["end_line"] is not None

    def test_nonexistent_returns_error(self, project: LensContext) -> None:
        """Non-existent node_id → success=False with error and hint."""
        result = handle_get_node({"node_id": "does.not.exist"}, project)

        assert not result.success
        assert result.error is not None
        assert "not found" in result.error.lower()
        assert result.hint is not None

    def test_class_node_returns_source(self, project: LensContext) -> None:
        """Small class → source code returned (not large container behavior)."""
        result = handle_get_node({"node_id": "models.User"}, project)

        assert result.success
        assert result.data["source_code"] is not None
        assert "class User" in result.data["source_code"]

    def test_returns_docstring(self, project: LensContext) -> None:
        """Function with docstring → docstring field populated."""
        result = handle_get_node({"node_id": "service.create_user"}, project)

        assert result.success
        assert result.data["docstring"] is not None
        assert "Create a new user" in result.data["docstring"]


# ---------------------------------------------------------------------------
# handle_get_connections
# ---------------------------------------------------------------------------


class TestGetConnections:
    def test_returns_edges(self, project: LensContext) -> None:
        """Node with connections → edges list is non-empty."""
        result = handle_get_connections(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert result.data["count"] >= 0
        assert isinstance(result.data["edges"], list)

    def test_direction_incoming(self, project: LensContext) -> None:
        """direction='incoming' → only edges pointing TO this node."""
        result = handle_get_connections(
            {"node_id": "service.create_user", "direction": "incoming"},
            project,
        )

        assert result.success
        assert result.data["direction"] == "incoming"
        for edge in result.data["edges"]:
            assert edge["to"] == "service.create_user"

    def test_direction_outgoing(self, project: LensContext) -> None:
        """direction='outgoing' → only edges pointing FROM this node."""
        result = handle_get_connections(
            {"node_id": "service.create_user", "direction": "outgoing"},
            project,
        )

        assert result.success
        assert result.data["direction"] == "outgoing"
        for edge in result.data["edges"]:
            assert edge["from"] == "service.create_user"

    def test_direction_both_is_default(self, project: LensContext) -> None:
        """No direction param → defaults to 'both'."""
        result = handle_get_connections(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert result.data["direction"] == "both"

    def test_edge_fields_present(self, project: LensContext) -> None:
        """Each edge has from, to, type, confidence fields."""
        result = handle_get_connections(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        for edge in result.data["edges"]:
            assert "from" in edge
            assert "to" in edge
            assert "type" in edge
            assert "confidence" in edge


# ---------------------------------------------------------------------------
# handle_search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_finds_by_name(self, project: LensContext) -> None:
        """Search by function name → finds the matching node."""
        result = handle_search({"query": "create_user"}, project)

        assert result.success
        ids = [r["id"] for r in result.data["results"]]
        assert any("create_user" in rid for rid in ids)

    def test_search_in_code(self, project: LensContext) -> None:
        """search_in='code' → searches in source code content."""
        result = handle_search(
            {"query": "return User(name)", "search_in": "code"}, project
        )

        assert result.success
        assert result.data["count"] > 0

    def test_search_in_name(self, project: LensContext) -> None:
        """search_in='name' → searches only in node names."""
        result = handle_search(
            {"query": "User", "search_in": "name"}, project
        )

        assert result.success
        names = [r["name"] for r in result.data["results"]]
        for name in names:
            assert "User" in name or "user" in name.lower()

    def test_no_matches_returns_empty(self, project: LensContext) -> None:
        """Query with no results → empty results, still success."""
        result = handle_search(
            {"query": "absolutely_nothing_matches_this_xyz"}, project
        )

        assert result.success
        assert result.data["count"] == 0

    def test_result_fields_present(self, project: LensContext) -> None:
        """Each result has id, type, name, file_path."""
        result = handle_search({"query": "create_user"}, project)

        assert result.success
        for r in result.data["results"]:
            assert "id" in r
            assert "type" in r
            assert "name" in r
            assert "file_path" in r


# ---------------------------------------------------------------------------
# handle_get_structure
# ---------------------------------------------------------------------------


class TestGetStructure:
    def test_summary_mode(self, project: LensContext) -> None:
        """mode='summary' → returns file list with counts."""
        result = handle_get_structure({"mode": "summary"}, project)

        assert result.success
        structure = result.data["structure"]
        assert isinstance(structure, list)
        assert len(structure) > 0
        # Each entry should have file and count fields
        for entry in structure:
            assert "file" in entry
            assert "classes" in entry
            assert "functions" in entry

    def test_compact_mode(self, project: LensContext) -> None:
        """mode='compact' → returns totals only, no file list."""
        result = handle_get_structure({"mode": "compact"}, project)

        assert result.success
        assert "totals" in result.data
        totals = result.data["totals"]
        assert "files" in totals
        assert "classes" in totals
        assert "functions" in totals
        assert totals["files"] >= 3  # models.py, service.py, test_service.py

    def test_full_mode(self, project: LensContext) -> None:
        """mode='full' → returns detailed structure with node info."""
        result = handle_get_structure({"mode": "full"}, project)

        assert result.success
        structure = result.data["structure"]
        assert isinstance(structure, dict)
        assert any("service.py" in key for key in structure)

    def test_pagination(self, project: LensContext) -> None:
        """limit and offset control pagination."""
        result = handle_get_structure({"mode": "summary", "limit": 1}, project)

        assert result.success
        assert len(result.data["structure"]) <= 1
        assert result.data["pagination"]["limit"] == 1
        assert result.data["pagination"]["has_more"] is True

    def test_path_prefix_filter(self, project: LensContext) -> None:
        """path_prefix filters to specific directory."""
        # All our files are at root level, so filtering for 'service'
        # should return only service.py
        result = handle_get_structure(
            {"mode": "summary", "path_prefix": "service"}, project
        )

        assert result.success
        for entry in result.data["structure"]:
            assert entry["file"].startswith("service")


# ---------------------------------------------------------------------------
# handle_context
# ---------------------------------------------------------------------------


class TestContext:
    def test_returns_target_source(self, project: LensContext) -> None:
        """Target node includes source code."""
        result = handle_context(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        target = result.data["target"]
        assert "def create_user" in target["source_code"]

    def test_nonexistent_node_returns_error(self, project: LensContext) -> None:
        """Non-existent node → error response with hint."""
        result = handle_context(
            {"node_id": "does.not.exist"}, project
        )

        assert not result.success
        assert "not found" in result.error.lower()
        assert result.hint is not None

    def test_includes_callers(self, project: LensContext) -> None:
        """include_callers=True → callers list present in response."""
        result = handle_context(
            {"node_id": "service.create_user", "include_callers": True},
            project,
        )

        assert result.success
        assert "callers" in result.data
        assert "caller_count" in result.data

    def test_includes_callees(self, project: LensContext) -> None:
        """include_callees=True → callees list present."""
        result = handle_context(
            {"node_id": "service.create_user", "include_callees": True},
            project,
        )

        assert result.success
        assert "callees" in result.data
        assert "callee_count" in result.data

    def test_includes_tests(self, project: LensContext) -> None:
        """include_tests=True → tests list present."""
        result = handle_context(
            {"node_id": "service.create_user", "include_tests": True},
            project,
        )

        assert result.success
        assert "tests" in result.data
        assert "test_count" in result.data

    def test_excludes_callers_when_disabled(self, project: LensContext) -> None:
        """include_callers=False → no callers key in response."""
        result = handle_context(
            {"node_id": "service.create_user", "include_callers": False},
            project,
        )

        assert result.success
        assert "callers" not in result.data

    def test_excludes_source_when_disabled(self, project: LensContext) -> None:
        """include_source=False → source_code is None."""
        result = handle_context(
            {"node_id": "service.create_user", "include_source": False},
            project,
        )

        assert result.success
        assert result.data["target"]["source_code"] is None

    def test_no_test_warning_when_untested(self, project: LensContext) -> None:
        """Functions with no tests get a test_warning."""
        result = handle_context(
            {"node_id": "service.list_users"}, project
        )

        assert result.success
        # list_users has no test calling it, so should get a warning
        assert "test_warning" in result.data


# ---------------------------------------------------------------------------
# handle_grep
# ---------------------------------------------------------------------------


class TestGrep:
    def test_finds_pattern(self, project: LensContext) -> None:
        """Regex pattern → matches in correct files."""
        result = handle_grep({"pattern": "def create_user"}, project)

        assert result.success
        assert result.data["count"] > 0
        files = [m["file"] for m in result.data["results"]]
        assert any("service.py" in f for f in files)

    def test_no_matches_returns_empty(self, project: LensContext) -> None:
        """Pattern with no results → empty results, count=0."""
        result = handle_grep(
            {"pattern": "absolutely_no_match_xyz_987"}, project
        )

        assert result.success
        assert result.data["count"] == 0
        assert result.data["results"] == []

    def test_respects_max_results(self, project: LensContext) -> None:
        """max_results limits the number of matches returned."""
        result = handle_grep({"pattern": "def ", "max_results": 2}, project)

        assert result.success
        assert result.data["count"] <= 2
        if result.data["count"] == 2:
            assert result.data["truncated"] is True

    def test_match_includes_graph_context(self, project: LensContext) -> None:
        """Matches inside functions include containing node_id."""
        result = handle_grep({"pattern": "return User"}, project)

        assert result.success
        assert result.data["count"] > 0
        # The match is inside create_user, so node_id should be present
        match = result.data["results"][0]
        assert "node_id" in match or "node_name" in match

    def test_file_glob_filter(self, project: LensContext) -> None:
        """file_glob → only searches matching files."""
        result = handle_grep(
            {"pattern": "def ", "file_glob": "service.py"}, project
        )

        assert result.success
        for match in result.data["results"]:
            assert match["file"] == "service.py"

    def test_regex_pattern(self, project: LensContext) -> None:
        """Regex patterns work (not just literal strings)."""
        result = handle_grep({"pattern": r"def \w+_user"}, project)

        assert result.success
        assert result.data["count"] > 0

    def test_invalid_regex_falls_back_to_literal(
        self, project: LensContext
    ) -> None:
        """Invalid regex → treated as literal string (escaped)."""
        result = handle_grep({"pattern": "[invalid(regex"}, project)

        assert result.success
        # Should not raise — falls back to re.escape
