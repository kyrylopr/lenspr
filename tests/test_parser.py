"""Tests for the Python parser."""

from pathlib import Path

import pytest

from lenspr.models import Edge, EdgeConfidence, EdgeType, Node, NodeType
from lenspr.parsers.multi import MultiParser, normalize_edge_targets
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


class TestLazyImports:
    """Test that imports inside function bodies (lazy imports) are tracked."""

    def test_lazy_import_creates_edge_from_function(self, parser, tmp_path):
        """Lazy import should create an IMPORTS edge from the function, not the module."""
        # Create two files: one with a function, one that lazy-imports it
        lib_file = tmp_path / "lib.py"
        lib_file.write_text("def helper():\n    return 42\n")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text(
            "def my_func():\n"
            "    from lib import helper\n"
            "    return helper()\n"
        )

        nodes, edges = parser.parse_file(caller_file, tmp_path)

        # Find IMPORTS edges
        import_edges = [e for e in edges if e.type == EdgeType.IMPORTS]

        # There should be an IMPORTS edge from the function (not the module)
        func_import_edges = [
            e for e in import_edges
            if "my_func" in e.from_node and "helper" in e.to_node
        ]
        assert len(func_import_edges) >= 1, (
            f"Expected IMPORTS edge from my_func to helper, got: "
            f"{[(e.from_node, e.to_node) for e in import_edges]}"
        )

    def test_lazy_import_resolves_calls(self, parser, tmp_path):
        """Calls to lazily imported names should be resolved via import table."""
        caller_file = tmp_path / "caller.py"
        caller_file.write_text(
            "def my_func():\n"
            "    from lib import helper\n"
            "    return helper()\n"
        )

        nodes, edges = parser.parse_file(caller_file, tmp_path)

        # Find CALLS edges from my_func
        call_edges = [
            e for e in edges
            if e.type == EdgeType.CALLS and "my_func" in e.from_node
        ]

        # The call to helper() should resolve to lib.helper (not bare 'helper')
        assert len(call_edges) >= 1, f"Expected CALLS edge from my_func, got none"
        targets = [e.to_node for e in call_edges]
        assert any(
            "lib.helper" in t for t in targets
        ), f"Expected lib.helper in call targets, got: {targets}"

    def test_lazy_import_not_from_module(self, parser, tmp_path):
        """Lazy imports should NOT create IMPORTS edges from the module."""
        caller_file = tmp_path / "caller.py"
        caller_file.write_text(
            "def my_func():\n"
            "    from lib import helper\n"
            "    return helper()\n"
        )

        nodes, edges = parser.parse_file(caller_file, tmp_path)

        import_edges = [e for e in edges if e.type == EdgeType.IMPORTS]
        module_import_edges = [
            e for e in import_edges
            if e.from_node == "caller" and "helper" in e.to_node
        ]
        # Module-level import edges should not exist for lazy imports
        # The edge should come from the function, not the module
        for e in module_import_edges:
            assert "my_func" in e.from_node, (
                f"IMPORTS edge from module 'caller' instead of function: {e.from_node} -> {e.to_node}"
            )


