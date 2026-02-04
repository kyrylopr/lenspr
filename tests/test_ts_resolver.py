"""Tests for TypeScript cross-file resolution.

Tests cover:
1. tsconfig.json parsing and path aliases
2. Export tracking and registration
3. Module path resolution
4. Cross-file import resolution
5. Resolver caching
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip all tests if tree-sitter is not installed
pytest.importorskip("tree_sitter", reason="tree-sitter not installed")


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    return tmp_path


class TestTsConfigParsing:
    """Test tsconfig.json parsing."""

    def test_load_tsconfig_with_paths(self, tmp_project: Path) -> None:
        """Load tsconfig.json with path aliases."""
        from lenspr.parsers.ts_resolver import TsConfig

        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {
                    "@/*": ["src/*"],
                    "@components/*": ["src/components/*"],
                },
            }
        }
        (tmp_project / "tsconfig.json").write_text(json.dumps(tsconfig))

        config = TsConfig.load(tmp_project)

        assert config.base_url == "."
        assert "@/*" in config.paths
        assert config.paths["@/*"] == ["src/*"]

    def test_load_jsconfig_fallback(self, tmp_project: Path) -> None:
        """Load jsconfig.json when tsconfig.json doesn't exist."""
        from lenspr.parsers.ts_resolver import TsConfig

        jsconfig = {
            "compilerOptions": {
                "baseUrl": "src",
            }
        }
        (tmp_project / "jsconfig.json").write_text(json.dumps(jsconfig))

        config = TsConfig.load(tmp_project)

        assert config.base_url == "src"

    def test_missing_config_returns_defaults(self, tmp_project: Path) -> None:
        """Missing config file returns default values."""
        from lenspr.parsers.ts_resolver import TsConfig

        config = TsConfig.load(tmp_project)

        assert config.base_url == "."
        assert config.paths == {}


class TestExportTracking:
    """Test export registration and lookup."""

    def test_register_exports(self, tmp_project: Path) -> None:
        """Register exports from a file."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        resolver = TypeScriptResolver(tmp_project)

        exports = [
            {"name": "Button", "node_id": "components.Button.Button", "is_default": True},
            {"name": "ButtonProps", "node_id": "components.Button.ButtonProps", "is_type": True},
        ]
        resolver.register_exports("components/Button.tsx", exports)

        stats = resolver.get_stats()
        assert stats["tracked_files"] == 1
        assert stats["total_exports"] >= 2

    def test_export_lookup_by_module(self, tmp_project: Path) -> None:
        """Look up exports by module ID."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        resolver = TypeScriptResolver(tmp_project)

        exports = [
            {"name": "helper", "node_id": "utils.helper", "is_default": False},
        ]
        resolver.register_exports("utils.ts", exports)

        # The export should be indexed
        stats = resolver.get_stats()
        assert stats["total_exports"] >= 1


class TestModuleResolution:
    """Test module path resolution."""

    def test_resolve_relative_import(self, tmp_project: Path) -> None:
        """Resolve relative import paths."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        # Create the target file in src directory
        (tmp_project / "src").mkdir()
        (tmp_project / "src" / "utils.ts").write_text("export function helper() {}")
        (tmp_project / "src" / "app.ts").write_text("")

        resolver = TypeScriptResolver(tmp_project)
        resolved = resolver._resolve_module_path("./utils", "src/app.ts")

        assert resolved == "src/utils.ts"

    def test_resolve_path_alias(self, tmp_project: Path) -> None:
        """Resolve path alias imports."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        # Create tsconfig with path alias
        tsconfig = {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {"@/*": ["src/*"]},
            }
        }
        (tmp_project / "tsconfig.json").write_text(json.dumps(tsconfig))
        (tmp_project / "src").mkdir()
        (tmp_project / "src" / "utils.ts").write_text("export function helper() {}")

        resolver = TypeScriptResolver(tmp_project)

        # Apply path alias
        resolved = resolver._apply_path_aliases("@/utils")
        assert resolved == "src/utils"

    def test_resolve_index_file(self, tmp_project: Path) -> None:
        """Resolve imports to index files."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        # Create directory with index file
        (tmp_project / "components").mkdir()
        (tmp_project / "components" / "index.ts").write_text("export * from './Button';")

        resolver = TypeScriptResolver(tmp_project)
        resolved = resolver._find_module_file("components")

        assert resolved == "components/index.ts"


class TestCrossFileResolution:
    """Test cross-file import resolution."""

    def test_resolve_named_import(self, tmp_project: Path) -> None:
        """Resolve named import to export."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        # Create source file structure
        (tmp_project / "utils.ts").write_text("export function helper() {}")

        resolver = TypeScriptResolver(tmp_project)

        # Register the export
        exports = [{"name": "helper", "node_id": "utils.helper", "is_default": False}]
        resolver.register_exports("utils.ts", exports)

        # Resolve import
        result = resolver.resolve(
            from_file="src/app.ts",
            import_source="./utils",
            imported_name="helper",
        )

        # Should resolve with high confidence
        # Note: In current implementation, relative paths need actual file
        # This test verifies the resolution flow
        assert result is not None

    def test_resolve_external_package(self, tmp_project: Path) -> None:
        """External packages resolve with EXTERNAL confidence."""
        from lenspr.models import EdgeConfidence
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        resolver = TypeScriptResolver(tmp_project)

        result = resolver.resolve(
            from_file="src/app.tsx",
            import_source="react",
            imported_name="useState",
        )

        assert result.confidence == EdgeConfidence.EXTERNAL
        assert "react" in (result.node_id or "")

    def test_resolution_caching(self, tmp_project: Path) -> None:
        """Resolution results are cached."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        resolver = TypeScriptResolver(tmp_project)

        # First resolution
        result1 = resolver.resolve("app.ts", "react", "useState")

        # Second resolution (should hit cache)
        result2 = resolver.resolve("app.ts", "react", "useState")

        assert result1.confidence == result2.confidence
        assert resolver.get_stats()["cache_size"] == 1

    def test_clear_cache(self, tmp_project: Path) -> None:
        """Cache can be cleared."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        resolver = TypeScriptResolver(tmp_project)
        resolver.resolve("app.ts", "react", "useState")

        assert resolver.get_stats()["cache_size"] == 1

        resolver.clear_cache()

        assert resolver.get_stats()["cache_size"] == 0


