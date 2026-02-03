"""Tests for the file patcher."""


import pytest

from lenspr.models import Node, NodeType, Patch, PatchError
from lenspr.patcher import PatchBuffer, apply_patch, apply_patches, insert_code, remove_lines


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(
        "def foo():\n"
        "    return 1\n"
        "\n"
        "def bar():\n"
        "    return 2\n"
        "\n"
        "def baz():\n"
        "    return 3\n"
    )
    return f


class TestApplyPatch:
    def test_single_patch(self, sample_file):
        patch = Patch(start_line=1, end_line=2, new_source="def foo():\n    return 42")
        result = apply_patch(sample_file, patch)
        assert "return 42" in result
        assert "return 2" in result  # bar unchanged
        assert "return 3" in result  # baz unchanged

    def test_patch_preserves_other_code(self, sample_file):
        patch = Patch(start_line=4, end_line=5, new_source="def bar():\n    return 99")
        result = apply_patch(sample_file, patch)
        assert "return 1" in result  # foo unchanged
        assert "return 99" in result
        assert "return 3" in result  # baz unchanged


class TestApplyPatches:
    def test_multiple_patches_bottom_to_top(self, sample_file):
        patches = [
            Patch(start_line=1, end_line=2, new_source="def foo():\n    return 10"),
            Patch(start_line=7, end_line=8, new_source="def baz():\n    return 30"),
        ]
        result = apply_patches(sample_file, patches)
        assert "return 10" in result
        assert "return 2" in result   # bar unchanged
        assert "return 30" in result

    def test_overlapping_patches_raise(self, sample_file):
        patches = [
            Patch(start_line=1, end_line=3, new_source="..."),
            Patch(start_line=2, end_line=5, new_source="..."),
        ]
        with pytest.raises(PatchError, match="Overlapping"):
            apply_patches(sample_file, patches)


class TestPatchBuffer:
    def test_buffer_and_flush(self, sample_file):
        buf = PatchBuffer()
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="sample.py",
            start_line=1, end_line=2, source_code="def foo():\n    return 1",
        )
        buf.add(sample_file, node, "def foo():\n    return 42")
        assert buf.has_pending

        modified = buf.flush()
        assert len(modified) == 1
        assert not buf.has_pending
        assert "return 42" in sample_file.read_text()

    def test_discard(self, sample_file):
        buf = PatchBuffer()
        node = Node(
            id="test.foo", type=NodeType.FUNCTION, name="foo",
            qualified_name="test.foo", file_path="sample.py",
            start_line=1, end_line=2, source_code="def foo():\n    return 1",
        )
        buf.add(sample_file, node, "INVALID PYTHON {{{{")
        buf.discard()
        assert not buf.has_pending


class TestInsertCode:
    def test_insert_at_end(self, sample_file):
        result = insert_code(sample_file, "def new_func():\n    pass", 8)
        assert "def new_func():" in result

    def test_insert_at_beginning(self, sample_file):
        result = insert_code(sample_file, "# Header comment", 0)
        assert result.startswith("\n# Header comment")


class TestRemoveLines:
    def test_remove_function(self, sample_file):
        result = remove_lines(sample_file, 4, 5)
        assert "def bar" not in result
        assert "def foo" in result
        assert "def baz" in result