class TestSelfMethodResolution:
    """self.method() → ClassName.method() edge resolution."""

    def _parse_source(self, source, module_id="mod", file_path="mod.py"):
        import ast
        from lenspr.parsers.python_parser import CodeGraphVisitor

        visitor = CodeGraphVisitor(source.splitlines(), module_id, file_path)
        visitor.visit(ast.parse(source))
        return [e for e in visitor.edges if e.type == EdgeType.CALLS]

    def test_self_method_creates_class_edge(self):
        """self.method_b() inside MyClass.method_a → edge to mod.MyClass.method_b"""
        edges = self._parse_source(
            "class MyClass:\n"
            "    def method_a(self):\n"
            "        self.method_b()\n"
            "    def method_b(self):\n"
            "        pass\n"
        )
        targets = [e.to_node for e in edges]
        assert "mod.MyClass.method_b" in targets

    def test_cls_method_creates_class_edge(self):
        """cls.create() inside MyClass.from_config → edge to mod.MyClass.create"""
        edges = self._parse_source(
            "class MyClass:\n"
            "    @classmethod\n"
            "    def from_config(cls, cfg):\n"
            "        return cls.create(cfg)\n"
            "    @classmethod\n"
            "    def create(cls, cfg):\n"
            "        pass\n"
        )
        targets = [e.to_node for e in edges]
        assert "mod.MyClass.create" in targets

    def test_self_in_nested_class(self):
        """self.y() inside Outer.Inner.x → edge to mod.Outer.Inner.y"""
        edges = self._parse_source(
            "class Outer:\n"
            "    class Inner:\n"
            "        def x(self):\n"
            "            self.y()\n"
            "        def y(self):\n"
            "            pass\n"
        )
        targets = [e.to_node for e in edges]
        assert "mod.Outer.Inner.y" in targets

    def test_non_self_attribute_unchanged(self):
        """obj.method() — not self, stays as-is for import table / jedi."""
        edges = self._parse_source(
            "class MyClass:\n"
            "    def method_a(self):\n"
            "        obj.do_something()\n"
        )
        targets = [e.to_node for e in edges]
        # Should NOT be rewritten to mod.MyClass.do_something
        assert all("MyClass.do_something" not in t for t in targets)

    def test_self_private_method(self):
        """self._private() inside class → edge to class._private."""
        edges = self._parse_source(
            "class MyClass:\n"
            "    def public(self):\n"
            "        self._private()\n"
            "    def _private(self):\n"
            "        pass\n"
        )
        targets = [e.to_node for e in edges]
        assert "mod.MyClass._private" in targets

    def test_self_outside_class_not_rewritten(self):
        """self.x() outside any class → stays as-is (edge case, bad code)."""
        edges = self._parse_source(
            "def standalone(self):\n"
            "    self.x()\n"
        )
        targets = [e.to_node for e in edges]
        # No class context → should NOT be rewritten
        assert all("MyClass" not in t for t in targets)
        assert any("self.x" in t for t in targets)


class TestMockPatchExtraction:
    """@patch("module.function") → MOCKS edge to mock target."""

    def _parse_source(self, source, module_id="test_mod", file_path="test_mod.py"):
        import ast
        from lenspr.parsers.python_parser import CodeGraphVisitor

        visitor = CodeGraphVisitor(source.splitlines(), module_id, file_path)
        visitor.visit(ast.parse(source))
        return [e for e in visitor.edges if e.type == EdgeType.MOCKS]

    def test_patch_creates_mocks_edge(self):
        """@patch("myapp.db.save") → MOCKS edge to myapp.db.save."""
        edges = self._parse_source(
            "from unittest.mock import patch\n"
            "\n"
            "@patch('myapp.db.save')\n"
            "def test_save():\n"
            "    pass\n"
        )
        assert len(edges) == 1
        assert edges[0].from_node == "test_mod.test_save"
        assert edges[0].to_node == "myapp.db.save"

    def test_mock_patch_creates_mocks_edge(self):
        """@mock.patch("myapp.db.save") → same result via mock.patch."""
        edges = self._parse_source(
            "from unittest import mock\n"
            "\n"
            "@mock.patch('myapp.service.run')\n"
            "def test_run():\n"
            "    pass\n"
        )
        assert len(edges) == 1
        assert edges[0].to_node == "myapp.service.run"

    def test_stacked_patches_multiple_edges(self):
        """@patch("a.b") @patch("c.d") → two MOCKS edges."""
        edges = self._parse_source(
            "from unittest.mock import patch\n"
            "\n"
            "@patch('alpha.beta')\n"
            "@patch('gamma.delta')\n"
            "def test_multi():\n"
            "    pass\n"
        )
        assert len(edges) == 2
        targets = {e.to_node for e in edges}
        assert targets == {"alpha.beta", "gamma.delta"}

    def test_patch_without_dot_ignored(self):
        """@patch("bare_name") → no MOCKS edge (ambiguous target)."""
        edges = self._parse_source(
            "from unittest.mock import patch\n"
            "\n"
            "@patch('bare_name')\n"
            "def test_bare():\n"
            "    pass\n"
        )
        assert len(edges) == 0

    def test_non_string_arg_ignored(self):
        """@patch(some_var) → no MOCKS edge."""
        edges = self._parse_source(
            "from unittest.mock import patch\n"
            "TARGET = 'myapp.db.save'\n"
            "\n"
            "@patch(TARGET)\n"
            "def test_var():\n"
            "    pass\n"
        )
        assert len(edges) == 0

    def test_non_patch_decorator_no_mocks(self):
        """@pytest.fixture → no MOCKS edge."""
        edges = self._parse_source(
            "import pytest\n"
            "\n"
            "@pytest.fixture\n"
            "def my_fixture():\n"
            "    pass\n"
        )
        assert len(edges) == 0

    def test_mocks_edge_confidence_is_inferred(self):
        """MOCKS edges should have INFERRED confidence for cross-file resolution."""
        edges = self._parse_source(
            "from unittest.mock import patch\n"
            "\n"
            "@patch('myapp.db.save')\n"
            "def test_save():\n"
            "    pass\n"
        )
        assert edges[0].confidence == EdgeConfidence.INFERRED


