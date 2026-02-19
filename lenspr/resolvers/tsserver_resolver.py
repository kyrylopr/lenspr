"""TypeScript/JavaScript edge resolver using typescript-language-server via LSP.

Uses the standard LSP protocol to resolve INFERRED call edges
in TypeScript and JavaScript files via go-to-definition.

Requires:
  - typescript-language-server (npm install -g typescript-language-server typescript)
  - OR npx (comes with Node.js, will auto-fetch on first use)
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from lenspr.models import Edge, EdgeConfidence, EdgeType
from lenspr.resolvers.lsp_client import LSPClient, LSPError

logger = logging.getLogger(__name__)

# Extensions handled by this resolver
_TS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx"})

# Language IDs for LSP protocol
_LANG_IDS: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
}


def is_tsserver_available() -> bool:
    """Check if typescript-language-server can be launched."""
    if shutil.which("typescript-language-server"):
        return True
    # npx can fetch it on-demand
    if shutil.which("npx"):
        return True
    return False


def _is_external(target: str) -> bool:
    """Check if a target looks like an external module."""
    if not target:
        return False
    top = target.split(".")[0]
    # Common Node.js built-ins and well-known packages
    externals = {
        "react", "react-dom", "next", "vue", "angular", "svelte",
        "express", "koa", "fastify", "hapi", "nestjs",
        "fs", "path", "http", "https", "url", "os", "crypto",
        "node", "child_process", "stream", "util", "events",
        "lodash", "axios", "moment", "dayjs", "zod", "yup",
        "prisma", "typeorm", "sequelize", "mongoose",
        "jest", "mocha", "vitest", "cypress",
    }
    return top in externals


class TsServerResolver:
    """Resolve TypeScript/JavaScript edges using typescript-language-server.

    Usage::

        resolver = TsServerResolver(project_root)
        resolved = resolver.resolve_edges(edges)
        resolver.close()
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()
        self._client = LSPClient(timeout=15.0)
        self._opened_files: set[str] = set()
        self._initialized = False

    def _ensure_started(self) -> None:
        """Start typescript-language-server if not already running."""
        if self._initialized:
            return

        cmd = self._find_command()
        if not cmd:
            raise LSPError("typescript-language-server not found")

        try:
            self._client.start(cmd, self._project_root)
            self._client.initialize()
            self._initialized = True
            logger.info("TsServer resolver started for %s", self._project_root)
        except LSPError as e:
            logger.warning("Failed to start typescript-language-server: %s", e)
            raise

    @staticmethod
    def _find_command() -> list[str] | None:
        """Find the typescript-language-server command."""
        if shutil.which("typescript-language-server"):
            return ["typescript-language-server", "--stdio"]
        if shutil.which("npx"):
            return ["npx", "typescript-language-server", "--stdio"]
        return None

    def _open_file(self, file_path: str) -> None:
        """Open a file in tsserver if not already opened."""
        abs_path = str(Path(file_path).resolve())
        if abs_path in self._opened_files:
            return
        try:
            source = Path(abs_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        lang_id = _LANG_IDS.get(Path(abs_path).suffix.lower(), "typescript")
        self._client.did_open(abs_path, lang_id, source)
        self._opened_files.add(abs_path)

    def resolve_edges(
        self, edges: list[Edge], settle_time: float = 1.0,
    ) -> list[Edge]:
        """Resolve INFERRED call edges using go-to-definition.

        Args:
            edges: All edges (only INFERRED CALLS are resolved).
            settle_time: Seconds to wait after opening files for analysis.

        Returns:
            Updated edge list with resolved edges.
        """
        self._ensure_started()

        # Collect edges that need resolution
        call_edges = [
            e for e in edges
            if e.type == EdgeType.CALLS
            and e.confidence == EdgeConfidence.INFERRED
        ]
        if not call_edges:
            return edges

        # Open all relevant source files
        files_to_open: set[str] = set()
        for edge in call_edges:
            file_path = edge.metadata.get("file") if edge.metadata else None
            if file_path and Path(file_path).suffix.lower() in _TS_EXTENSIONS:
                files_to_open.add(file_path)

        for fp in files_to_open:
            self._open_file(fp)

        # Let tsserver analyze
        if settle_time > 0 and files_to_open:
            time.sleep(settle_time)

        # Resolve each edge
        resolved_count = 0
        for edge in call_edges:
            file_path = edge.metadata.get("file") if edge.metadata else None
            if not file_path:
                continue
            abs_path = str(Path(file_path).resolve())

            if _is_external(edge.to_node):
                edge.confidence = EdgeConfidence.EXTERNAL
                resolved_count += 1
                continue

            if self._resolve_single_edge(edge, abs_path):
                resolved_count += 1

        if resolved_count:
            logger.info(
                "TsServer resolver: resolved %d/%d edges",
                resolved_count, len(call_edges),
            )
        return edges

    def _resolve_single_edge(
        self, edge: Edge, file_path: str,
    ) -> bool:
        """Try to resolve one edge via go-to-definition. Returns True if resolved."""
        if edge.line_number is None:
            return False

        line_idx = edge.line_number - 1  # LSP is 0-based
        column = edge.metadata.get("column", 0) if edge.metadata else 0

        # Read file to try smarter column detection
        try:
            source_lines = Path(file_path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return False

        if line_idx < 0 or line_idx >= len(source_lines):
            return False

        line = source_lines[line_idx]

        # For dotted names like "user.greet", resolve at the attribute
        target_parts = edge.to_node.split(".")
        attr_name = target_parts[-1]

        columns: list[int] = []

        # 1. Attribute position
        if len(target_parts) > 1:
            dot_attr = f".{attr_name}"
            pos = line.find(dot_attr)
            if pos >= 0:
                columns.append(pos + 1)

        # 2. Stored column
        if column:
            columns.append(column)

        # 3. Name occurrence
        pos = line.find(attr_name)
        if pos >= 0 and pos not in columns:
            columns.append(pos)

        if not columns:
            columns.append(0)

        loc = None
        for col in columns:
            try:
                loc = self._client.definition(file_path, line=line_idx, col=col)
            except LSPError:
                continue
            if loc:
                break

        if not loc:
            return False

        resolved_id = self._location_to_node_id(loc.file_path, loc.line + 1)
        if not resolved_id:
            return False

        edge.to_node = resolved_id
        if _is_external(resolved_id):
            edge.confidence = EdgeConfidence.EXTERNAL
        else:
            edge.confidence = EdgeConfidence.RESOLVED
        return True

    def _location_to_node_id(self, file_path: str, line: int) -> str | None:
        """Convert a definition location to a LensPR node ID.

        Maps file path + line to module.name convention.
        """
        try:
            def_path = Path(file_path).resolve()
        except (OSError, ValueError):
            return None

        # Check if inside project
        try:
            rel_path = def_path.relative_to(self._project_root)
        except ValueError:
            return self._external_module_id(file_path)

        # Convert path to module ID: src/utils/helpers.ts → src.utils.helpers
        parts = list(rel_path.with_suffix("").parts)
        if not parts:
            return None

        # Read the file to find the symbol name at the definition line
        try:
            source = def_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ".".join(parts)

        lines = source.splitlines()
        if line < 1 or line > len(lines):
            return ".".join(parts)

        def_line = lines[line - 1].strip()
        name = self._extract_name_from_def(def_line)

        module_id = ".".join(parts)
        if name:
            return f"{module_id}.{name}"
        return module_id

    @staticmethod
    def _extract_name_from_def(line: str) -> str | None:
        """Extract function/class/variable name from a TS/JS definition line."""
        import re

        # function name(
        m = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", line)
        if m:
            return m.group(1)

        # class Name
        m = re.match(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", line)
        if m:
            return m.group(1)

        # interface Name
        m = re.match(r"(?:export\s+)?interface\s+(\w+)", line)
        if m:
            return m.group(1)

        # type Name =
        m = re.match(r"(?:export\s+)?type\s+(\w+)", line)
        if m:
            return m.group(1)

        # const/let/var name =
        m = re.match(r"(?:export\s+)?(?:const|let|var)\s+(\w+)", line)
        if m:
            return m.group(1)

        # Arrow function: name = (
        m = re.match(r"(\w+)\s*=\s*(?:async\s+)?\(", line)
        if m:
            return m.group(1)

        # Method: name(
        m = re.match(r"(?:async\s+)?(\w+)\s*\(", line)
        if m and m.group(1) not in ("if", "for", "while", "switch", "catch"):
            return m.group(1)

        return None

    @staticmethod
    def _external_module_id(file_path: str) -> str | None:
        """Derive module ID for node_modules or external definitions."""
        path = Path(file_path)
        parts = path.parts
        for i, part in enumerate(parts):
            if part == "node_modules" and i + 1 < len(parts):
                remainder = parts[i + 1:]
                # Scoped package: @scope/package
                if remainder and remainder[0].startswith("@"):
                    if len(remainder) >= 2:
                        return f"{remainder[0]}/{remainder[1]}"
                    return remainder[0]
                if remainder:
                    return remainder[0]
        return None

    def close(self) -> None:
        """Shut down the typescript-language-server process."""
        if self._initialized:
            self._client.shutdown()
            self._initialized = False
            self._opened_files.clear()
            logger.info("TsServer resolver shut down")

    def __enter__(self) -> TsServerResolver:
        return self

    def __exit__(self, *args) -> None:
        self.close()
