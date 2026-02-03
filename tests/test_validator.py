"""Tests for the three-level validator."""


from lenspr.models import Node, NodeType
from lenspr.validator import validate_full, validate_signature, validate_structure, validate_syntax


class TestSyntaxValidation:
    def test_valid_code(self):
        result = validate_syntax("def foo():\n    return 1")
        assert result.valid

    def test_invalid_code(self):
        result = validate_syntax("def foo(\n    return 1")
        assert not result.valid
        assert len(result.errors) > 0

    def test_empty_string(self):
        result = validate_syntax("")
        assert result.valid  # Empty string is valid Python


class TestStructureValidation:
    def test_function_stays_function(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2, source_code="def foo():\n    pass",
        )
        result = validate_structure("def foo():\n    return 1", node)
        assert result.valid

    def test_function_becomes_class_fails(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2, source_code="def foo():\n    pass",
        )
        result = validate_structure("class Foo:\n    pass", node)
        assert not result.valid

    def test_name_change_warns(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2, source_code="def foo():\n    pass",
        )
        result = validate_structure("def bar():\n    pass", node)
        assert result.valid  # Allowed but warned
        assert len(result.warnings) > 0
        assert "Name changed" in result.warnings[0]


class TestSignatureValidation:
    def test_compatible_change(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2,
            source_code="def foo(x, y):\n    pass",
        )
        result = validate_signature("def foo(x, y, z=None):\n    pass", node)
        assert result.valid
        assert len(result.warnings) == 0  # Adding optional param is safe

    def test_removed_required_param_warns(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2,
            source_code="def foo(x, y):\n    pass",
        )
        result = validate_signature("def foo(x):\n    pass", node)
        assert result.valid  # Warning, not error
        assert len(result.warnings) > 0
        assert "y" in result.warnings[0]

    def test_added_required_param_warns(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2,
            source_code="def foo(x):\n    pass",
        )
        result = validate_signature("def foo(x, y):\n    pass", node)
        assert len(result.warnings) > 0
        assert "y" in result.warnings[0]


class TestFullValidation:
    def test_all_levels_pass(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2,
            source_code="def foo(x):\n    pass",
        )
        result = validate_full("def foo(x):\n    return x + 1", node)
        assert result.valid
        assert len(result.warnings) == 0

    def test_syntax_error_blocks(self):
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="test.py",
            start_line=1, end_line=2,
            source_code="def foo():\n    pass",
        )
        result = validate_full("def foo(:\n    pass", node)
        assert not result.valid
