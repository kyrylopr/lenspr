"""TypeScript cross-file resolution without Node.js.

Provides module resolution and export tracking for TypeScript/JavaScript projects.
Parses tsconfig.json for path aliases and tracks exports from parsed files.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lenspr.models import EdgeConfidence, Resolution

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class TsConfig:
    """Parsed tsconfig.json settings relevant for resolution."""

    base_url: str = "."
    paths: dict[str, list[str]] = field(default_factory=dict)
    root_dir: str = "."
    out_dir: str | None = None

    @classmethod
    def load(cls, project_root: Path) -> TsConfig:
        """Load tsconfig.json from project root."""
        config_path = project_root / "tsconfig.json"
        if not config_path.exists():
            # Try jsconfig.json for JS projects
            config_path = project_root / "jsconfig.json"

        if not config_path.exists():
            return cls()

        try:
            with open(config_path) as f:
                data = json.load(f)

            compiler_opts = data.get("compilerOptions", {})
            return cls(
                base_url=compiler_opts.get("baseUrl", "."),
                paths=compiler_opts.get("paths", {}),
                root_dir=compiler_opts.get("rootDir", "."),
                out_dir=compiler_opts.get("outDir"),
            )
        except Exception as e:
            logger.warning("Failed to parse tsconfig.json: %s", e)
            return cls()


@dataclass
class ExportInfo:
    """Information about an export from a file."""

    name: str
    node_id: str
    file_path: str
    is_default: bool = False
    is_type: bool = False  # export type { X }


class TypeScriptResolver:
    """
    Cross-file resolution for TypeScript/JavaScript projects.

    Resolution strategy:
    1. Parse tsconfig.json for path aliases
    2. Track exports from all parsed files
    3. Resolve imports by matching to exports
    4. Cache results for performance
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._config = TsConfig.load(project_root)
        self._exports: dict[str, list[ExportInfo]] = {}  # file_path -> exports
        self._export_index: dict[str, ExportInfo] = {}  # "module.name" -> ExportInfo
        self._resolution_cache: dict[str, Resolution] = {}

        # Common external packages (don't try to resolve)
        self._external_packages = {
            "react",
            "react-dom",
            "next",
            "vue",
            "angular",
            "express",
            "lodash",
            "axios",
            "moment",
            "dayjs",
            "date-fns",
            "uuid",
            "zod",
            "yup",
        }

        logger.debug(
            "TypeScriptResolver initialized with baseUrl=%s, paths=%s",
            self._config.base_url,
            list(self._config.paths.keys()),
        )

    def register_exports(
        self, file_path: str, exports: list[dict[str, Any]]
    ) -> None:
        """Register exports from a parsed file.

        Args:
            file_path: Relative path from project root
            exports: List of export info dicts with keys:
                - name: exported name
                - node_id: full node ID in the graph
                - is_default: True if default export
                - is_type: True if type-only export
        """
        export_list = []
        for exp in exports:
            info = ExportInfo(
                name=exp["name"],
                node_id=exp["node_id"],
                file_path=file_path,
                is_default=exp.get("is_default", False),
                is_type=exp.get("is_type", False),
            )
            export_list.append(info)

            # Index by module.name for fast lookup
            module_id = self._file_to_module_id(file_path)
            key = f"{module_id}.{info.name}"
            self._export_index[key] = info

            # Also index default exports by module name only
            if info.is_default:
                self._export_index[module_id] = info

        self._exports[file_path] = export_list
        logger.debug("Registered %d exports from %s", len(export_list), file_path)

    def resolve(
        self,
        from_file: str,
        import_source: str,
        imported_name: str,
    ) -> Resolution:
        """Resolve an imported name to its definition.

        Args:
            from_file: File containing the import
            import_source: Import source (e.g., './utils', 'react', '@/lib/api')
            imported_name: Name being imported (e.g., 'useState', 'default')

        Returns:
            Resolution with node_id and confidence level
        """
        cache_key = f"{from_file}:{import_source}:{imported_name}"
        if cache_key in self._resolution_cache:
            return self._resolution_cache[cache_key]

        result = self._do_resolve(from_file, import_source, imported_name)
        self._resolution_cache[cache_key] = result
        return result

    def _do_resolve(
        self,
        from_file: str,
        import_source: str,
        imported_name: str,
    ) -> Resolution:
        """Internal resolution logic."""
        # Check if external package
        package_name = import_source.split("/")[0]
        if package_name in self._external_packages or not self._is_local_import(
            import_source
        ):
            return Resolution(
                node_id=f"{import_source}.{imported_name}",
                confidence=EdgeConfidence.EXTERNAL,
            )

        # Resolve module path
        resolved_path = self._resolve_module_path(import_source, from_file)
        if resolved_path is None:
            return Resolution(
                node_id=None,
                confidence=EdgeConfidence.UNRESOLVED,
                untracked_reason="module_not_found",
            )

        # Find export in resolved file
        module_id = self._file_to_module_id(resolved_path)

        # Try exact match first
        if imported_name == "default":
            key = module_id
        else:
            key = f"{module_id}.{imported_name}"

        if key in self._export_index:
            export = self._export_index[key]
            return Resolution(
                node_id=export.node_id,
                confidence=EdgeConfidence.RESOLVED,
            )

        # Try fuzzy match (name exists in file but not tracked as export)
        # This catches cases where we didn't parse exports correctly
        for exp in self._exports.get(resolved_path, []):
            if exp.name == imported_name:
                return Resolution(
                    node_id=exp.node_id,
                    confidence=EdgeConfidence.RESOLVED,
                )

        # Module exists but name not found - might be re-exported
        return Resolution(
            node_id=f"{module_id}.{imported_name}",
            confidence=EdgeConfidence.INFERRED,
            untracked_reason="export_not_found",
        )

    def _is_local_import(self, import_source: str) -> bool:
        """Check if import is local (not from node_modules)."""
        if import_source.startswith("."):
            return True
        if import_source.startswith("@/"):
            return True
        if import_source.startswith("~/"):
            return True
        # Check against tsconfig paths
        for pattern in self._config.paths:
            if import_source.startswith(pattern.rstrip("*")):
                return True
        return False

    def _resolve_module_path(
        self, import_source: str, from_file: str
    ) -> str | None:
        """Resolve import source to actual file path.

        Handles:
        - Relative imports: './utils', '../lib/helper'
        - Absolute imports via baseUrl: 'components/Button'
        - Path aliases: '@/lib/api', '~/utils'
        - Index files: './utils' -> './utils/index.ts'
        """
        from_dir = Path(from_file).parent

        # Handle path aliases first
        resolved = self._apply_path_aliases(import_source)
        if resolved != import_source:
            import_source = resolved

        # Handle relative imports
        if import_source.startswith("."):
            target = from_dir / import_source
        else:
            # Absolute import via baseUrl
            base = Path(self._config.base_url)
            target = base / import_source

        # Try to find the actual file
        return self._find_module_file(str(target))

    def _apply_path_aliases(self, import_source: str) -> str:
        """Apply tsconfig path aliases."""
        for pattern, replacements in self._config.paths.items():
            # Handle wildcard patterns like "@/*" -> ["src/*"]
            if pattern.endswith("*"):
                prefix = pattern[:-1]  # "@/"
                if import_source.startswith(prefix):
                    suffix = import_source[len(prefix) :]  # "lib/api"
                    if replacements:
                        replacement = replacements[0]
                        if replacement.endswith("*"):
                            return replacement[:-1] + suffix
                        return replacement + "/" + suffix
            elif import_source == pattern:
                if replacements:
                    return replacements[0]

        return import_source

    def _find_module_file(self, target: str) -> str | None:
        """Find actual file for a module path.

        Tries extensions and index files:
        - target.ts, target.tsx, target.js, target.jsx
        - target/index.ts, target/index.tsx, etc.

        Normalizes paths to resolve .. and . segments.
        """
        extensions = [".ts", ".tsx", ".js", ".jsx"]
        resolved_root = self._project_root.resolve()

        # Direct file match
        for ext in extensions:
            candidate = (self._project_root / f"{target}{ext}").resolve()
            try:
                if candidate.exists():
                    return str(candidate.relative_to(resolved_root))
            except ValueError:
                # Path is outside project root
                continue

        # Index file match
        for ext in extensions:
            candidate = (self._project_root / target / f"index{ext}").resolve()
            try:
                if candidate.exists():
                    return str(candidate.relative_to(resolved_root))
            except ValueError:
                continue

        # Already has extension
        target_path = Path(target)
        if target_path.suffix in extensions:
            candidate = (self._project_root / target).resolve()
            try:
                if candidate.exists():
                    return str(candidate.relative_to(resolved_root))
            except ValueError:
                pass

        return None

    def _file_to_module_id(self, file_path: str) -> str:
        """Convert file path to module-style ID."""
        path = Path(file_path)
        # Remove extension
        if path.suffix in (".ts", ".tsx", ".js", ".jsx"):
            path = path.with_suffix("")
        # Convert to dot notation
        parts = list(path.parts)
        # Handle index files
        if parts and parts[-1] == "index":
            parts = parts[:-1]
        return ".".join(parts) if parts else file_path

    def resolve_call(
        self,
        from_node: str,
        call_name: str,
        imports: dict[str, str],
    ) -> Resolution:
        """Resolve a function call to its definition.

        Args:
            from_node: Node ID of the caller (e.g., 'src.App.App')
            call_name: Name being called - can be:
                - Simple name: 'helper' (lookup in imports)
                - Import path: './utils/helpers.capitalize' (already resolved)
                - Method call: 'data.map'
            imports: Map of local_name -> import_source from the file

        Returns:
            Resolution with node_id and confidence level
        """
        # Extract from_file from from_node (convert dots to path)
        node_parts = from_node.split(".")
        if len(node_parts) > 1:
            # Remove the function name at the end (e.g., 'src.App.App' -> 'src/App')
            from_file = "/".join(node_parts[:-1])
        else:
            from_file = node_parts[0]

        # Check if call_name is already an import path (starts with './' or '../')
        if call_name.startswith("./") or call_name.startswith("../"):
            # Parse: './utils/helpers.capitalize' -> path='./utils/helpers', func='capitalize'
            last_segment = call_name.split("/")[-1]
            if "." in last_segment:
                # Last part has a dot, so it's path.function
                path_parts = call_name.rsplit(".", 1)
                import_source = path_parts[0]
                imported_name = path_parts[1] if len(path_parts) > 1 else "default"
            else:
                import_source = call_name
                imported_name = "default"

            # Resolve the import path to a module
            resolved_path = self._resolve_module_path(import_source, from_file + ".ts")
            if resolved_path:
                module_id = self._file_to_module_id(resolved_path)
                lookup_key = f"{module_id}.{imported_name}"

                if lookup_key in self._export_index:
                    return Resolution(
                        node_id=self._export_index[lookup_key].node_id,
                        confidence=EdgeConfidence.RESOLVED,
                    )

                # Check if it's in the exports list for that file
                for exp in self._exports.get(resolved_path, []):
                    if exp.name == imported_name:
                        return Resolution(
                            node_id=exp.node_id,
                            confidence=EdgeConfidence.RESOLVED,
                        )

            # Couldn't resolve fully
            return Resolution(
                node_id=call_name,
                confidence=EdgeConfidence.INFERRED,
                untracked_reason="import_path_not_resolved",
            )

        # Check if call is to an imported name (via imports dict)
        if call_name in imports:
            import_source = imports[call_name]
            return self.resolve(from_file, import_source, call_name)

        # Check if it's a method call like 'data.map' or 'react.useState'
        if "." in call_name:
            call_parts = call_name.split(".")
            first_part = call_parts[0]

            # Check if first part is an external package
            if first_part in self._external_packages:
                return Resolution(
                    node_id=call_name,
                    confidence=EdgeConfidence.EXTERNAL,
                )

            if first_part in imports:
                import_source = imports[first_part]
                return Resolution(
                    node_id=f"{import_source}.{call_name}",
                    confidence=EdgeConfidence.INFERRED,
                )

        # Check if defined in current module
        module_id = ".".join(from_node.split(".")[:-1]) if "." in from_node else from_node
        local_key = f"{module_id}.{call_name}"
        if local_key in self._export_index:
            return Resolution(
                node_id=self._export_index[local_key].node_id,
                confidence=EdgeConfidence.RESOLVED,
            )

        # Unresolved - could be built-in, external, or dynamic
        return Resolution(
            node_id=call_name,
            confidence=EdgeConfidence.INFERRED,
            untracked_reason="not_imported",
        )

    def clear_cache(self) -> None:
        """Clear resolution cache (call after file changes)."""
        self._resolution_cache.clear()

    def get_stats(self) -> dict[str, int]:
        """Get resolver statistics."""
        return {
            "tracked_files": len(self._exports),
            "total_exports": len(self._export_index),
            "cache_size": len(self._resolution_cache),
        }
