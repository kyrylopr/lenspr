"""Multi-language parser: combines multiple language parsers."""

from __future__ import annotations

import logging
from pathlib import Path

from lenspr.models import Edge, EdgeConfidence, Node, Resolution
from lenspr.parsers.base import BaseParser, ProgressCallback
from lenspr.parsers.python_parser import PythonParser

logger = logging.getLogger(__name__)


class MultiParser(BaseParser):
    """
    Combines multiple language parsers into one.

    Delegates to the appropriate parser based on file extension.
    Supports Python by default, TypeScript/JavaScript if tree-sitter is installed.
    """

    def __init__(self) -> None:
        self._parsers: list[BaseParser] = []
        self._extension_map: dict[str, BaseParser] = {}

        # Always add Python parser
        python_parser = PythonParser()
        self._parsers.append(python_parser)
        for ext in python_parser.get_file_extensions():
            self._extension_map[ext] = python_parser

        # Try to add TypeScript parser (optional)
        try:
            from lenspr.parsers.typescript_parser import TypeScriptParser

            ts_parser = TypeScriptParser()
            self._parsers.append(ts_parser)
            for ext in ts_parser.get_file_extensions():
                self._extension_map[ext] = ts_parser
            logger.info("TypeScript/JavaScript support enabled")
        except ImportError:
            logger.debug(
                "TypeScript parser not available (install with: pip install 'lenspr[typescript]')"
            )

    def get_file_extensions(self) -> list[str]:
        """Return all supported file extensions."""
        return list(self._extension_map.keys())

    def get_parser_for_file(self, file_path: Path) -> BaseParser | None:
        """Get the appropriate parser for a file."""
        return self._extension_map.get(file_path.suffix.lower())

    def parse_file(
        self, file_path: Path, root_path: Path
    ) -> tuple[list[Node], list[Edge]]:
        """Parse a file using the appropriate parser."""
        parser = self.get_parser_for_file(file_path)
        if parser is None:
            return [], []
        return parser.parse_file(file_path, root_path)

    def resolve_name(
        self, file_path: str, line: int, column: int, project_root: str
    ) -> Resolution:
        """Resolve a name using the appropriate parser."""
        parser = self.get_parser_for_file(Path(file_path))
        if parser is None:
            return Resolution(
                node_id=None,
                confidence=EdgeConfidence.UNRESOLVED,
                untracked_reason="no_parser_for_extension",
            )
        return parser.resolve_name(file_path, line, column, project_root)

    def set_project_root(self, root_path: Path) -> None:
        """Set project root on all parsers that support it."""
        for parser in self._parsers:
            if hasattr(parser, "set_project_root"):
                parser.set_project_root(root_path)

    def parse_project(
        self,
        root_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """Parse project using all available parsers."""
        # Set project root for jedi and other tools
        self.set_project_root(root_path)

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
            "site-packages",
            "lib",
            ".next",  # Next.js
            ".nuxt",  # Nuxt.js
            "coverage",
        }

        venv_suffixes = ("-env", "-venv", "_env", "_venv")

        def should_skip_path(path: Path) -> bool:
            for part in path.parts:
                if part in skip_dirs:
                    return True
                if any(part.endswith(suffix) for suffix in venv_suffixes):
                    return True
            return False

        # Collect files
        files_to_parse: list[Path] = []
        for file_path in sorted(root_path.rglob("*")):
            if not file_path.is_file():
                continue
            if should_skip_path(file_path):
                continue
            if file_path.suffix.lower() not in extensions:
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
                logger.warning("Failed to parse %s: %s", file_path, e)

        return all_nodes, all_edges

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        languages = ["Python"]
        if any(ext in self._extension_map for ext in [".ts", ".tsx", ".js", ".jsx"]):
            languages.append("TypeScript/JavaScript")
        return languages
