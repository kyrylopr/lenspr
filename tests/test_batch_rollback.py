"""Tests for handle_batch multi-file atomic rollback.

Covers:
- Successful multi-file batch update
- Rollback on reparse failure (file 2 breaks → file 1 restored)
- Rollback on flush/patch failure
- verify_tests=True rollback on regressions
- _rollback_files helper
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.modification import (
    _rollback_files,
    handle_batch,
)


@pytest.fixture()
def project(tmp_path: Path) -> LensContext:
    """Two-file project for multi-file batch tests."""
    (tmp_path / "mod_a.py").write_text(
        "def func_a():\n"
        "    return 'a'\n"
    )
    (tmp_path / "mod_b.py").write_text(
        "def func_b():\n"
        "    return 'b'\n"
    )
    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)
    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


class TestBatchMultiFileSuccess:
    """Happy path: batch updates across two files succeed."""

    def test_both_files_updated(self, project: LensContext):
        result = handle_batch(
            {
                "updates": [
                    {"node_id": "mod_a.func_a", "new_source": "def func_a():\n    return 'A'\n"},
                    {"node_id": "mod_b.func_b", "new_source": "def func_b():\n    return 'B'\n"},
                ],
            },
            project,
        )
        assert result.success
        assert result.data["count"] == 2

        # Verify file contents on disk
        a_content = (project.project_root / "mod_a.py").read_text()
        b_content = (project.project_root / "mod_b.py").read_text()
        assert "'A'" in a_content
        assert "'B'" in b_content

    def test_graph_updated_after_batch(self, project: LensContext):
        handle_batch(
            {
                "updates": [
                    {"node_id": "mod_a.func_a", "new_source": "def func_a():\n    return 42\n"},
                ],
            },
            project,
        )
        node = database.get_node("mod_a.func_a", project.graph_db)
        assert node is not None
        assert "42" in node.source_code


class TestBatchRollbackOnReparseFailure:
    """When graph sync fails for one file, ALL files must be restored."""

    def test_reparse_failure_rollbacks_all_files(self, project: LensContext):
        original_a = (project.project_root / "mod_a.py").read_text()
        original_b = (project.project_root / "mod_b.py").read_text()

        # Monkey-patch reparse_file to fail on the second call
        call_count = 0
        original_reparse = project.reparse_file

        def failing_reparse(file_path):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("Simulated reparse crash")
            return original_reparse(file_path)

        project.reparse_file = failing_reparse  # type: ignore[assignment]

        result = handle_batch(
            {
                "updates": [
                    {
                        "node_id": "mod_a.func_a",
                        "new_source": "def func_a():\n"
                                      "    return 'NEW_A'\n",
                    },
                    {
                        "node_id": "mod_b.func_b",
                        "new_source": "def func_b():\n"
                                      "    return 'NEW_B'\n",
                    },
                ],
            },
            project,
        )

        assert not result.success
        assert "Graph sync failed" in result.error

        # BOTH files must be restored
        assert (project.project_root / "mod_a.py").read_text() == original_a
        assert (project.project_root / "mod_b.py").read_text() == original_b


class TestBatchRollbackOnFlushFailure:
    """When PatchBuffer.flush() fails, all files must be restored."""

    def test_flush_failure_restores_files(self, project: LensContext):
        original_a = (project.project_root / "mod_a.py").read_text()

        from lenspr.models import PatchError

        with patch.object(project.patch_buffer, "flush", side_effect=PatchError("disk full")):
            result = handle_batch(
                {
                    "updates": [
                        {
                            "node_id": "mod_a.func_a",
                            "new_source": "def func_a():\n"
                                          "    return 'GONE'\n",
                        },
                    ],
                },
                project,
            )

        assert not result.success
        assert "Patch failed" in result.error

        # File must be restored to original
        assert (project.project_root / "mod_a.py").read_text() == original_a


class TestBatchValidationAborts:
    """Validation failures abort before any file is touched."""

    def test_invalid_syntax_aborts(self, project: LensContext):
        original = (project.project_root / "mod_a.py").read_text()

        result = handle_batch(
            {
                "updates": [
                    {"node_id": "mod_a.func_a", "new_source": "def func_a(\n    broken syntax\n"},
                ],
            },
            project,
        )

        assert not result.success
        assert (project.project_root / "mod_a.py").read_text() == original

    def test_nonexistent_node_aborts(self, project: LensContext):
        result = handle_batch(
            {
                "updates": [
                    {"node_id": "mod_a.nonexistent", "new_source": "def x():\n    pass\n"},
                ],
            },
            project,
        )
        assert not result.success
        assert "not found" in result.error.lower()


class TestBatchVerifyTests:
    """verify_tests=True runs tests before/after and rolls back on regressions."""

    def test_verify_tests_rollback_on_regression(self, project: LensContext):
        original_a = (project.project_root / "mod_a.py").read_text()

        # First call: baseline (all pass). Second call: regression.
        baseline = {"passed": 10, "failed": 0, "errors": 0, "all_passed": True, "failures": []}
        regression = {
            "passed": 9, "failed": 1, "errors": 0, "all_passed": False,
            "failures": [{"test": "tests/test_x.py::test_broken", "reason": "AssertionError"}],
        }

        with patch(
            "lenspr.tools.modification._run_test_baseline",
            side_effect=[baseline, regression],
        ):
            result = handle_batch(
                {
                    "updates": [
                        {
                            "node_id": "mod_a.func_a",
                            "new_source": "def func_a():\n"
                                          "    return 'BAD'\n",
                        },
                    ],
                    "verify_tests": True,
                },
                project,
            )

        assert not result.success
        assert "regression" in result.error.lower()
        assert "test_broken" in str(result.data.get("regressions", []))

        # File must be restored
        assert (project.project_root / "mod_a.py").read_text() == original_a

    def test_verify_tests_passes_when_no_regressions(self, project: LensContext):
        baseline = {"passed": 10, "failed": 0, "errors": 0, "all_passed": True, "failures": []}
        after = {"passed": 10, "failed": 0, "errors": 0, "all_passed": True, "failures": []}

        with patch(
            "lenspr.tools.modification._run_test_baseline",
            side_effect=[baseline, after],
        ):
            result = handle_batch(
                {
                    "updates": [
                        {
                            "node_id": "mod_a.func_a",
                            "new_source": "def func_a():\n"
                                          "    return 'GOOD'\n",
                        },
                    ],
                    "verify_tests": True,
                },
                project,
            )

        assert result.success
        assert result.data["tests"]["regressions"] == 0

    def test_verify_tests_ignores_preexisting_failures(self, project: LensContext):
        """Pre-existing failures should not cause rollback."""
        baseline = {
            "passed": 9, "failed": 1, "errors": 0, "all_passed": False,
            "failures": [{"test": "tests/test_old.py::test_known_broken", "reason": "old bug"}],
        }
        after = {
            "passed": 9, "failed": 1, "errors": 0, "all_passed": False,
            "failures": [{"test": "tests/test_old.py::test_known_broken", "reason": "old bug"}],
        }

        with patch(
            "lenspr.tools.modification._run_test_baseline",
            side_effect=[baseline, after],
        ):
            result = handle_batch(
                {
                    "updates": [
                        {
                            "node_id": "mod_a.func_a",
                            "new_source": "def func_a():\n"
                                          "    return 'OK'\n",
                        },
                    ],
                    "verify_tests": True,
                },
                project,
            )

        # Same failure in both → NOT a regression → batch succeeds
        assert result.success


class TestRollbackFilesHelper:
    """Tests for the _rollback_files utility."""

    def test_restores_file_contents(self, tmp_path: Path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("original_a")
        f2.write_text("original_b")

        old_contents = {f1: "original_a", f2: "original_b"}

        # Overwrite files
        f1.write_text("modified_a")
        f2.write_text("modified_b")

        # Create a minimal mock context
        class FakeCtx:
            project_root = tmp_path
            def reparse_file(self, path):
                pass

        _rollback_files(old_contents, {"a.py", "b.py"}, FakeCtx())  # type: ignore[arg-type]

        assert f1.read_text() == "original_a"
        assert f2.read_text() == "original_b"

    def test_swallows_write_errors(self, tmp_path: Path):
        """_rollback_files must not crash even if file write fails."""
        f1 = tmp_path / "a.py"
        f1.write_text("original")

        old_contents = {f1: "original", Path("/nonexistent/path.py"): "data"}

        class FakeCtx:
            project_root = tmp_path
            def reparse_file(self, path):
                pass

        # Must not raise despite /nonexistent/path.py
        _rollback_files(old_contents, {"a.py"}, FakeCtx())  # type: ignore[arg-type]
        assert f1.read_text() == "original"
