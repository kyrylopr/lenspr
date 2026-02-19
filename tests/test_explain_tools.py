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


# ---------------------------------------------------------------------------
# Fixture for limit-filtering and class method tests
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_methods(tmp_path: Path) -> LensContext:
    """Project where class methods call each other via self and also call
    many stdlib functions, exposing the limit-filtering bug."""
    (tmp_path / "processor.py").write_text(
        "import os\n"
        "import json\n"
        "import hashlib\n"
        "\n"
        "class DataProcessor:\n"
        '    """Processes data files."""\n'
        "\n"
        "    def __init__(self, path: str):\n"
        "        self.path = path\n"
        "        self.data = None\n"
        "\n"
        "    def load(self) -> dict:\n"
        '        """Load data from disk."""\n'
        "        raw = os.path.join(self.path, 'data.json')\n"
        "        text = json.dumps({'key': 'value'})\n"
        "        h = hashlib.md5(text.encode()).hexdigest()\n"
        "        self.data = self.parse(text)\n"
        "        return self.data\n"
        "\n"
        "    def parse(self, text: str) -> dict:\n"
        '        """Parse text into a dict."""\n'
        "        return json.loads(text)\n"
        "\n"
        "    def transform(self) -> dict:\n"
        '        """Transform loaded data."""\n'
        "        if self.data is None:\n"
        "            self.load()\n"
        "        return self.validate(self.data)\n"
        "\n"
        "    def validate(self, data: dict) -> dict:\n"
        '        """Validate data structure."""\n'
        "        return data\n"
    )

    (tmp_path / "runner.py").write_text(
        "from processor import DataProcessor\n"
        "\n"
        "def run_pipeline(path: str) -> dict:\n"
        '    """Run the full processing pipeline."""\n'
        "    proc = DataProcessor(path)\n"
        "    proc.load()\n"
        "    return proc.transform()\n"
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)
    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# Regression tests for limit-filtering bug and class method explain
# ---------------------------------------------------------------------------


class TestExplainLimitFix:
    """Regression tests for the callers/callees limit filtering bug.

    Old code applied [:limit] before filtering external nodes, so if
    the first N successors were all external, 0 internal callees appeared.
    """

    def test_method_callees_include_self_calls(
        self, project_with_methods: LensContext
    ) -> None:
        """Method calling self.parse() → parse appears in callees."""
        result = handle_explain(
            {"node_id": "processor.DataProcessor.load"},
            project_with_methods,
        )

        assert result.success
        callees = result.data["context"]["callees"]
        callee_names = [c["name"] for c in callees]
        assert "parse" in callee_names, (
            f"Expected 'parse' in callees but got: {callee_names}"
        )

    def test_method_callees_despite_many_externals(
        self, project_with_methods: LensContext
    ) -> None:
        """Method with many external calls (os, json, hashlib) still shows
        internal callees (self.parse)."""
        result = handle_explain(
            {"node_id": "processor.DataProcessor.load"},
            project_with_methods,
        )

        assert result.success
        callee_count = result.data["context"]["callee_count"]
        assert callee_count > 0, "Expected at least 1 internal callee"

    def test_method_callers_from_other_methods(
        self, project_with_methods: LensContext
    ) -> None:
        """Method called via self from another method → caller appears."""
        result = handle_explain(
            {"node_id": "processor.DataProcessor.validate"},
            project_with_methods,
        )

        assert result.success
        callers = result.data["context"]["callers"]
        caller_names = [c["name"] for c in callers]
        assert "transform" in caller_names, (
            f"Expected 'transform' in callers but got: {caller_names}"
        )

    def test_method_callers_from_external_function(
        self, project_with_methods: LensContext
    ) -> None:
        """Method called from a function in another file → caller appears."""
        result = handle_explain(
            {"node_id": "processor.DataProcessor.load"},
            project_with_methods,
        )

        assert result.success
        callers = result.data["context"]["callers"]
        caller_names = [c["name"] for c in callers]
        assert "run_pipeline" in caller_names, (
            f"Expected 'run_pipeline' in callers but got: {caller_names}"
        )

    def test_function_calling_method_shows_callees(
        self, project_with_methods: LensContext
    ) -> None:
        """Function calling instance.method() → method appears in callees."""
        result = handle_explain(
            {"node_id": "runner.run_pipeline"},
            project_with_methods,
        )

        assert result.success
        callees = result.data["context"]["callees"]
        callee_names = [c["name"] for c in callees]
        # Should show at least DataProcessor, load, transform
        assert len(callees) >= 2, (
            f"Expected >= 2 callees but got {len(callees)}: {callee_names}"
        )
