"""Tests for lenspr/tools/arch.py — class_metrics, project_metrics, largest_classes, components."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.arch import (
    handle_class_metrics,
    handle_compare_classes,
    handle_components,
    handle_largest_classes,
    handle_project_metrics,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Project with classes in two directories for component analysis."""
    # Package A
    pkg_a = tmp_path / "pkg_a"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("")
    (pkg_a / "models.py").write_text(
        "class Animal:\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
        "\n"
        "    def speak(self) -> str:\n"
        '        return "..."\n'
        "\n"
        "    def eat(self, food: str) -> None:\n"
        "        pass\n"
    )

    # Package B
    pkg_b = tmp_path / "pkg_b"
    pkg_b.mkdir()
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "utils.py").write_text(
        "class Formatter:\n"
        "    def format_name(self, name: str) -> str:\n"
        "        return name.title()\n"
    )

    # Root module
    (tmp_path / "main.py").write_text(
        "from pkg_a.models import Animal\n"
        "from pkg_b.utils import Formatter\n"
        "\n"
        "def run():\n"
        "    a = Animal('cat')\n"
        "    f = Formatter()\n"
        "    print(f.format_name(a.name))\n"
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)
    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# handle_class_metrics
# ---------------------------------------------------------------------------


class TestClassMetrics:
    def test_returns_method_count(self, project: LensContext) -> None:
        """Class with 3 methods → method_count=3."""
        result = handle_class_metrics(
            {"node_id": "pkg_a.models.Animal"}, project
        )

        assert result.success
        assert result.data["method_count"] == 3

    def test_nonexistent_node_returns_error(
        self, project: LensContext
    ) -> None:
        """Non-existent node → error."""
        result = handle_class_metrics(
            {"node_id": "does.not.exist"}, project
        )

        assert not result.success

    def test_function_node_returns_error(self, project: LensContext) -> None:
        """Passing a function node → error (not a class)."""
        result = handle_class_metrics({"node_id": "main.run"}, project)

        assert not result.success
        assert "not a class" in result.error.lower()

    def test_missing_node_id_returns_error(
        self, project: LensContext
    ) -> None:
        """No node_id → error."""
        result = handle_class_metrics({}, project)

        assert not result.success


# ---------------------------------------------------------------------------
# handle_project_metrics
# ---------------------------------------------------------------------------


class TestProjectMetrics:
    def test_returns_class_stats(self, project: LensContext) -> None:
        """Project metrics include class statistics."""
        result = handle_project_metrics({}, project)

        assert result.success
        assert "total_classes" in result.data
        assert result.data["total_classes"] >= 2  # Animal, Formatter

    def test_includes_percentiles(self, project: LensContext) -> None:
        """Project metrics include percentile data."""
        result = handle_project_metrics({}, project)

        assert result.success
        # Should have median and/or percentile info
        assert "median_methods" in result.data or "avg_methods" in result.data


# ---------------------------------------------------------------------------
# handle_largest_classes
# ---------------------------------------------------------------------------


class TestLargestClasses:
    def test_returns_sorted_by_method_count(
        self, project: LensContext
    ) -> None:
        """Largest classes are sorted by method count (descending)."""
        result = handle_largest_classes({}, project)

        assert result.success
        classes = result.data["classes"]
        assert len(classes) >= 2
        # Should be sorted descending by method count
        for i in range(len(classes) - 1):
            assert classes[i]["method_count"] >= classes[i + 1]["method_count"]

    def test_animal_has_more_methods_than_formatter(
        self, project: LensContext
    ) -> None:
        """Animal (3 methods) should rank higher than Formatter (1 method)."""
        result = handle_largest_classes({}, project)

        assert result.success
        names = [c["name"] for c in result.data["classes"]]
        if "Animal" in names and "Formatter" in names:
            animal_idx = names.index("Animal")
            formatter_idx = names.index("Formatter")
            assert animal_idx < formatter_idx

    def test_limit_param(self, project: LensContext) -> None:
        """limit=1 → only the largest class returned."""
        result = handle_largest_classes({"limit": 1}, project)

        assert result.success
        assert len(result.data["classes"]) <= 1


# ---------------------------------------------------------------------------
# handle_compare_classes
# ---------------------------------------------------------------------------


class TestCompareClasses:
    def test_compares_two_classes(self, project: LensContext) -> None:
        """Comparing two classes → both appear in result."""
        result = handle_compare_classes(
            {"node_ids": ["pkg_a.models.Animal", "pkg_b.utils.Formatter"]},
            project,
        )

        assert result.success
        assert len(result.data["comparisons"]) == 2


# ---------------------------------------------------------------------------
# handle_components
# ---------------------------------------------------------------------------


class TestComponents:
    def test_detects_directory_components(self, project: LensContext) -> None:
        """Project with two packages → at least 2 components detected."""
        result = handle_components({}, project)

        assert result.success
        assert result.data["count"] >= 2

    def test_cohesion_in_range(self, project: LensContext) -> None:
        """Each component's cohesion is between 0 and 1."""
        result = handle_components({}, project)

        assert result.success
        for comp in result.data["components"]:
            assert 0 <= comp["cohesion"] <= 1

    def test_path_filter(self, project: LensContext) -> None:
        """path filter → only components under that path."""
        result = handle_components({"path": "pkg_a"}, project)

        assert result.success
        for comp in result.data["components"]:
            assert "pkg_a" in comp["path"]

    def test_min_cohesion_filter(self, project: LensContext) -> None:
        """min_cohesion filter excludes low-cohesion components."""
        # Get all components first
        all_result = handle_components({}, project)
        assert all_result.success

        # Filter with very high cohesion → fewer or zero results
        high_result = handle_components({"min_cohesion": 0.99}, project)
        assert high_result.success
        assert high_result.data["count"] <= all_result.data["count"]
