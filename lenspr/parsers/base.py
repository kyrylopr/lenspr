"""Base parser interface for language-specific implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from lenspr.models import Edge, Node, Resolution

# Progress callback type: (current_file_index, total_files, current_file_path)
ProgressCallback = Callable[[int, int, str], None]

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """
    Abstract base for language parsers.

    Every language (Python, TypeScript, Go, etc.) implements this interface.
    The rest of LensPR (database, graph, patcher, tools) is language-agnostic
    and works through this abstraction.
    """

    @abstractmethod
    def parse_file(self, file_path: Path, root_path: Path) -> tuple[list[Node], list[Edge]]:
        """
        Parse a single source file into nodes and edges.

        Args:
            file_path: Absolute path to the source file.
            root_path: Project root for computing relative paths.

        Returns:
            Tuple of (nodes, edges) extracted from this file.
        """

    @abstractmethod
    def get_file_extensions(self) -> list[str]:
        """
        Return file extensions this parser handles (e.g. [".py"]).
        """

    @abstractmethod
    def resolve_name(
        self, file_path: str, line: int, column: int, project_root: str
    ) -> Resolution:
        """
        Resolve a name at a specific location to its definition.

        Args:
            file_path: Path to the file containing the name.
            line: 1-based line number.
            column: 0-based column offset.
            project_root: Project root path for context.

        Returns:
            Resolution with node_id and confidence level.
        """

    def parse_project(
        self,
        root_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """
        Parse all files in a project directory.

        Default implementation walks the directory tree, skipping common
        non-source directories. Language parsers can override for custom behavior.

        Args:
            root_path: Project root directory.
            progress_callback: Optional callback(current, total, file_path) for progress.
        """
        all_nodes: list[Node] = []
        all_edges: list[Edge] = []
        extensions = set(self.get_file_extensions())

        skip_dirs = {
            "__pycache__",
            ".git",
            ".lens",
            ".venv",
            "venv",
            "env",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "dist",
            "build",
            ".eggs",
            ".tox",
            "site-packages",  # Skip installed packages
            "lib",  # Skip lib directories (often in venvs)
        }

        # Also skip directories ending with common venv suffixes
        venv_suffixes = ("-env", "-venv", "_env", "_venv")

        def should_skip_path(path: Path) -> bool:
            """Check if path should be skipped."""
            for part in path.parts:
                if part in skip_dirs:
                    return True
                # Skip directories with venv-like names
                if any(part.endswith(suffix) for suffix in venv_suffixes):
                    return True
            return False

        # Collect files first for progress tracking
        files_to_parse: list[Path] = []
        for file_path in sorted(root_path.rglob("*")):
            if not file_path.is_file():
                continue
            if should_skip_path(file_path):
                continue
            if file_path.suffix not in extensions:
                continue
            files_to_parse.append(file_path)

        total = len(files_to_parse)

        for i, file_path in enumerate(files_to_parse):
            if progress_callback:
                progress_callback(i + 1, total, str(file_path))

            try:
                nodes, edges = self.parse_file(file_path, root_path)
                all_nodes.extend(nodes)
                all_edges.extend(edges)
            except Exception as e:
                # Log but don't fail on individual file parse errors
                logger.warning("Failed to parse %s: %s", file_path, e)

        return all_nodes, all_edges
