"""Tests for lenspr/tools/analysis.py — impact, validate, health, dead code, find usages, diff."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.analysis import (
    handle_check_impact,
    handle_dead_code,
    handle_diff,
    handle_find_usages,
    handle_health,
    handle_validate_change,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Project with cross-module calls and a test file."""
    (tmp_path / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
    )

    (tmp_path / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name: str) -> User:\n"
        '    """Create a new user."""\n'
        "    return User(name)\n"
        "\n"
        "def unused_helper():\n"
        '    """This function is never called."""\n'
        "    pass\n"
    )

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
# handle_check_impact
# ---------------------------------------------------------------------------


class TestCheckImpact:
    def test_returns_severity(self, project: LensContext) -> None:
        """Impact analysis includes a severity level."""
        result = handle_check_impact(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert result.data["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")

    def test_low_impact_function(self, project: LensContext) -> None:
        """Function with few callers → LOW severity."""
        result = handle_check_impact(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        # create_user has at most 1 caller (test_create_user)
        assert result.data["severity"] in ("LOW", "MEDIUM")

    def test_nonexistent_node_returns_zero_impact(
        self, project: LensContext
    ) -> None:
        """Non-existent node → total_affected=0 (graph.get_impact_zone handles gracefully)."""
        result = handle_check_impact(
            {"node_id": "does.not.exist"}, project
        )

        assert result.success
        assert result.data["total_affected"] == 0

    def test_has_tests_flag(self, project: LensContext) -> None:
        """Function called by test → has_tests=True."""
        result = handle_check_impact(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert result.data["has_tests"] is True

    def test_no_tests_flag(self, project: LensContext) -> None:
        """Function with no test callers → has_tests=False."""
        result = handle_check_impact(
            {"node_id": "service.unused_helper"}, project
        )

        assert result.success
        assert result.data["has_tests"] is False

    def test_includes_direct_callers(self, project: LensContext) -> None:
        """Response includes direct_callers list."""
        result = handle_check_impact(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert "direct_callers" in result.data
        assert isinstance(result.data["direct_callers"], list)


# ---------------------------------------------------------------------------
# handle_validate_change
# ---------------------------------------------------------------------------


class TestValidateChange:
    def test_valid_code_accepted(self, project: LensContext) -> None:
        """Syntactically valid replacement → would_apply=True."""
        new_source = (
            "def create_user(name: str) -> User:\n"
            '    """Create a new user with validation."""\n'
            "    if not name:\n"
            '        raise ValueError("name required")\n'
            "    return User(name)\n"
        )
        result = handle_validate_change(
            {"node_id": "service.create_user", "new_source": new_source},
            project,
        )

        assert result.success
        assert result.data["would_apply"] is True
        assert result.data["validation"]["valid"] is True

    def test_syntax_error_rejected(self, project: LensContext) -> None:
        """Code with syntax error → valid=False."""
        result = handle_validate_change(
            {
                "node_id": "service.create_user",
                "new_source": "def create_user(name:\n    return broken",
            },
            project,
        )

        assert result.success  # The handler itself succeeds
        assert result.data["would_apply"] is False
        assert result.data["validation"]["valid"] is False
        assert len(result.data["validation"]["errors"]) > 0

    def test_nonexistent_node_returns_error(
        self, project: LensContext
    ) -> None:
        """Non-existent node → success=False."""
        result = handle_validate_change(
            {"node_id": "does.not.exist", "new_source": "def f(): pass"},
            project,
        )

        assert not result.success
        assert "not found" in result.error.lower()

    def test_includes_impact_data(self, project: LensContext) -> None:
        """Response includes impact analysis for the node."""
        result = handle_validate_change(
            {
                "node_id": "service.create_user",
                "new_source": "def create_user(name):\n    return User(name)\n",
            },
            project,
        )

        assert result.success
        assert "impact" in result.data
        assert "total_affected" in result.data["impact"]


# ---------------------------------------------------------------------------
# handle_health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_all_metrics(self, project: LensContext) -> None:
        """Health report includes all expected top-level keys."""
        result = handle_health({}, project)

        assert result.success
        data = result.data
        assert "total_nodes" in data
        assert "total_edges" in data
        assert "confidence_pct" in data
        assert "nodes_by_type" in data
        assert "edges_by_confidence" in data
        assert "circular_imports" in data

    def test_node_count_positive(self, project: LensContext) -> None:
        """A non-empty project → positive node count."""
        result = handle_health({}, project)

        assert result.success
        assert result.data["total_nodes"] > 0

    def test_confidence_in_range(self, project: LensContext) -> None:
        """Confidence percentage is between 0 and 100."""
        result = handle_health({}, project)

        assert result.success
        pct = result.data["confidence_pct"]
        assert 0 <= pct <= 100

    def test_no_circular_imports(self, project: LensContext) -> None:
        """Our test project has no circular imports."""
        result = handle_health({}, project)

        assert result.success
        assert result.data["circular_imports"] == []

    def test_internal_edges_tracked(self, project: LensContext) -> None:
        """Internal edge stats are present and consistent."""
        result = handle_health({}, project)

        assert result.success
        internal = result.data["internal_edges"]
        assert internal["total"] >= internal["resolved"]
        assert internal["resolved"] >= 0

    def test_docstring_pct_in_range(self, project: LensContext) -> None:
        """Docstring percentage is between 0 and 100."""
        result = handle_health({}, project)

        assert result.success
        assert 0 <= result.data["docstring_pct"] <= 100


# ---------------------------------------------------------------------------
# handle_dead_code
# ---------------------------------------------------------------------------


class TestDeadCode:
    def test_finds_unused_function(self, project: LensContext) -> None:
        """unused_helper is never called → should appear in dead code."""
        result = handle_dead_code({}, project)

        assert result.success
        dead_ids = result.data["dead_code"]
        assert any("unused_helper" in nid for nid in dead_ids), (
            f"unused_helper should be in dead code, got: {dead_ids}"
        )

    def test_excludes_test_functions(self, project: LensContext) -> None:
        """Test functions are entry points → never flagged as dead."""
        result = handle_dead_code({}, project)

        assert result.success
        dead_ids = result.data["dead_code"]
        for nid in dead_ids:
            assert not nid.startswith("test_"), (
                f"Test function {nid} should not be in dead code"
            )

    def test_groups_by_file(self, project: LensContext) -> None:
        """Dead code is grouped by file path."""
        result = handle_dead_code({}, project)

        assert result.success
        assert "by_file" in result.data
        assert isinstance(result.data["by_file"], dict)

    def test_custom_entry_points(self, project: LensContext) -> None:
        """Custom entry_points override auto-detection."""
        result = handle_dead_code(
            {"entry_points": ["service.create_user"]}, project
        )

        assert result.success
        # With only create_user as entry, unused_helper is definitely dead
        dead_ids = result.data["dead_code"]
        assert any("unused_helper" in nid for nid in dead_ids)

    def test_warning_present_when_dead_code_found(
        self, project: LensContext
    ) -> None:
        """When dead code exists, a static analysis warning is returned."""
        result = handle_dead_code({}, project)

        assert result.success
        if result.data["count"] > 0:
            assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# handle_find_usages
# ---------------------------------------------------------------------------


class TestFindUsages:
    def test_finds_callers(self, project: LensContext) -> None:
        """Function with known callers → callers list is non-empty."""
        result = handle_find_usages(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        # test_create_user calls create_user
        callers = result.data.get("callers", [])
        assert len(callers) > 0 or result.data.get("total_usages", 0) > 0

    def test_nonexistent_node_returns_error(
        self, project: LensContext
    ) -> None:
        """Non-existent node → error response."""
        result = handle_find_usages(
            {"node_id": "does.not.exist"}, project
        )

        assert not result.success
        assert result.error is not None

    def test_missing_params_returns_error(
        self, project: LensContext
    ) -> None:
        """No node_id or node_ids → error response."""
        result = handle_find_usages({}, project)

        assert not result.success
        assert "required" in result.error.lower()

    def test_batch_mode(self, project: LensContext) -> None:
        """node_ids param → batch results for multiple nodes."""
        result = handle_find_usages(
            {"node_ids": ["service.create_user", "service.unused_helper"]},
            project,
        )

        assert result.success
        assert result.data["count"] == 2

    def test_batch_mode_not_found(self, project: LensContext) -> None:
        """Batch mode with nonexistent node → appears in not_found list."""
        result = handle_find_usages(
            {"node_ids": ["service.create_user", "does.not.exist"]},
            project,
        )

        assert result.success
        assert "does.not.exist" in result.data["not_found"]


# ---------------------------------------------------------------------------
# handle_diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_no_changes_after_sync(self, project: LensContext) -> None:
        """Freshly synced project → zero changes."""
        result = handle_diff({}, project)

        assert result.success
        assert result.data["total_changes"] == 0

    def test_detects_new_file(self, project: LensContext) -> None:
        """Adding a new .py file → appears in added_files."""
        (project.project_root / "new_module.py").write_text(
            "def new_func():\n    pass\n"
        )

        result = handle_diff({}, project)

        assert result.success
        assert "new_module.py" in result.data["added_files"]

    def test_detects_modified_file(self, project: LensContext) -> None:
        """Modifying a tracked file → appears in modified_files."""
        time.sleep(0.05)
        (project.project_root / "service.py").write_text(
            "def create_user(name):\n    return name.upper()\n"
        )

        result = handle_diff({}, project)

        assert result.success
        assert "service.py" in result.data["modified_files"]

    def test_detects_deleted_file(self, project: LensContext) -> None:
        """Deleting a tracked file → appears in deleted_files."""
        (project.project_root / "service.py").unlink()

        result = handle_diff({}, project)

        assert result.success
        assert "service.py" in result.data["deleted_files"]
        # deleted_nodes should include nodes from the deleted file
        deleted_node_ids = [n["id"] for n in result.data["deleted_nodes"]]
        assert any("service" in nid for nid in deleted_node_ids)