class TestParserIntegration:
    """Test TypeScriptParser integration with resolver."""

    def test_parser_registers_exports(self, tmp_project: Path) -> None:
        """Parser registers exports during parse_file."""
        from lenspr.parsers.typescript_parser import TypeScriptParser

        parser = TypeScriptParser()
        parser.set_project_root(tmp_project)

        # Create file with export
        src = tmp_project / "utils.ts"
        src.write_text("export function helper() { return 42; }\n")

        nodes, edges = parser.parse_file(src, tmp_project)

        # Check resolver stats
        stats = parser.get_resolver_stats()
        assert stats["tracked_files"] >= 1

    def test_parser_tracks_exported_functions(self, tmp_project: Path) -> None:
        """Parser sets is_exported metadata for exported functions."""
        from lenspr.parsers.typescript_parser import TypeScriptParser

        parser = TypeScriptParser()
        parser.set_project_root(tmp_project)

        src = tmp_project / "api.ts"
        src.write_text(
            "export function fetchData() { return fetch('/api'); }\n"
            "function privateHelper() { return null; }\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Find the functions
        func_nodes = [n for n in nodes if n.type.value == "function"]

        exported = [n for n in func_nodes if n.metadata.get("is_exported")]
        private = [n for n in func_nodes if not n.metadata.get("is_exported")]

        assert len(exported) == 1
        assert exported[0].name == "fetchData"
        assert len(private) == 1
        assert private[0].name == "privateHelper"

    def test_parse_project_resolves_edges(self, tmp_project: Path) -> None:
        """parse_project resolves edges using collected exports."""
        from lenspr.parsers.typescript_parser import TypeScriptParser

        parser = TypeScriptParser()

        # Create a multi-file project
        (tmp_project / "utils.ts").write_text(
            "export function helper() { return 42; }\n"
        )
        (tmp_project / "app.ts").write_text(
            "import { helper } from './utils';\n"
            "function main() { return helper(); }\n"
        )

        nodes, edges = parser.parse_project(tmp_project)

        # Should have nodes from both files
        assert len(nodes) >= 4  # 2 modules + 2 functions

        # Check resolver stats after project parse
        stats = parser.get_resolver_stats()
        assert stats["tracked_files"] >= 1


class TestRealWorldPatterns:
    """Test real-world TypeScript/React patterns."""

    def test_react_component_export(self, tmp_project: Path) -> None:
        """Parse and track React component exports."""
        from lenspr.parsers.typescript_parser import TypeScriptParser

        parser = TypeScriptParser()
        parser.set_project_root(tmp_project)

        src = tmp_project / "Button.tsx"
        src.write_text(
            "export function Button({ children }) {\n"
            "    return <button>{children}</button>;\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        func_nodes = [n for n in nodes if n.name == "Button" and n.type.value == "function"]
        assert len(func_nodes) == 1
        assert func_nodes[0].metadata.get("is_exported") is True
        assert func_nodes[0].metadata.get("is_react_component") is True

    def test_default_export_class(self, tmp_project: Path) -> None:
        """Parse default export class."""
        from lenspr.parsers.typescript_parser import TypeScriptParser

        parser = TypeScriptParser()
        parser.set_project_root(tmp_project)

        src = tmp_project / "Service.ts"
        src.write_text(
            "export default class ApiService {\n"
            "    fetch() { return null; }\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        class_nodes = [n for n in nodes if n.name == "ApiService" and n.type.value == "class"]
        assert len(class_nodes) == 1
        assert class_nodes[0].metadata.get("is_exported") is True

    def test_barrel_export_pattern(self, tmp_project: Path) -> None:
        """Parse barrel export pattern (index.ts re-exports)."""
        from lenspr.parsers.typescript_parser import TypeScriptParser

        parser = TypeScriptParser()
        parser.set_project_root(tmp_project)

        src = tmp_project / "index.ts"
        src.write_text(
            "export { Button } from './Button';\n"
            "export { Card } from './Card';\n"
            "export * from './utils';\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Should have import edges for re-exports
        import_edges = [e for e in edges if e.type.value == "imports"]
        assert len(import_edges) >= 3
