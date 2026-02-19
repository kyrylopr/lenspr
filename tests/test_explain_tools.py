"""Tests for lenspr/tools/explain.py — handle_explain."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.explain import handle_explain


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Project with a class and functions that call each other."""
    (tmp_path / "models.py").write_text(
        "class User:\n"
        '    """A user in the system."""\n'
        "\n"
        "    def __init__(self, name: str, email: str):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def greet(self) -> str:\n"
        '        """Return a greeting."""\n'
        '        return f"Hello, {self.name}"\n'
    )

    (tmp_path / "service.py").write_text(
        "from models import User\n"
        "\n"
        "def create_user(name: str, email: str) -> User:\n"
        '    """Create and return a new User instance."""\n'
        "    return User(name, email)\n"
        "\n"
        "def get_greeting(name: str, email: str) -> str:\n"
        '    """Create a user and return their greeting."""\n'
        "    user = create_user(name, email)\n"
        "    return user.greet()\n"
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)
    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExplain:
    def test_returns_explanation_for_function(
        self, project: LensContext
    ) -> None:
        """Explaining a function → explanation text is non-empty."""
        result = handle_explain(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert result.data["explanation"]
        assert len(result.data["explanation"]) > 0

    def test_includes_source_code(self, project: LensContext) -> None:
        """Response includes the function's source code."""
        result = handle_explain(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert "def create_user" in result.data["source_code"]

    def test_includes_analysis(self, project: LensContext) -> None:
        """Response includes structured analysis with purpose, inputs, outputs."""
        result = handle_explain(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        analysis = result.data["analysis"]
        assert "purpose" in analysis
        assert "inputs" in analysis
        assert "outputs" in analysis
        assert "side_effects" in analysis

    def test_includes_context(self, project: LensContext) -> None:
        """Response includes caller/callee context."""
        result = handle_explain(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        ctx = result.data["context"]
        assert "callers" in ctx
        assert "callees" in ctx
        assert "caller_count" in ctx

    def test_class_explanation(self, project: LensContext) -> None:
        """Explaining a class → includes methods info."""
        result = handle_explain({"node_id": "models.User"}, project)

        assert result.success
        assert result.data["type"] == "class"
        assert result.data["explanation"]

    def test_nonexistent_node_returns_error(
        self, project: LensContext
    ) -> None:
        """Non-existent node → error response."""
        result = handle_explain(
            {"node_id": "does.not.exist"}, project
        )

        assert not result.success
        assert "not found" in result.error.lower()

    def test_usage_examples_included(self, project: LensContext) -> None:
        """include_examples=True → usage_examples list in response."""
        result = handle_explain(
            {"node_id": "service.create_user", "include_examples": True},
            project,
        )

        assert result.success
        assert "usage_examples" in result.data

    def test_usage_examples_excluded(self, project: LensContext) -> None:
        """include_examples=False → usage_examples is empty."""
        result = handle_explain(
            {"node_id": "service.create_user", "include_examples": False},
            project,
        )

        assert result.success
        assert result.data["usage_examples"] == []

    def test_includes_docstring(self, project: LensContext) -> None:
        """Function with docstring → docstring present in response."""
        result = handle_explain(
            {"node_id": "service.create_user"}, project
        )

        assert result.success
        assert result.data["docstring"] is not None
        assert "Create and return" in result.data["docstring"]
