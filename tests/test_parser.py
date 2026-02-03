"""Tests for the Python parser."""

from pathlib import Path

import pytest

from lenspr.models import EdgeType, NodeType
from lenspr.parsers.python_parser import PythonParser

FIXTURES = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def parser():
    return PythonParser()


class TestParseFile:
    def test_parses_module_node(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "models.py", FIXTURES)
        module_nodes = [n for n in nodes if n.type == NodeType.MODULE]
        assert len(module_nodes) == 1
        assert module_nodes[0].name == "models"

    def test_parses_classes(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "models.py", FIXTURES)
        classes = [n for n in nodes if n.type == NodeType.CLASS]
        names = {c.name for c in classes}
        assert "User" in names
        assert "Admin" in names

    def test_parses_methods(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "models.py", FIXTURES)
        methods = [n for n in nodes if n.type == NodeType.METHOD]
        names = {m.name for m in methods}
        assert "display_name" in names

    def test_parses_functions(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "main.py", FIXTURES)
        functions = [n for n in nodes if n.type == NodeType.FUNCTION]
        names = {f.name for f in functions}
        assert "main" in names
        assert "create_user" in names

    def test_parses_imports(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "main.py", FIXTURES)
        import_edges = [e for e in edges if e.type == EdgeType.IMPORTS]
        assert len(import_edges) > 0

    def test_parses_inheritance(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "models.py", FIXTURES)
        inherits = [e for e in edges if e.type == EdgeType.INHERITS]
        assert len(inherits) >= 1
        admin_inherits = [e for e in inherits if "Admin" in e.from_node]
        assert len(admin_inherits) == 1

    def test_parses_calls(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "main.py", FIXTURES)
        calls = [e for e in edges if e.type == EdgeType.CALLS]
        assert len(calls) > 0

    def test_extracts_docstrings(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "models.py", FIXTURES)
        user_class = next(n for n in nodes if n.name == "User")
        assert user_class.docstring is not None
        assert "user" in user_class.docstring.lower()

    def test_extracts_signatures(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "main.py", FIXTURES)
        create_user = next(n for n in nodes if n.name == "create_user")
        assert create_user.signature is not None
        assert "name" in create_user.signature
        assert "email" in create_user.signature

    def test_extracts_blocks(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "main.py", FIXTURES)
        blocks = [n for n in nodes if n.type == NodeType.BLOCK]
        assert len(blocks) >= 1
        # Should capture MAX_USERS and APP_NAME constants
        block_sources = " ".join(b.source_code for b in blocks)
        assert "MAX_USERS" in block_sources or "APP_NAME" in block_sources

    def test_computes_hash(self, parser):
        nodes, edges = parser.parse_file(FIXTURES / "main.py", FIXTURES)
        for node in nodes:
            assert node.hash != ""
            assert len(node.hash) == 64  # SHA256 hex length


class TestParseProject:
    def test_parses_all_files(self, parser):
        nodes, edges = parser.parse_project(FIXTURES)
        files = {n.file_path for n in nodes if n.type == NodeType.MODULE}
        assert "main.py" in files
        assert "models.py" in files

    def test_produces_cross_file_edges(self, parser):
        nodes, edges = parser.parse_project(FIXTURES)
        # main.py imports from utils.helpers and models
        import_edges = [e for e in edges if e.type == EdgeType.IMPORTS]
        assert len(import_edges) > 0

    def test_node_ids_are_unique(self, parser):
        nodes, edges = parser.parse_project(FIXTURES)
        ids = [n.id for n in nodes]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"


class TestRelativeImports:
    def test_relative_import_resolution(self, parser, tmp_path):
        """Test that relative imports resolve to full qualified names."""
        # Create a package with relative imports
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        subpkg = pkg / "submodule"
        subpkg.mkdir()
        (subpkg / "__init__.py").write_text("")
        (subpkg / "storage.py").write_text("def save(): pass\n")
        (subpkg / "router.py").write_text(
            "from . import storage\n\n"
            "def handler():\n"
            "    return storage.save()\n"
        )

        nodes, edges = parser.parse_project(tmp_path)

        # Find the call edge from handler to storage.save
        call_edges = [e for e in edges if e.type == EdgeType.CALLS]
        handler_calls = [
            e for e in call_edges
            if "router.handler" in e.from_node
        ]

        # Should have at least one call from handler
        assert len(handler_calls) >= 1

        # The target should resolve to mypackage.submodule.storage.save
        targets = [e.to_node for e in handler_calls]
        assert any("storage.save" in t for t in targets)

    def test_absolute_import_to_sibling_package(self, parser, tmp_path):
        """Test that absolute imports to sibling packages resolve correctly.

        This simulates a structure like chatplay where:
        - backend/
          - benchmarks/
            - router.py (imports from benchmarks import storage)
            - storage.py
        """
        # Create a package structure like chatplay
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "__init__.py").write_text("")

        benchmarks = backend / "benchmarks"
        benchmarks.mkdir()
        (benchmarks / "__init__.py").write_text("")
        (benchmarks / "storage.py").write_text(
            "def list_benchmarks():\n"
            "    return []\n"
        )
        (benchmarks / "router.py").write_text(
            "from benchmarks import storage\n\n"
            "def get_benchmarks():\n"
            "    return storage.list_benchmarks()\n"
        )

        nodes, edges = parser.parse_project(tmp_path)

        # Find the call edge from get_benchmarks to storage.list_benchmarks
        call_edges = [e for e in edges if e.type == EdgeType.CALLS]
        router_calls = [
            e for e in call_edges
            if "router.get_benchmarks" in e.from_node
        ]

        # Should have at least one call from get_benchmarks
        assert len(router_calls) >= 1

        # The target should resolve to backend.benchmarks.storage.list_benchmarks
        targets = [e.to_node for e in router_calls]
        # Jedi should resolve this to the full path
        assert any(
            "benchmarks.storage.list_benchmarks" in t
            for t in targets
        ), f"Expected benchmarks.storage.list_benchmarks in targets, got: {targets}"
