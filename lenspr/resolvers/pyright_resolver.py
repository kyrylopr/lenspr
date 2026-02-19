"""Pyright-based edge resolver for Python code.

Uses pyright-langserver (LSP) to resolve INFERRED edges to RESOLVED or
EXTERNAL. Pyright handles self.method(), super().method(), isinstance()
narrowing, and complex import chains — all cases where Jedi struggles.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from lenspr.models import Edge, EdgeConfidence, EdgeType
from lenspr.parsers.python_parser import _is_external
from lenspr.resolvers.lsp_client import LSPClient, LSPError

logger = logging.getLogger(__name__)


def is_pyright_available() -> bool:
    """Check if pyright-langserver is installed and on PATH."""
    return shutil.which("pyright-langserver") is not None


class PyrightResolver:
    """Resolve Python edges using pyright-langserver via LSP.

    Usage::

        resolver = PyrightResolver(project_root)
        resolver.resolve_edges(edges, "/path/to/file.py")
        resolver.close()
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()
        self._client = LSPClient(timeout=15.0)
        self._opened_files: set[str] = set()
        self._initialized = False

    def _ensure_started(self) -> None:
        """Start pyright-langserver if not already running."""
        if self._initialized:
            return
        try:
            self._client.start(
                ["pyright-langserver", "--stdio"], self._project_root
            )
            self._client.initialize()
            self._initialized = True
            logger.info("Pyright resolver started for %s", self._project_root)
        except LSPError as e:
            logger.warning("Failed to start pyright: %s", e)
            raise

    def _open_file(self, file_path: str) -> None:
        """Open a file in pyright if not already opened."""
        abs_path = str(Path(file_path).resolve())
        if abs_path in self._opened_files:
            return
        try:
            source = Path(abs_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        self._client.did_open(abs_path, "python", source)
        self._opened_files.add(abs_path)

    def resolve_edges(
        self, edges: list[Edge], file_path: str, settle_time: float = 0.5
    ) -> None:
        """Resolve INFERRED edges in-place using pyright go-to-definition.

        Args:
            edges: List of edges to resolve (modified in-place).
            file_path: Absolute path to the source file.
            settle_time: Seconds to wait after didOpen for pyright analysis.
        """
        self._ensure_started()
        abs_path = str(Path(file_path).resolve())

        # Open the file and give pyright time to analyze
        self._open_file(abs_path)
        if settle_time > 0:
            time.sleep(settle_time)

        source_lines = Path(abs_path).read_text(encoding="utf-8").splitlines()

        for edge in edges:
            if edge.confidence != EdgeConfidence.INFERRED:
                continue
            if edge.type not in (EdgeType.CALLS, EdgeType.USES):
                continue
            if edge.line_number is None:
                continue

            # Quick check: if target is already known external
            if _is_external(edge.to_node):
                edge.confidence = EdgeConfidence.EXTERNAL
                continue

            self._resolve_single_edge(edge, abs_path, source_lines)

    def _resolve_single_edge(
        self, edge: Edge, file_path: str, source_lines: list[str]
    ) -> None:
        """Try to resolve a single edge via pyright definition lookup."""
        line_idx = edge.line_number - 1  # LSP is 0-based
        if line_idx < 0 or line_idx >= len(source_lines):
            return

        line = source_lines[line_idx]

        # For dotted targets like "user.greet", resolve at the attribute name
        # (greet), not the receiver (user). The receiver resolves to a variable,
        # the attribute resolves to the actual method/function.
        target_parts = edge.to_node.split(".")
        attr_name = target_parts[-1]

        # Build candidate columns to try, in priority order
        columns: list[int] = []

        # 1. Try attribute position (for dotted names)
        if len(target_parts) > 1:
            # Find .attr_name in the line — the dot+name pattern
            dot_attr = f".{attr_name}"
            pos = line.find(dot_attr)
            if pos >= 0:
                columns.append(pos + 1)  # Skip the dot

        # 2. Try stored column
        if edge.column is not None:
            columns.append(edge.column)

        # 3. Try finding the target name anywhere in the line
        pos = line.find(attr_name)
        if pos >= 0 and pos not in columns:
            columns.append(pos)

        # 4. Fallback to column 0
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
            return

        # Map definition location to a node ID
        def_path = loc.file_path
        def_line = loc.line + 1  # Convert from 0-based to 1-based

        resolved_id = self._location_to_node_id(def_path, def_line)
        if not resolved_id:
            return

        edge.to_node = resolved_id
        if _is_external(resolved_id):
            edge.confidence = EdgeConfidence.EXTERNAL
        else:
            edge.confidence = EdgeConfidence.RESOLVED

    def _location_to_node_id(self, file_path: str, line: int) -> str | None:
        """Convert a definition location to a LensPR node ID.

        Maps file path + line number to a qualified module.name identifier
        matching the LensPR graph convention.
        """
        try:
            def_path = Path(file_path).resolve()
        except (OSError, ValueError):
            return None

        # Check if the definition is inside the project
        try:
            rel_path = def_path.relative_to(self._project_root)
        except ValueError:
            # Definition is outside project (stdlib, site-packages)
            return self._external_module_id(file_path)

        # Convert file path to module ID: lenspr/validator.py → lenspr.validator
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

        # Extract the name from the definition line
        def_line = lines[line - 1].strip()
        name = self._extract_name_from_def(def_line)

        module_id = ".".join(parts)
        if name:
            return f"{module_id}.{name}"
        return module_id

    @staticmethod
    def _extract_name_from_def(line: str) -> str | None:
        """Extract function/class/variable name from a definition line."""
        for prefix in ("def ", "async def "):
            if line.startswith(prefix):
                rest = line[len(prefix):]
                paren = rest.find("(")
                if paren >= 0:
                    return rest[:paren].strip()
                return rest.split(":")[0].strip()

        if line.startswith("class "):
            rest = line[6:]
            paren = rest.find("(")
            colon = rest.find(":")
            end = min(
                p for p in (paren, colon, len(rest)) if p >= 0
            )
            return rest[:end].strip()

        # Variable assignment: name = ... or name: type = ...
        for sep in (":", "="):
            if sep in line:
                name = line.split(sep, 1)[0].strip()
                if name.isidentifier():
                    return name

        return None

    @staticmethod
    def _external_module_id(file_path: str) -> str | None:
        """Derive a module ID for an external (stdlib/site-packages) file."""
        path = Path(file_path)
        # Try to extract from site-packages path
        parts = path.parts
        for i, part in enumerate(parts):
            if part == "site-packages" and i + 1 < len(parts):
                remainder = parts[i + 1:]
                module_parts = list(remainder)
                # Remove .py extension from last part
                if module_parts:
                    last = module_parts[-1]
                    if last.endswith(".py"):
                        module_parts[-1] = last[:-3]
                    elif last.endswith(".pyi"):
                        module_parts[-1] = last[:-4]
                return ".".join(module_parts)

        # For stdlib, try to get module name from path
        if "lib/python" in file_path or "typeshed" in file_path:
            stem = path.stem
            if stem == "__init__":
                return path.parent.name
            return stem

        return None

    def close(self) -> None:
        """Shut down the pyright-langserver process."""
        if self._initialized:
            self._client.shutdown()
            self._initialized = False
            self._opened_files.clear()
            logger.info("Pyright resolver shut down")

    def __enter__(self) -> PyrightResolver:
        return self

    def __exit__(self, *args) -> None:
        self.close()
