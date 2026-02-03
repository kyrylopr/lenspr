"""Tests for NetworkX graph operations."""

import pytest

from lenspr.graph import (
    build_graph,
    detect_circular_imports,
    find_dead_code,
    find_path,
    get_dependency_tree,
    get_impact_zone,
)
from lenspr.models import Edge, EdgeType, Node, NodeType


@pytest.fixture
def sample_graph():
    """Build a sample graph for testing."""
    nodes = [
        Node(id="main", type=NodeType.MODULE, name="main", qualified_name="main",
             file_path="main.py", start_line=1, end_line=20, source_code="..."),
        Node(id="main.run", type=NodeType.FUNCTION, name="run", qualified_name="main.run",
             file_path="main.py", start_line=5, end_line=10, source_code="def run(): ..."),
        Node(id="service", type=NodeType.MODULE, name="service", qualified_name="service",
             file_path="service.py", start_line=1, end_line=30, source_code="..."),
        Node(id="service.process", type=NodeType.FUNCTION, name="process",
             qualified_name="service.process", file_path="service.py",
             start_line=5, end_line=15, source_code="def process(): ..."),
        Node(id="service.validate", type=NodeType.FUNCTION, name="validate",
             qualified_name="service.validate", file_path="service.py",
             start_line=17, end_line=25, source_code="def validate(): ..."),
        Node(id="db.save", type=NodeType.FUNCTION, name="save",
             qualified_name="db.save", file_path="db.py",
             start_line=1, end_line=10, source_code="def save(): ..."),
        Node(id="unused.orphan", type=NodeType.FUNCTION, name="orphan",
             qualified_name="unused.orphan", file_path="unused.py",
             start_line=1, end_line=5, source_code="def orphan(): ..."),
    ]
    edges = [
        Edge(id="e1", from_node="main.run", to_node="service.process", type=EdgeType.CALLS),
        Edge(id="e2", from_node="service.process", to_node="service.validate", type=EdgeType.CALLS),
        Edge(id="e3", from_node="service.process", to_node="db.save", type=EdgeType.CALLS),
        Edge(id="e4", from_node="main", to_node="service", type=EdgeType.IMPORTS),
    ]
    return build_graph(nodes, edges)


class TestImpactZone:
    def test_direct_callers(self, sample_graph):
        impact = get_impact_zone(sample_graph, "service.process")
        assert "main.run" in impact["direct_callers"]

    def test_indirect_callers(self, sample_graph):
        impact = get_impact_zone(sample_graph, "db.save", depth=2)
        assert "service.process" in impact["direct_callers"]

    def test_nonexistent_node(self, sample_graph):
        impact = get_impact_zone(sample_graph, "does.not.exist")
        assert impact["total_affected"] == 0

    def test_leaf_node(self, sample_graph):
        impact = get_impact_zone(sample_graph, "main.run")
        # main.run has no callers in this graph
        assert impact["total_affected"] == 0


class TestDependencyTree:
    def test_dependencies(self, sample_graph):
        tree = get_dependency_tree(sample_graph, "service.process")
        deps = [d["target"]["id"] for d in tree["dependencies"]]
        assert "service.validate" in deps
        assert "db.save" in deps

    def test_max_depth(self, sample_graph):
        tree = get_dependency_tree(sample_graph, "main.run", max_depth=1)
        for dep in tree["dependencies"]:
            assert dep["target"]["dependencies"] == []
            assert dep["target"]["truncated"] is True


class TestDeadCode:
    def test_finds_unreachable(self, sample_graph):
        dead = find_dead_code(sample_graph, entry_points=["main.run"])
        assert "unused.orphan" in dead

    def test_reachable_not_dead(self, sample_graph):
        dead = find_dead_code(sample_graph, entry_points=["main.run"])
        assert "service.process" not in dead
        assert "db.save" not in dead


class TestFindPath:
    def test_path_exists(self, sample_graph):
        path = find_path(sample_graph, "main.run", "db.save")
        assert len(path) > 0
        assert path[0] == "main.run"
        assert path[-1] == "db.save"

    def test_no_path(self, sample_graph):
        path = find_path(sample_graph, "unused.orphan", "main.run")
        assert path == []


class TestCircularImports:
    def test_no_cycles(self, sample_graph):
        cycles = detect_circular_imports(sample_graph)
        assert len(cycles) == 0

    def test_detects_cycle(self):
        nodes = [
            Node(id="a", type=NodeType.MODULE, name="a", qualified_name="a",
                 file_path="a.py", start_line=1, end_line=1, source_code=""),
            Node(id="b", type=NodeType.MODULE, name="b", qualified_name="b",
                 file_path="b.py", start_line=1, end_line=1, source_code=""),
        ]
        edges = [
            Edge(id="e1", from_node="a", to_node="b", type=EdgeType.IMPORTS),
            Edge(id="e2", from_node="b", to_node="a", type=EdgeType.IMPORTS),
        ]
        G = build_graph(nodes, edges)
        cycles = detect_circular_imports(G)
        assert len(cycles) >= 1
