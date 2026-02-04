"""Multi-language parser: combines multiple language parsers."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from lenspr.models import Edge, EdgeConfidence, Node, Resolution
from lenspr.parsers.base import BaseParser, ProgressCallback
from lenspr.parsers.python_parser import PythonParser
from lenspr.stats import ParseStats, get_language_for_extension

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
        collect_stats: bool = False,
    ) -> tuple[list[Node], list[Edge], ParseStats | None]:
        """Parse project using all available parsers.

        Args:
            root_path: Project root directory.
            progress_callback: Optional callback(current, total, file_path) for progress.
            collect_stats: If True, return detailed parsing statistics.

        Returns:
            Tuple of (nodes, edges, stats). Stats is None if collect_stats=False.
        """
        start_time = time.time()

        # Set project root for jedi and other tools
        self.set_project_root(root_path)

        all_nodes: list[Node] = []
        all_edges: list[Edge] = []
        edges_by_parser: dict[BaseParser, list[Edge]] = {p: [] for p in self._parsers}
        extensions = set(self.get_file_extensions())

        # Initialize stats if collecting
        stats = ParseStats(project_root=root_path) if collect_stats else None

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
            ".output",  # Nuxt 3
            "coverage",
            "htmlcov",
            ".nyc_output",
            "out",  # Next.js static export
        }

        venv_suffixes = ("-env", "-venv", "_env", "_venv")

        def should_skip_path(path: Path) -> bool:
            for part in path.parts:
                if part in skip_dirs:
                    return True
                if any(part.endswith(suffix) for suffix in venv_suffixes):
                    return True
            return False

        # Collect files and track skipped directories
        files_to_parse: list[Path] = []
        skipped_counts: dict[str, int] = {}

        for file_path in sorted(root_path.rglob("*")):
            if not file_path.is_file():
                continue

            # Check if in skipped directory
            skip_reason = None
            for part in file_path.relative_to(root_path).parts:
                if part in skip_dirs:
                    skip_reason = part
                    break
                if any(part.endswith(suffix) for suffix in venv_suffixes):
                    skip_reason = part
                    break

            if skip_reason:
                if file_path.suffix.lower() in extensions:
                    skipped_counts[skip_reason] = skipped_counts.get(skip_reason, 0) + 1
                continue

            if file_path.suffix.lower() not in extensions:
                continue

            files_to_parse.append(file_path)

        # Track skipped dirs in stats
        if stats:
            for dir_name, count in skipped_counts.items():
                stats.add_skipped_dir(dir_name, count)

        total = len(files_to_parse)

        for i, file_path in enumerate(files_to_parse):
            if progress_callback:
                progress_callback(i + 1, total, str(file_path))

            ext = file_path.suffix.lower()
            language, ext_display = get_language_for_extension(ext)

            try:
                parser = self.get_parser_for_file(file_path)
                if parser is None:
                    continue

                nodes, edges = parser.parse_file(file_path, root_path)
                all_nodes.extend(nodes)
                edges_by_parser[parser].extend(edges)

                # Collect stats (before resolution - stats track raw parsed edges)
                if stats:
                    stats.add_file(file_path, language, ext_display)
                    stats.add_nodes(nodes, language, ext_display)
                    stats.add_edges(edges, language, ext_display)

            except Exception as e:
                logger.warning("Failed to parse %s: %s", file_path, e)
                if stats:
                    stats.add_file(file_path, language, ext_display)
                    stats.add_parse_error(language, str(file_path), str(e))

        # Second pass: resolve edges using each parser's cross-file resolution
        for parser, edges in edges_by_parser.items():
            if edges:
                resolved = parser.resolve_edges(edges, root_path)
                all_edges.extend(resolved)

        # Update stats with resolved edges (recalculate resolution percentages)
        if stats:
            stats.recalculate_resolution(all_edges)

        # Finalize stats
        if stats:
            stats.total_time_ms = (time.time() - start_time) * 1000

            # Add warnings based on analysis
            self._collect_warnings(stats, root_path)

        return all_nodes, all_edges, stats

    def _collect_warnings(self, stats: ParseStats, root_path: Path) -> None:
        """Collect warnings based on project analysis."""
        # Check for JS/TS projects (including monorepos)
        if "TypeScript" in stats.languages or "JavaScript" in stats.languages:
            from lenspr.monorepo import find_packages

            monorepo = find_packages(root_path)

            # Check for tsconfig/jsconfig
            has_config = False
            if monorepo.packages:
                # Check in each package directory
                for pkg in monorepo.packages:
                    if (pkg.path / "tsconfig.json").exists() or (pkg.path / "jsconfig.json").exists():
                        has_config = True
                        break
            else:
                # Check at root
                has_config = (root_path / "tsconfig.json").exists() or (root_path / "jsconfig.json").exists()

            if not has_config:
                stats.add_warning("No tsconfig.json or jsconfig.json found - create one for 80%+ JS resolution")

            # Check for node_modules
            if monorepo.packages:
                # Monorepo: check each package
                missing = monorepo.missing_node_modules
                if missing:
                    if len(missing) == 1:
                        rel = missing[0].relative_to(root_path) if missing[0] != root_path else Path(".")
                        stats.add_warning(f"node_modules not found in {rel} - run 'npm install' or use --install-deps")
                    else:
                        stats.add_warning(f"node_modules missing in {len(missing)} packages - use --install-deps")
            else:
                # Single project
                if not (root_path / "node_modules").exists():
                    stats.add_warning("node_modules not found - run 'npm install' for better type resolution")

        # Check resolution quality per language
        for lang_name, lang_stats in stats.languages.items():
            if lang_stats.total_edges > 0:
                pct = lang_stats.resolution_pct
                if pct < 80:
                    stats.add_warning(f"{lang_name} resolution is {pct:.0f}% (below 80% target)")

        # Check for parse errors
        total_errors = sum(len(lang.parse_errors) for lang in stats.languages.values())
        if total_errors > 0:
            stats.add_warning(f"{total_errors} files had parse errors")

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        languages = ["Python"]
        if any(ext in self._extension_map for ext in [".ts", ".tsx", ".js", ".jsx"]):
            languages.append("TypeScript/JavaScript")
        return languages
