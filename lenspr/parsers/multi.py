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


def normalize_edges_by_ids(
    edges: list[Edge], node_ids: set[str],
) -> int:
    """Normalize edge endpoints using a pre-built set of node IDs.

    Resolves short import paths (e.g. ``crawlers.func``) to full node IDs
    (e.g. ``myproject.crawlers.func``) via suffix matching.  Mutates edges
    in place.

    Returns:
        Number of endpoints normalized.
    """
    # Build suffix index: suffix -> full_id (None if ambiguous)
    suffix_index: dict[str, str | None] = {}
    for nid in node_ids:
        parts = nid.split(".")
        # Start from 1 to skip the full ID (already in node_ids)
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in suffix_index:
                if suffix_index[suffix] != nid:
                    suffix_index[suffix] = None  # ambiguous
            else:
                suffix_index[suffix] = nid

    normalized = 0
    for edge in edges:
        if edge.to_node not in node_ids:
            full_id = suffix_index.get(edge.to_node)
            if full_id is not None:
                edge.to_node = full_id
                normalized += 1
        if edge.from_node not in node_ids:
            full_id = suffix_index.get(edge.from_node)
            if full_id is not None:
                edge.from_node = full_id
                normalized += 1

    if normalized:
        logger.info("Normalized %d edge endpoints via suffix matching", normalized)

    return normalized


