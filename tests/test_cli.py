"""Tests for the CLI entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal Python project for CLI tests."""
    src = tmp_path / "main.py"
    src.write_text("def hello():\n    return 'world'\n")
    return tmp_path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lenspr.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestInit:
    def test_init_creates_lens_dir(self, sample_project: Path) -> None:
        result = run_cli("init", str(sample_project))
        assert result.returncode == 0
        assert (sample_project / ".lens").exists()
        assert "Initialized" in result.stdout

    def test_init_shows_node_count(self, sample_project: Path) -> None:
        result = run_cli("init", str(sample_project))
        assert "Nodes:" in result.stdout
        assert "Edges:" in result.stdout

    def test_init_force(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("init", "--force", str(sample_project))
        assert result.returncode == 0


class TestSync:
    def test_sync_after_init(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("sync", str(sample_project))
        assert result.returncode == 0
        assert "Sync complete" in result.stdout

    def test_sync_full_flag(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("sync", "--full", str(sample_project))
        assert result.returncode == 0
        assert "Sync complete" in result.stdout


class TestStatus:
    def test_status_shows_stats(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("status", str(sample_project))
        assert result.returncode == 0
        assert "Nodes:" in result.stdout
        assert "Files:" in result.stdout


class TestSearch:
    def test_search_finds_function(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("search", "hello", str(sample_project))
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_search_no_results(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("search", "nonexistent_xyz", str(sample_project))
        assert result.returncode == 0


class TestImpact:
    def test_impact_on_node(self, sample_project: Path) -> None:
        run_cli("init", str(sample_project))
        result = run_cli("impact", "main.hello", str(sample_project))
        assert result.returncode == 0


class TestNoArgs:
    def test_no_command_shows_help(self) -> None:
        result = run_cli()
        assert result.returncode == 1
