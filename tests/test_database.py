"""Tests for SQLite database operations."""

import tempfile
from pathlib import Path

import pytest

import sqlite3

from lenspr.database import (
    _connect,
    delete_node,
    get_edges,
    get_node,
    get_nodes,
    init_database,
    load_graph,
    save_graph,
    search_nodes,
    update_node_source,
)
from lenspr.models import Edge, EdgeType, Node, NodeType


@pytest.fixture
def db_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir) / ".lens"
        init_database(d)
        yield d


@pytest.fixture
def sample_nodes():
    return [
        Node(
            id="app.main",
            type=NodeType.FUNCTION,
            name="main",
            qualified_name="app.main",
            file_path="app.py",
            start_line=1,
            end_line=5,
            source_code="def main():\n    pass",
        ),
        Node(
            id="app.helper",
            type=NodeType.FUNCTION,
            name="helper",
            qualified_name="app.helper",
            file_path="app.py",
            start_line=7,
            end_line=10,
            source_code="def helper():\n    return 42",
        ),
    ]


@pytest.fixture
def sample_edges():
    return [
        Edge(
            id="e1",
            from_node="app.main",
            to_node="app.helper",
            type=EdgeType.CALLS,
            line_number=3,
        ),
    ]


class TestInitDatabase:
    def test_creates_databases(self, db_dir):
        assert (db_dir / "graph.db").exists()
        assert (db_dir / "history.db").exists()
        assert (db_dir / "resolve_cache.db").exists()


class TestSaveAndLoad:
    def test_roundtrip(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        nodes, edges = load_graph(db)
        assert len(nodes) == 2
        assert len(edges) == 1
        assert nodes[0].name in ("main", "helper")

    def test_save_overwrites(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        save_graph(sample_nodes[:1], [], db)
        nodes, edges = load_graph(db)
        assert len(nodes) == 1
        assert len(edges) == 0


class TestGetNode:
    def test_found(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        node = get_node("app.main", db)
        assert node is not None
        assert node.name == "main"

    def test_not_found(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        node = get_node("nonexistent", db)
        assert node is None


class TestGetNodes:
    def test_filter_by_type(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        nodes = get_nodes(db, type_filter="function")
        assert len(nodes) == 2

    def test_filter_by_file(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        nodes = get_nodes(db, file_filter="app.py")
        assert len(nodes) == 2


class TestGetEdges:
    def test_outgoing(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        edges = get_edges("app.main", db, "outgoing")
        assert len(edges) == 1
        assert edges[0].to_node == "app.helper"

    def test_incoming(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        edges = get_edges("app.helper", db, "incoming")
        assert len(edges) == 1

    def test_both(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        edges = get_edges("app.main", db, "both")
        assert len(edges) == 1


class TestUpdateAndDelete:
    def test_update_source(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        success = update_node_source("app.main", "def main():\n    print('hi')", "newhash", db)
        assert success
        node = get_node("app.main", db)
        assert "print" in node.source_code

    def test_delete(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        success = delete_node("app.helper", db)
        assert success
        assert get_node("app.helper", db) is None
        # Edge should also be deleted
        edges = get_edges("app.main", db, "outgoing")
        assert len(edges) == 0


class TestSearch:
    def test_search_by_name(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        results = search_nodes("main", db, "name")
        assert len(results) == 1

    def test_search_by_code(self, db_dir, sample_nodes, sample_edges):
        db = db_dir / "graph.db"
        save_graph(sample_nodes, sample_edges, db)
        results = search_nodes("42", db, "code")
        assert len(results) == 1
        assert results[0].name == "helper"


class TestConnect:
    def test_returns_connection_for_valid_path(self, tmp_path):
        """_connect opens a connection and sets WAL mode and row_factory."""
        db_path = tmp_path / "test.db"
        conn = _connect(db_path)
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            assert row[0] == "wal"
        finally:
            conn.close()

    def test_raises_operational_error_for_invalid_path(self, tmp_path):
        """_connect raises OperationalError with a path-aware message when it can't open the DB."""
        bad_path = tmp_path / "nonexistent_dir" / "x" / "graph.db"
        with pytest.raises(sqlite3.OperationalError, match="Cannot open database at"):
            _connect(bad_path)