def normalize_edge_targets(
    nodes: list[Node], edges: list[Edge],
) -> list[Edge]:
    """Normalize edge targets to match actual node IDs via suffix matching.

    When project root != package root, node IDs have a prefix that raw
    import paths lack.  E.g. node ID is ``myproject.crawlers.func`` but
    the import edge targets ``crawlers.func``.  This function resolves
    the mismatch by matching suffixes.
    """
    node_ids: set[str] = {n.id for n in nodes}
    normalize_edges_by_ids(edges, node_ids)
    return edges


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
            ".next",  # Next.js
            ".nuxt",  # Nuxt.js
            ".output",  # Nuxt 3
            "coverage",
            "htmlcov",
            ".nyc_output",
            "out",  # Next.js static export
        }

        venv_suffixes = ("-env", "-venv", "_env", "_venv")

        # Skip only at project root: "lib" is Python stdlib,
        # but "src/lib/" is a standard React/Vite utility directory
        skip_toplevel_only = {"lib"}

        def should_skip_path(path: Path) -> bool:
            for part in path.parts:
                if part in skip_dirs:
                    return True
                if any(part.endswith(suffix) for suffix in venv_suffixes):
                    return True
            return False

        # Collect files and track skipped directories
        files_to_parse: list[Path] = []
        skipped_counts: dict[str, int] = {}  # dir_name -> ALL file count (code + non-code)
        total_file_count = 0

        for file_path in sorted(root_path.rglob("*")):
            if not file_path.is_file():
                continue

            total_file_count += 1

            # Check if in skipped directory
            skip_reason = None
            rel_parts = file_path.relative_to(root_path).parts
            for idx, part in enumerate(rel_parts):
                if part in skip_dirs:
                    skip_reason = part
                    break
                if part in skip_toplevel_only and idx == 0:
                    skip_reason = part
                    break
                if any(part.endswith(suffix) for suffix in venv_suffixes):
                    skip_reason = part
                    break

            if skip_reason:
                skipped_counts[skip_reason] = skipped_counts.get(skip_reason, 0) + 1
                continue

            if file_path.suffix.lower() not in extensions:
                # Track unparsed file types
                if stats and file_path.suffix:
                    ext = file_path.suffix.lower()
                    stats.unparsed_extensions[ext] = (
                        stats.unparsed_extensions.get(ext, 0) + 1
                    )
                continue

            files_to_parse.append(file_path)

        # Track skipped dirs and total file count in stats
        if stats:
            stats.total_project_files = total_file_count
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

        # Third pass: normalize edge targets to match actual node IDs
        # Fixes mismatches when project root != package root
        normalize_edge_targets(all_nodes, all_edges)

        # Fourth pass: cross-language API mapping (frontend HTTP → backend route)
        try:
            from lenspr.resolvers.api_mapper import ApiMapper

            api_mapper = ApiMapper()
            api_mapper.extract_routes(all_nodes)
            api_mapper.extract_api_calls(all_nodes)
            api_edges = api_mapper.match()
            if api_edges:
                all_edges.extend(api_edges)
                logger.info(
                    "API mapper: added %d cross-language edges", len(api_edges),
                )
        except Exception as e:
            logger.debug("API mapper skipped: %s", e)

        # Fifth pass: SQL/DB schema mapping (function → table edges)
        try:
            from lenspr.resolvers.sql_mapper import SqlMapper

            sql_mapper = SqlMapper()
            sql_mapper.extract_tables(all_nodes)
            sql_mapper.extract_operations(all_nodes)

            # Parse raw .sql files (migrations, seeds, etc.)
            sql_file_count = 0
            for sql_file in sorted(root_path.rglob("*.sql")):
                if should_skip_path(sql_file):
                    continue
                sql_mapper.parse_sql_file(sql_file, root_path)
                sql_file_count += 1
            sql_file_nodes = sql_mapper.get_sql_file_nodes()
            if sql_file_nodes:
                all_nodes.extend(sql_file_nodes)
            if stats and sql_file_count:
                stats.infra_files["SQL files (.sql)"] = sql_file_count

            db_edges = sql_mapper.match()
            if db_edges:
                all_edges.extend(db_edges)
                logger.info(
                    "SQL mapper: added %d database edges", len(db_edges),
                )
        except Exception as e:
            logger.debug("SQL mapper skipped: %s", e)

        # Sixth pass: infrastructure mapping (Docker, env vars)
        try:
            from lenspr.resolvers.infra_mapper import InfraMapper

            infra_mapper = InfraMapper()

            # Parse docker-compose files (recursive for monorepos)
            compose_count = 0
            compose_patterns = [
                "docker-compose*.yml", "docker-compose*.yaml",
                "compose.yml", "compose.yaml",
            ]
            for pattern in compose_patterns:
                for compose_path in sorted(root_path.rglob(pattern)):
                    if compose_path.is_file() and not should_skip_path(
                        compose_path.relative_to(root_path)
                    ):
                        infra_mapper.parse_compose(compose_path)
                        compose_count += 1

            # Parse .env files (recursive for monorepos)
            env_count = 0
            for env_file in sorted(root_path.rglob(".env*")):
                if env_file.is_file() and not should_skip_path(
                    env_file.relative_to(root_path)
                ):
                    infra_mapper.parse_env_file(env_file)
                    env_count += 1

            # Extract env var definitions from compose environment: sections
            from lenspr.resolvers.infra_mapper import EnvVarDef

            for svc in infra_mapper._services.values():
                for key, val in svc.environment.items():
                    infra_mapper._env_vars.append(
                        EnvVarDef(
                            name=key,
                            value=val or "",
                            source_file=svc.file_path,
                            line=0,
                        )
                    )

            # Extract env var usages from code
            infra_mapper.extract_env_usages(all_nodes)

            # Parse Dockerfiles
            dockerfile_count = 0
            for df_path in sorted(root_path.rglob("Dockerfile*")):
                if should_skip_path(df_path):
                    continue
                infra_mapper.parse_dockerfile(df_path, root_path)
                dockerfile_count += 1
            for df_path in sorted(root_path.rglob("*.dockerfile")):
                if should_skip_path(df_path):
                    continue
                infra_mapper.parse_dockerfile(df_path, root_path)
                dockerfile_count += 1

            # Create virtual service nodes
            service_nodes = infra_mapper.get_service_nodes()
            if service_nodes:
                all_nodes.extend(service_nodes)

            # Create virtual Dockerfile nodes and edges
            dockerfile_nodes = infra_mapper.get_dockerfile_nodes()
            if dockerfile_nodes:
                all_nodes.extend(dockerfile_nodes)
            dockerfile_edges = infra_mapper.get_dockerfile_edges()
            if dockerfile_edges:
                all_edges.extend(dockerfile_edges)

            # Create edges
            infra_edges = infra_mapper.match()
            if infra_edges:
                all_edges.extend(infra_edges)
                logger.info(
                    "Infra mapper: added %d infrastructure edges", len(infra_edges),
                )

            # Track infra files in stats
            if stats:
                if compose_count:
                    stats.infra_files["Docker Compose"] = compose_count
                if env_count:
                    stats.infra_files["Environment (.env)"] = env_count
                if dockerfile_count:
                    stats.infra_files["Dockerfiles"] = dockerfile_count
        except Exception as e:
            logger.debug("Infra mapper skipped: %s", e)

        # Seventh pass: FFI bridge mapping (NAPI, koffi, WASM)
        try:
            from lenspr.resolvers.ffi_mapper import FfiMapper

            ffi_mapper = FfiMapper()
            ffi_mapper.extract_bindings(all_nodes)
            native_nodes = ffi_mapper.get_native_nodes()
            if native_nodes:
                all_nodes.extend(native_nodes)
            ffi_edges = ffi_mapper.match()
            if ffi_edges:
                all_edges.extend(ffi_edges)
                logger.info(
                    "FFI mapper: added %d native bridge edges", len(ffi_edges),
                )
        except Exception as e:
            logger.debug("FFI mapper skipped: %s", e)

        # Eighth pass: CI/CD workflow mapping (GitHub Actions)
        try:
            from lenspr.resolvers.ci_mapper import CiMapper

            ci_mapper = CiMapper()
            wf_count = 0
            gh_dir = root_path / ".github" / "workflows"
            if gh_dir.is_dir():
                for wf_path in sorted(gh_dir.glob("*.y*ml")):
                    ci_mapper.parse_github_workflow(wf_path, root_path)
                    wf_count += 1
            ci_nodes = ci_mapper.get_ci_nodes()
            if ci_nodes:
                all_nodes.extend(ci_nodes)
            ci_edges = ci_mapper.match()
            if ci_edges:
                all_edges.extend(ci_edges)
                logger.info(
                    "CI mapper: added %d CI/CD edges", len(ci_edges),
                )
            if stats and wf_count:
                stats.infra_files["CI workflows (.yml)"] = wf_count
        except Exception as e:
            logger.debug("CI mapper skipped: %s", e)

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
                    tsconfig = (pkg.path / "tsconfig.json").exists()
                    jsconfig = (pkg.path / "jsconfig.json").exists()
                    if tsconfig or jsconfig:
                        has_config = True
                        break
            else:
                # Check at root
                tsconfig = (root_path / "tsconfig.json").exists()
                jsconfig = (root_path / "jsconfig.json").exists()
                has_config = tsconfig or jsconfig

            if not has_config:
                msg = "No tsconfig.json or jsconfig.json found - "
                msg += "create one for 80%+ JS resolution"
                stats.add_warning(msg)

            # Check for node_modules
            if monorepo.packages:
                # Monorepo: check each package
                missing = monorepo.missing_node_modules
                if missing:
                    if len(missing) == 1:
                        if missing[0] != root_path:
                            rel = missing[0].relative_to(root_path)
                        else:
                            rel = Path(".")
                        msg = f"node_modules not found in {rel} - "
                        msg += "run 'npm install' or use --install-deps"
                        stats.add_warning(msg)
                    else:
                        msg = f"node_modules missing in {len(missing)} packages"
                        msg += " - use --install-deps"
                        stats.add_warning(msg)
            else:
                # Single project
                if not (root_path / "node_modules").exists():
                    msg = "node_modules not found - "
                    msg += "run 'npm install' for better type resolution"
                    stats.add_warning(msg)

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
