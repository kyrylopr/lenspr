"""Tests for lenspr/tools/entry_points.py — declarative entry point pattern registry."""

from __future__ import annotations

import networkx as nx

from lenspr.tools.entry_points import (
    _CUSTOM_PREDICATES,
    ENTRY_POINT_PATTERNS,
    CheckField,
    EntryPointPattern,
    MatchOp,
    _check_op,
    collect_entry_points,
    collect_public_api,
    expand_entry_points,
    matches_pattern,
)

# ---------------------------------------------------------------------------
# _check_op — low-level match operations
# ---------------------------------------------------------------------------


class TestCheckOp:
    def test_exact_match(self) -> None:
        assert _check_op(MatchOp.EXACT, "main", ("main", "__main__"))
        assert _check_op(MatchOp.EXACT, "__main__", ("main", "__main__"))
        assert not _check_op(MatchOp.EXACT, "main_loop", ("main", "__main__"))

    def test_prefix_match(self) -> None:
        assert _check_op(MatchOp.PREFIX, "test_create_user", ("test_",))
        assert not _check_op(MatchOp.PREFIX, "create_test", ("test_",))

    def test_suffix_match(self) -> None:
        assert _check_op(MatchOp.SUFFIX, "conftest.py", ("conftest.py",))
        assert _check_op(MatchOp.SUFFIX, "TaskStatus", ("Status",))
        assert not _check_op(MatchOp.SUFFIX, "conftest.py.bak", ("conftest.py",))

    def test_contains_match(self) -> None:
        assert _check_op(MatchOp.CONTAINS, "src/mcp_server.py", ("mcp_server",))
        assert _check_op(MatchOp.CONTAINS, "@app.get('/')", ("@app.",))
        assert not _check_op(MatchOp.CONTAINS, "app_server.py", ("mcp_server",))

    def test_empty_value(self) -> None:
        assert not _check_op(MatchOp.EXACT, "", ("main",))
        assert not _check_op(MatchOp.PREFIX, "", ("test_",))
        assert not _check_op(MatchOp.CONTAINS, "", ("mcp_server",))

    def test_multiple_values_ored(self) -> None:
        assert _check_op(MatchOp.PREFIX, "cmd_init", ("cmd_", "handle_"))
        assert _check_op(MatchOp.PREFIX, "handle_update", ("cmd_", "handle_"))
        assert not _check_op(MatchOp.PREFIX, "do_thing", ("cmd_", "handle_"))


# ---------------------------------------------------------------------------
# matches_pattern — single pattern against single node
# ---------------------------------------------------------------------------


class TestMatchesPattern:
    def test_name_exact(self) -> None:
        pat = EntryPointPattern("main", CheckField.NAME, MatchOp.EXACT, ("main",))
        assert matches_pattern(pat, {"name": "main", "type": "function"})
        assert not matches_pattern(pat, {"name": "main_loop", "type": "function"})

    def test_type_filter_passes(self) -> None:
        pat = EntryPointPattern(
            "web", CheckField.SOURCE, MatchOp.CONTAINS,
            ("@app.get",), type_filter=("function",),
        )
        data = {"name": "get_users", "type": "function", "source_code": "@app.get('/')"}
        assert matches_pattern(pat, data)

    def test_type_filter_blocks(self) -> None:
        pat = EntryPointPattern(
            "web", CheckField.SOURCE, MatchOp.CONTAINS,
            ("@app.get",), type_filter=("function",),
        )
        data = {"name": "GetUsers", "type": "class", "source_code": "@app.get('/')"}
        assert not matches_pattern(pat, data)

    def test_file_path_contains(self) -> None:
        pat = EntryPointPattern(
            "mcp", CheckField.FILE_PATH, MatchOp.CONTAINS, ("mcp_server",),
        )
        assert matches_pattern(pat, {"file_path": "lenspr/mcp_server.py", "type": "function"})
        assert not matches_pattern(pat, {"file_path": "lenspr/cli.py", "type": "function"})

    def test_missing_field_returns_empty(self) -> None:
        pat = EntryPointPattern("test", CheckField.SOURCE, MatchOp.CONTAINS, ("@pytest.fixture",))
        # No source_code key → empty string → no match
        assert not matches_pattern(pat, {"name": "my_fixture", "type": "function"})

    def test_dunder_method_exact(self) -> None:
        pat = EntryPointPattern(
            "dunder", CheckField.NAME, MatchOp.EXACT,
            ("__init__", "__repr__"), type_filter=("method",),
        )
        assert matches_pattern(pat, {"name": "__init__", "type": "method"})
        assert not matches_pattern(pat, {"name": "__init__", "type": "function"})

    def test_enum_suffix(self) -> None:
        pat = EntryPointPattern(
            "enum", CheckField.NAME, MatchOp.SUFFIX,
            ("Enum", "Type"), type_filter=("class",),
        )
        assert matches_pattern(pat, {"name": "NodeType", "type": "class"})
        assert not matches_pattern(pat, {"name": "NodeType", "type": "function"})


