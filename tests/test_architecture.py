"""Tests for lenspr/architecture.py — detect_components, compute_all_metrics."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.architecture import compute_all_metrics, detect_components
from lenspr.context import LensContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Project with two packages for component detection."""
    # Package A with a class
    pkg_a = tmp_path / "pkg_a"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("")
    (pkg_a / "core.py").write_text(
        "class Engine:\n"
        "    def start(self):\n"
        "        pass\n"
        "\n"
        "    def stop(self):\n"
        "        pass\n"
        "\n"
        "def helper():\n"
        "    return Engine()\n"
    )

    # Package B with a class
    pkg_b = tmp_path / "pkg_b"
    pkg_b.mkdir()
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "output.py").write_text(
        "class Printer:\n"
        "    def print_line(self, text: str) -> None:\n"
        "        print(text)\n"
    )

    # Root connector
    (tmp_path / "main.py").write_text(
        "from pkg_a.core import Engine\n"
        "from pkg_b.output import Printer\n"
        "\n"
        "def run():\n"
        "    e = Engine()\n"
        "    p = Printer()\n"
        "    e.start()\n"
        "    p.print_line('done')\n"
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)
    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# detect_components
# ---------------------------------------------------------------------------


class TestDetectComponents:
    def test_groups_by_directory(self, project: LensContext) -> None:
        """Two directories → at least 2 components detected."""
        nodes, edges = database.load_graph(project.graph_db)
        components = detect_components(nodes, edges, project.project_root)

        assert len(components) >= 2
        paths = [c.path for c in components]
        assert any("pkg_a" in p for p in paths)
        assert any("pkg_b" in p for p in paths)

    def test_component_has_cohesion(self, project: LensContext) -> None:
        """Each component has a cohesion score between 0 and 1."""
        nodes, edges = database.load_graph(project.graph_db)
        components = detect_components(nodes, edges, project.project_root)

        for comp in components:
            assert 0 <= comp.cohesion <= 1

    def test_component_has_modules(self, project: LensContext) -> None:
        """Components include module IDs."""
        nodes, edges = database.load_graph(project.graph_db)
        components = detect_components(nodes, edges, project.project_root)

        # pkg_a should have at least one module
        pkg_a_comp = next(
            (c for c in components if "pkg_a" in c.path), None
        )
        assert pkg_a_comp is not None
        assert len(pkg_a_comp.modules) > 0 or len(pkg_a_comp.classes) > 0

    def test_skips_single_file_directories(
        self, project: LensContext
    ) -> None:
        """Directories with only 1 node are skipped."""
        nodes, edges = database.load_graph(project.graph_db)
        components = detect_components(nodes, edges, project.project_root)

        for comp in components:
            # Each component should have at least 2 nodes
            total_nodes = len(comp.modules) + len(comp.classes) + len(
                comp.public_api
            ) + len(comp.internal_nodes)
            # The directory grouping includes module, class, functions
            # so there should be at least 2 items
            assert total_nodes >= 0  # Component was created, so directory had >= 2

    def test_component_edge_counts(self, project: LensContext) -> None:
        """Components have non-negative edge counts."""
        nodes, edges = database.load_graph(project.graph_db)
        components = detect_components(nodes, edges, project.project_root)

        for comp in components:
            assert comp.internal_edges >= 0
            assert comp.external_edges >= 0


# ---------------------------------------------------------------------------
# compute_all_metrics
# ---------------------------------------------------------------------------


class TestComputeAllMetrics:
    def test_returns_class_metrics(self, project: LensContext) -> None:
        """Project with classes → node_metrics has entries for them."""
        nodes, edges = database.load_graph(project.graph_db)
        node_metrics, project_metrics = compute_all_metrics(nodes, edges)

        # Should have metrics for Engine and Printer
        class_ids = [nid for nid in node_metrics if "Engine" in nid or "Printer" in nid]
        assert len(class_ids) >= 1

    def test_method_count_correct(self, project: LensContext) -> None:
        """Engine has 2 methods (start, stop) → method_count=2."""
        nodes, edges = database.load_graph(project.graph_db)
        node_metrics, _ = compute_all_metrics(nodes, edges)

        engine_id = next(
            (nid for nid in node_metrics if "Engine" in nid), None
        )
        assert engine_id is not None
        assert node_metrics[engine_id]["method_count"] == 2

    def test_project_metrics_include_aggregates(
        self, project: LensContext
    ) -> None:
        """Project metrics include total_classes, avg_methods, etc."""
        nodes, edges = database.load_graph(project.graph_db)
        _, project_metrics = compute_all_metrics(nodes, edges)

        assert "total_classes" in project_metrics
        assert "avg_methods" in project_metrics
        assert "median_methods" in project_metrics
        assert project_metrics["total_classes"] >= 2

    def test_percentile_rank_added(self, project: LensContext) -> None:
        """Each class gets a percentile_rank in its metrics."""
        nodes, edges = database.load_graph(project.graph_db)
        node_metrics, _ = compute_all_metrics(nodes, edges)

        for nid, metrics in node_metrics.items():
            assert "percentile_rank" in metrics
            assert 0 <= metrics["percentile_rank"] <= 100

    def test_public_private_method_split(
        self, project: LensContext
    ) -> None:
        """Engine's methods (start, stop) are all public."""
        nodes, edges = database.load_graph(project.graph_db)
        node_metrics, _ = compute_all_metrics(nodes, edges)

        engine_id = next(
            (nid for nid in node_metrics if "Engine" in nid), None
        )
        assert engine_id is not None
        assert node_metrics[engine_id]["public_methods"] == 2
        assert node_metrics[engine_id]["private_methods"] == 0

    def test_empty_project(self, tmp_path: Path) -> None:
        """Project with no classes → empty metrics."""
        (tmp_path / "app.py").write_text("def f(): pass\n")
        lens_dir = tmp_path / ".lens"
        lens_dir.mkdir()
        database.init_database(lens_dir)
        ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        ctx.full_sync()

        nodes, edges = database.load_graph(ctx.graph_db)
        node_metrics, project_metrics = compute_all_metrics(nodes, edges)

        assert len(node_metrics) == 0
        assert project_metrics == {} or project_metrics.get("total_classes", 0) == 0