def _make_node(node_id, name=None):
    """Helper to create a minimal Node for normalization tests."""
    return Node(
        id=node_id,
        type=NodeType.FUNCTION,
        name=name or node_id.split(".")[-1],
        qualified_name=node_id,
        file_path="dummy.py",
        start_line=1,
        end_line=1,
        source_code="pass",
    )


def _make_edge(from_node, to_node, edge_type=EdgeType.CALLS):
    """Helper to create a minimal Edge for normalization tests."""
    return Edge(
        id="test",
        from_node=from_node,
        to_node=to_node,
        type=edge_type,
    )


class TestEdgeNormalization:
    """Test normalize_edge_targets for mismatched project/package roots."""

    def test_normalizes_suffix_match(self):
        """Edge target 'crawlers.func' should resolve to 'myproject.crawlers.func'."""
        nodes = [
            _make_node("myproject.crawlers.integration.fetch_data"),
            _make_node("myproject.main.run"),
        ]
        edges = [
            _make_edge("myproject.main.run", "crawlers.integration.fetch_data"),
        ]

        normalize_edge_targets(nodes, edges)
        assert edges[0].to_node == "myproject.crawlers.integration.fetch_data"

    def test_does_not_normalize_ambiguous(self):
        """When suffix matches multiple nodes, leave edge unchanged."""
        nodes = [
            _make_node("pkg_a.utils.helper"),
            _make_node("pkg_b.utils.helper"),
        ]
        edges = [
            _make_edge("something", "utils.helper"),
        ]

        normalize_edge_targets(nodes, edges)
        assert edges[0].to_node == "utils.helper"  # unchanged

    def test_already_matched_unchanged(self):
        """Edges that already match a node ID stay the same."""
        nodes = [_make_node("myproject.utils.helper")]
        edges = [
            _make_edge("myproject.main", "myproject.utils.helper"),
        ]

        normalize_edge_targets(nodes, edges)
        assert edges[0].to_node == "myproject.utils.helper"

    def test_from_node_normalization(self):
        """from_node should also be normalized if mismatched."""
        nodes = [
            _make_node("myproject.main.run"),
            _make_node("myproject.utils.helper"),
        ]
        edges = [
            _make_edge("main.run", "myproject.utils.helper"),
        ]

        normalize_edge_targets(nodes, edges)
        assert edges[0].from_node == "myproject.main.run"

    def test_external_edges_untouched(self):
        """Edges to external modules (no matching node) stay unchanged."""
        nodes = [_make_node("myproject.main")]
        edges = [
            _make_edge("myproject.main", "os.path.join"),
        ]

        normalize_edge_targets(nodes, edges)
        assert edges[0].to_node == "os.path.join"

    def test_cross_file_with_subdirectory(self, tmp_path):
        """Integration: parse from parent dir, edges should still connect."""
        # parent_dir/mypackage/a.py  and  parent_dir/mypackage/b.py
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("def helper():\n    return 42\n")
        (pkg / "b.py").write_text(
            "from mypackage.a import helper\n\n"
            "def caller():\n"
            "    return helper()\n"
        )

        mp = MultiParser()
        nodes, edges, _ = mp.parse_project(tmp_path)  # root = parent_dir

        node_ids = {n.id for n in nodes}
        assert "mypackage.a.helper" in node_ids

        # Import and call edges should point to the real node ID
        edges_to_helper = [
            e for e in edges
            if e.to_node == "mypackage.a.helper"
        ]
        assert len(edges_to_helper) >= 1, (
            f"Expected edge to mypackage.a.helper, got targets: "
            f"{[e.to_node for e in edges if 'helper' in e.to_node]}"
        )

    def test_lazy_import_cross_file_with_subdirectory(self, tmp_path):
        """Integration: lazy import from parent dir also connects."""
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "a.py").write_text("def helper():\n    return 42\n")
        (pkg / "b.py").write_text(
            "def caller():\n"
            "    from mypackage.a import helper\n"
            "    return helper()\n"
        )

        mp = MultiParser()
        nodes, edges, _ = mp.parse_project(tmp_path)

        edges_to_helper = [
            e for e in edges
            if e.to_node == "mypackage.a.helper"
        ]
        assert len(edges_to_helper) >= 1, (
            f"Expected edge to mypackage.a.helper from lazy import, got targets: "
            f"{[e.to_node for e in edges if 'helper' in e.to_node]}"
        )