# ---------------------------------------------------------------------------
# Custom predicates
# ---------------------------------------------------------------------------


class TestCustomPredicates:
    def test_init_top_level_function(self) -> None:
        pred = _CUSTOM_PREDICATES["init_top_level_function"]
        assert pred("pkg.func", {"file_path": "pkg/__init__.py", "type": "function"})
        # Nested function in __init__.py — too many dots
        assert not pred("pkg.sub.func", {"file_path": "pkg/__init__.py", "type": "function"})
        # Right depth but not __init__.py
        assert not pred("pkg.func", {"file_path": "pkg/main.py", "type": "function"})
        # Class in __init__.py — wrong type
        assert not pred("pkg.MyClass", {"file_path": "pkg/__init__.py", "type": "class"})

    def test_private_method(self) -> None:
        pred = _CUSTOM_PREDICATES["private_method"]
        assert pred("cls._helper", {"type": "method", "name": "_helper"})
        # Dunder methods should NOT match
        assert not pred("cls.__init__", {"type": "method", "name": "__init__"})
        # Functions (not methods) should NOT match
        assert not pred("mod._util", {"type": "function", "name": "_util"})


# ---------------------------------------------------------------------------
# collect_public_api
# ---------------------------------------------------------------------------


class TestCollectPublicApi:
    def _make_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node("pkg", type="module", source_code='__all__ = ["func_a", "func_b"]')
        G.add_node("pkg.func_a", type="function")
        G.add_node("pkg.func_b", type="function")
        G.add_node("pkg.Sub.method", type="method")  # nested — not direct child
        G.add_node("other", type="module", source_code="x = 1")  # no __all__
        G.add_node("other.func_c", type="function")
        return G

    def test_finds_direct_children_of_all_module(self) -> None:
        public = collect_public_api(self._make_graph())
        assert "pkg.func_a" in public
        assert "pkg.func_b" in public

    def test_excludes_nested_nodes(self) -> None:
        public = collect_public_api(self._make_graph())
        assert "pkg.Sub.method" not in public

    def test_excludes_modules_without_all(self) -> None:
        public = collect_public_api(self._make_graph())
        assert "other.func_c" not in public


# ---------------------------------------------------------------------------
# collect_entry_points
# ---------------------------------------------------------------------------


