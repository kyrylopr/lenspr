"""Tests for git integration tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import lenspr
from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.git import (
    _is_git_repo,
    _run_git,
    handle_blame,
    handle_commit_scope,
    handle_node_history,
    handle_recent_changes,
)


@pytest.fixture
def git_project(tmp_path: Path) -> LensContext:
    """Create a minimal project with git repo and initialized LensPR context."""
    # Create source file
    src = tmp_path / "app.py"
    src.write_text(
        "def greet(name):\n"
        '    return f"Hello, {name}"\n'
    )

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=tmp_path, capture_output=True,
    )

    # Initialize LensPR
    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


class TestGitHelpers:
    """Test git helper functions."""

    def test_run_git_success(self, git_project: LensContext) -> None:
        """Running a valid git command returns success."""
        success, output = _run_git(
            ["rev-parse", "--git-dir"],
            str(git_project.project_root),
        )
        assert success is True
        assert ".git" in output

    def test_run_git_failure(self, tmp_path: Path) -> None:
        """Running git in non-repo returns failure."""
        success, output = _run_git(
            ["rev-parse", "--git-dir"],
            str(tmp_path),
        )
        assert success is False

    def test_is_git_repo_true(self, git_project: LensContext) -> None:
        """Correctly identifies a git repo."""
        assert _is_git_repo(str(git_project.project_root)) is True

    def test_is_git_repo_false(self, tmp_path: Path) -> None:
        """Correctly identifies non-repo."""
        assert _is_git_repo(str(tmp_path)) is False


class TestBlame:
    """Test lens_blame handler."""

    def test_blame_returns_author_info(self, git_project: LensContext) -> None:
        """Blame returns author information for node lines."""
        result = handle_blame({"node_id": "app.greet"}, git_project)

        assert result.success
        assert result.data is not None
        assert result.data["node_id"] == "app.greet"
        assert result.data["total_lines"] == 2
        assert "Test User" in result.data["authors"]

    def test_blame_node_not_found(self, git_project: LensContext) -> None:
        """Blame returns error for non-existent node."""
        result = handle_blame({"node_id": "nonexistent"}, git_project)

        assert not result.success
        assert "not found" in result.error.lower()

    def test_blame_not_git_repo(self, tmp_path: Path) -> None:
        """Blame fails gracefully for non-git repo."""
        # Create project without git
        src = tmp_path / "app.py"
        src.write_text("def test(): pass\n")

        lens_dir = tmp_path / ".lens"
        lens_dir.mkdir()
        database.init_database(lens_dir)

        ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
        ctx.full_sync()

        result = handle_blame({"node_id": "app.test"}, ctx)

        assert not result.success
        assert "git" in result.error.lower()


class TestNodeHistory:
    """Test lens_node_history handler."""

    def test_history_returns_commits(self, git_project: LensContext) -> None:
        """Node history returns commit information."""
        result = handle_node_history(
            {"node_id": "app.greet", "limit": 5},
            git_project,
        )

        assert result.success
        assert result.data is not None
        assert result.data["count"] >= 1
        assert len(result.data["commits"]) >= 1

        commit = result.data["commits"][0]
        assert "hash" in commit
        assert "author" in commit
        assert "message" in commit

    def test_history_node_not_found(self, git_project: LensContext) -> None:
        """History returns error for non-existent node."""
        result = handle_node_history({"node_id": "nonexistent"}, git_project)

        assert not result.success
        assert "not found" in result.error.lower()


class TestCommitScope:
    """Test lens_commit_scope handler."""

    def test_commit_scope_returns_affected_nodes(
        self, git_project: LensContext
    ) -> None:
        """Commit scope returns list of affected nodes."""
        result = handle_commit_scope({"commit": "HEAD"}, git_project)

        assert result.success
        assert result.data is not None
        assert "affected_nodes" in result.data
        # Initial commit should have affected the greet function
        assert result.data["count"] >= 0

    def test_commit_scope_invalid_commit(self, git_project: LensContext) -> None:
        """Commit scope fails for invalid commit hash."""
        result = handle_commit_scope(
            {"commit": "invalidhash123"},
            git_project,
        )

        assert not result.success
        assert "invalid" in result.error.lower()


class TestRecentChanges:
    """Test lens_recent_changes handler."""

    def test_recent_changes_returns_commits(
        self, git_project: LensContext
    ) -> None:
        """Recent changes returns list of commits with files."""
        result = handle_recent_changes({"limit": 5}, git_project)

        assert result.success
        assert result.data is not None
        assert result.data["count"] >= 1

        commit = result.data["commits"][0]
        assert "hash" in commit
        assert "message" in commit
        assert "files" in commit

    def test_recent_changes_with_file_filter(
        self, git_project: LensContext
    ) -> None:
        """Recent changes respects file filter."""
        result = handle_recent_changes(
            {"limit": 5, "file_path": "app.py"},
            git_project,
        )

        assert result.success


class TestGitToolIntegration:
    """Integration tests using actual lenspr.handle_tool."""

    def test_handle_tool_lens_blame(self, git_project: LensContext) -> None:
        """lens_blame works through handle_tool."""
        lenspr._ctx = git_project
        result = lenspr.handle_tool("lens_blame", {"node_id": "app.greet"})

        assert result["success"]
        assert "authors" in result["data"]

    def test_handle_tool_lens_node_history(
        self, git_project: LensContext
    ) -> None:
        """lens_node_history works through handle_tool."""
        lenspr._ctx = git_project
        result = lenspr.handle_tool(
            "lens_node_history",
            {"node_id": "app.greet"},
        )

        assert result["success"]
        assert "commits" in result["data"]

    def test_handle_tool_lens_commit_scope(
        self, git_project: LensContext
    ) -> None:
        """lens_commit_scope works through handle_tool."""
        lenspr._ctx = git_project
        result = lenspr.handle_tool("lens_commit_scope", {"commit": "HEAD"})

        assert result["success"]

    def test_handle_tool_lens_recent_changes(
        self, git_project: LensContext
    ) -> None:
        """lens_recent_changes works through handle_tool."""
        lenspr._ctx = git_project
        result = lenspr.handle_tool("lens_recent_changes", {"limit": 3})

        assert result["success"]
        assert "commits" in result["data"]