class TestCollectEntryPoints:
    def _make_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        # Main entry
        G.add_node("app.main", type="function", name="main",
                    file_path="app/main.py", source_code="")
        # Test
        G.add_node("tests.test_app.test_create", type="function", name="test_create",
                    file_path="tests/test_app.py", source_code="")
        # Block
        G.add_node("app.main._block_1", type="block", name="block_1",
                    file_path="app/main.py", source_code="")
        # Class
        G.add_node("app.models.User", type="class", name="User",
                    file_path="app/models.py", source_code="")
        # Regular function (should NOT be entry point)
        G.add_node("app.utils.helper", type="function", name="helper",
                    file_path="app/utils.py", source_code="")
        # Handler
        G.add_node("app.handlers.handle_update", type="function", name="handle_update",
                    file_path="app/handlers.py", source_code="")
        # CLI command
        G.add_node("app.cli.cmd_init", type="function", name="cmd_init",
                    file_path="app/cli.py", source_code="")
        # Pytest fixture
        G.add_node("tests.fixtures.db_session", type="function", name="db_session",
                    file_path="tests/fixtures.py",
                    source_code="@pytest.fixture\ndef db_session():\n    pass")
        return G

    def test_finds_main(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "app.main" in entries

    def test_finds_test(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "tests.test_app.test_create" in entries

    def test_finds_block(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "app.main._block_1" in entries

    def test_finds_class(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "app.models.User" in entries

    def test_finds_handler(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "app.handlers.handle_update" in entries

    def test_finds_cli_command(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "app.cli.cmd_init" in entries

    def test_finds_pytest_fixture(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "tests.fixtures.db_session" in entries

    def test_excludes_plain_helper(self) -> None:
        entries = collect_entry_points(self._make_graph())
        assert "app.utils.helper" not in entries

    def test_custom_patterns_subset(self) -> None:
        """Passing a subset of patterns only matches that subset."""
        only_tests = tuple(p for p in ENTRY_POINT_PATTERNS if p.category == "test")
        entries = collect_entry_points(self._make_graph(), patterns=only_tests)
        assert "tests.test_app.test_create" in entries
        assert "app.main" not in entries  # main pattern excluded

    def test_custom_predicates_disabled(self) -> None:
        """Passing empty custom_predicates disables them."""
        G = nx.DiGraph()
        G.add_node("pkg.func", type="function", name="func",
                    file_path="pkg/__init__.py", source_code="")
        # With custom predicates, this could match init_top_level_function
        # But "func" doesn't have nid.count(".") == 1 — pkg.func has 1 dot — it matches!
        entries_with = collect_entry_points(G)
        entries_without = collect_entry_points(G, custom_predicates={})
        # pkg.func matches no declarative pattern but matches custom predicate
        assert "pkg.func" in entries_with
        assert "pkg.func" not in entries_without


# ---------------------------------------------------------------------------
# expand_entry_points
# ---------------------------------------------------------------------------


class TestExpandEntryPoints:
    def test_expands_class_methods(self) -> None:
        G = nx.DiGraph()
        G.add_node("mod.MyClass", type="class")
        G.add_node("mod.MyClass.method_a", type="method")
        G.add_node("mod.MyClass.method_b", type="method")
        G.add_node("mod.other_func", type="function")

        expanded = expand_entry_points(G, {"mod.MyClass"})
        assert "mod.MyClass.method_a" in expanded
        assert "mod.MyClass.method_b" in expanded
        assert "mod.other_func" not in expanded

    def test_expands_nested_functions(self) -> None:
        G = nx.DiGraph()
        G.add_node("mod.outer", type="function")
        G.add_node("mod.outer.inner", type="function")
        G.add_node("mod.outer.InnerClass", type="class")
        G.add_node("mod.unrelated", type="function")

        expanded = expand_entry_points(G, {"mod.outer"})
        assert "mod.outer.inner" in expanded
        assert "mod.outer.InnerClass" in expanded
        assert "mod.unrelated" not in expanded

    def test_expands_decorated_functions(self) -> None:
        G = nx.DiGraph()
        G.add_node("mod.decorator", type="function")
        G.add_node("mod.decorated", type="function")
        G.add_edge("mod.decorator", "mod.decorated", type="decorates")

        expanded = expand_entry_points(G, set())
        assert "mod.decorated" in expanded

    def test_preserves_existing_entries(self) -> None:
        G = nx.DiGraph()
        G.add_node("mod.existing", type="function")
        expanded = expand_entry_points(G, {"mod.existing"})
        assert "mod.existing" in expanded


# ---------------------------------------------------------------------------
# Registry completeness — sanity checks on ENTRY_POINT_PATTERNS
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    def test_registry_is_nonempty(self) -> None:
        assert len(ENTRY_POINT_PATTERNS) > 25

    def test_all_patterns_have_category(self) -> None:
        for pat in ENTRY_POINT_PATTERNS:
            assert pat.category, f"Pattern with values={pat.values} has empty category"

    def test_all_patterns_have_nonempty_values(self) -> None:
        for pat in ENTRY_POINT_PATTERNS:
            assert len(pat.values) > 0, f"Pattern {pat.category} has empty values"

    def test_categories_cover_expected_domains(self) -> None:
        categories = {p.category for p in ENTRY_POINT_PATTERNS}
        expected = {"main", "test", "structural", "cli", "mcp", "handler", "web",
                    "migration", "task_queue", "pytest", "django", "dunder", "visitor"}
        assert expected.issubset(categories), f"Missing categories: {expected - categories}"
