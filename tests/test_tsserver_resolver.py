"""Tests for lenspr/resolvers/tsserver_resolver.py — TsServerResolver."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lenspr.models import Edge, EdgeConfidence, EdgeSource, EdgeType
from lenspr.resolvers.tsserver_resolver import (
    TsServerResolver,
    _is_external,
    is_tsserver_available,
)

# Skip all tests if typescript-language-server is not available
pytestmark = pytest.mark.skipif(
    not is_tsserver_available(),
    reason="typescript-language-server not available (no npx or global install)",
)


def _make_edge(
    from_node: str,
    to_node: str,
    line: int,
    file_path: str = "",
    col: int | None = None,
    confidence: EdgeConfidence = EdgeConfidence.INFERRED,
    edge_type: EdgeType = EdgeType.CALLS,
) -> Edge:
    metadata = {"file": file_path}
    if col is not None:
        metadata["column"] = col
    return Edge(
        id=f"e_{from_node}_{to_node}",
        from_node=from_node,
        to_node=to_node,
        type=edge_type,
        line_number=line,
        confidence=confidence,
        source=EdgeSource.STATIC,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ensure_typescript(project_dir: Path) -> bool:
    """Symlink or copy typescript into project node_modules.

    Returns True if typescript is available for tsserver.
    """
    import subprocess

    node_modules = project_dir / "node_modules"
    node_modules.mkdir(exist_ok=True)

    # Try to find typescript in common locations
    for candidate in [
        Path(__file__).resolve().parent.parent / "node_modules" / "typescript",
    ]:
        if candidate.is_dir():
            target = node_modules / "typescript"
            if not target.exists():
                target.symlink_to(candidate)
            return True

    # Fallback: npm install (slow but works in CI)
    try:
        subprocess.run(
            ["npm", "init", "-y"],
            cwd=project_dir, capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["npm", "install", "typescript"],
            cwd=project_dir, capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a minimal TypeScript project for tsserver to analyze."""
    # tsconfig.json is required for tsserver to work
    (tmp_path / "tsconfig.json").write_text(
        '{\n'
        '  "compilerOptions": {\n'
        '    "target": "ES2020",\n'
        '    "module": "commonjs",\n'
        '    "strict": true\n'
        '  }\n'
        '}\n'
    )

    (tmp_path / "utils.ts").write_text(
        "export function helper(x: number): string {\n"
        "    return String(x);\n"
        "}\n"
        "\n"
        "export function formatName(name: string): string {\n"
        '    return name.trim().toLowerCase();\n'
        "}\n"
    )

    (tmp_path / "models.ts").write_text(
        "export class User {\n"
        "    constructor(public name: string) {}\n"
        "\n"
        "    greet(): string {\n"
        '        return `Hello, ${this.name}`;\n'
        "    }\n"
        "}\n"
    )

    (tmp_path / "service.ts").write_text(
        'import { helper } from "./utils";\n'
        'import { User } from "./models";\n'
        "\n"
        "export function createGreeting(name: string): string {\n"
        "    const user = new User(name);\n"
        "    const result = helper(42);\n"
        "    return user.greet();\n"
        "}\n"
    )

    # typescript-language-server requires typescript to be installed
    if not _ensure_typescript(tmp_path):
        pytest.skip("typescript package not available")

    return tmp_path


@pytest.fixture
def resolver(project: Path) -> TsServerResolver:
    r = TsServerResolver(project)
    yield r
    r.close()


# ---------------------------------------------------------------------------
# Tests — Unit: _is_external
# ---------------------------------------------------------------------------


class TestIsExternal:
    def test_react(self) -> None:
        assert _is_external("react") is True

    def test_fs(self) -> None:
        assert _is_external("fs") is True

    def test_lodash(self) -> None:
        assert _is_external("lodash") is True

    def test_project_module(self) -> None:
        assert _is_external("service") is False

    def test_empty(self) -> None:
        assert _is_external("") is False

    def test_dotted_external(self) -> None:
        assert _is_external("react.createElement") is True

    def test_dotted_internal(self) -> None:
        assert _is_external("models.User") is False


# ---------------------------------------------------------------------------
# Tests — Unit: _extract_name_from_def
# ---------------------------------------------------------------------------


class TestExtractName:
    def test_function(self) -> None:
        assert TsServerResolver._extract_name_from_def("function foo() {") == "foo"

    def test_async_function(self) -> None:
        assert TsServerResolver._extract_name_from_def("async function bar() {") == "bar"

    def test_export_function(self) -> None:
        assert TsServerResolver._extract_name_from_def("export function baz() {") == "baz"

    def test_class(self) -> None:
        assert TsServerResolver._extract_name_from_def("class MyClass {") == "MyClass"

    def test_export_class(self) -> None:
        assert TsServerResolver._extract_name_from_def("export class User {") == "User"

    def test_interface(self) -> None:
        assert TsServerResolver._extract_name_from_def("interface Props {") == "Props"

    def test_type_alias(self) -> None:
        assert TsServerResolver._extract_name_from_def("type Result = string | number") == "Result"

    def test_const(self) -> None:
        assert TsServerResolver._extract_name_from_def("const MAX = 100") == "MAX"

    def test_export_const(self) -> None:
        assert TsServerResolver._extract_name_from_def("export const helper = (") == "helper"

    def test_arrow_function(self) -> None:
        assert TsServerResolver._extract_name_from_def("greet = (name: string) =>") == "greet"

    def test_method(self) -> None:
        assert TsServerResolver._extract_name_from_def("greet() {") == "greet"

    def test_async_method(self) -> None:
        assert TsServerResolver._extract_name_from_def("async fetchData() {") == "fetchData"

    def test_ignores_keywords(self) -> None:
        assert TsServerResolver._extract_name_from_def("if (x) {") is None
        assert TsServerResolver._extract_name_from_def("for (let i = 0;") is None
        assert TsServerResolver._extract_name_from_def("while (true) {") is None

    def test_no_match(self) -> None:
        assert TsServerResolver._extract_name_from_def("  // comment") is None


# ---------------------------------------------------------------------------
# Tests — Unit: _external_module_id
# ---------------------------------------------------------------------------


class TestExternalModuleId:
    def test_regular_package(self) -> None:
        result = TsServerResolver._external_module_id(
            "/project/node_modules/lodash/index.js"
        )
        assert result == "lodash"

    def test_scoped_package(self) -> None:
        result = TsServerResolver._external_module_id(
            "/project/node_modules/@types/react/index.d.ts"
        )
        assert result == "@types/react"

    def test_no_node_modules(self) -> None:
        result = TsServerResolver._external_module_id(
            "/project/src/utils.ts"
        )
        assert result is None

    def test_scoped_single_part(self) -> None:
        result = TsServerResolver._external_module_id(
            "/project/node_modules/@scope"
        )
        assert result == "@scope"


# ---------------------------------------------------------------------------
# Tests — Integration with real typescript-language-server
# ---------------------------------------------------------------------------


class TestTsServerResolution:
    def test_resolves_imported_function(
        self, resolver: TsServerResolver, project: Path
    ) -> None:
        """helper(42) in service.ts → resolves to utils.helper."""
        service_file = str(project / "service.ts")
        edges = [
            _make_edge(
                "service.createGreeting", "helper",
                line=6, file_path=service_file, col=20,
            ),
        ]
        resolved = resolver.resolve_edges(edges, settle_time=3)

        assert resolved[0].confidence in (
            EdgeConfidence.RESOLVED, EdgeConfidence.EXTERNAL,
        )
        if resolved[0].confidence == EdgeConfidence.RESOLVED:
            assert "helper" in resolved[0].to_node

    def test_resolves_class_constructor(
        self, resolver: TsServerResolver, project: Path
    ) -> None:
        """new User(name) in service.ts → resolves to models.User."""
        service_file = str(project / "service.ts")
        edges = [
            _make_edge(
                "service.createGreeting", "User",
                line=5, file_path=service_file, col=22,
            ),
        ]
        resolved = resolver.resolve_edges(edges, settle_time=3)

        assert resolved[0].confidence in (
            EdgeConfidence.RESOLVED, EdgeConfidence.EXTERNAL,
        )
        if resolved[0].confidence == EdgeConfidence.RESOLVED:
            assert "User" in resolved[0].to_node

    def test_resolves_method_call(
        self, resolver: TsServerResolver, project: Path
    ) -> None:
        """user.greet() in service.ts → resolves to models.User.greet."""
        service_file = str(project / "service.ts")
        edges = [
            _make_edge(
                "service.createGreeting", "user.greet",
                line=7, file_path=service_file, col=16,
            ),
        ]
        resolved = resolver.resolve_edges(edges, settle_time=3)

        assert resolved[0].confidence in (
            EdgeConfidence.RESOLVED, EdgeConfidence.EXTERNAL,
        )
        if resolved[0].confidence == EdgeConfidence.RESOLVED:
            assert "greet" in resolved[0].to_node

    def test_skips_already_resolved_edges(
        self, resolver: TsServerResolver, project: Path
    ) -> None:
        """Edges that are already RESOLVED are not touched."""
        service_file = str(project / "service.ts")
        edges = [
            _make_edge(
                "service.createGreeting", "utils.helper",
                line=6, file_path=service_file,
                confidence=EdgeConfidence.RESOLVED,
            ),
        ]
        resolved = resolver.resolve_edges(edges)

        assert resolved[0].to_node == "utils.helper"
        assert resolved[0].confidence == EdgeConfidence.RESOLVED

    def test_marks_external_as_external(
        self, resolver: TsServerResolver, project: Path
    ) -> None:
        """Calls to known external packages are marked EXTERNAL."""
        service_file = str(project / "service.ts")
        edges = [
            _make_edge(
                "service.createGreeting", "react.createElement",
                line=1, file_path=service_file,
            ),
        ]
        resolved = resolver.resolve_edges(edges)

        assert resolved[0].confidence == EdgeConfidence.EXTERNAL

    def test_no_inferred_edges_returns_unchanged(
        self, resolver: TsServerResolver, project: Path
    ) -> None:
        """When there are no INFERRED CALLS, return edges unchanged."""
        service_file = str(project / "service.ts")
        edges = [
            _make_edge(
                "service.foo", "service.bar",
                line=1, file_path=service_file,
                confidence=EdgeConfidence.RESOLVED,
            ),
            _make_edge(
                "service.foo", "service.baz",
                line=2, file_path=service_file,
                edge_type=EdgeType.IMPORTS,
                confidence=EdgeConfidence.INFERRED,
            ),
        ]
        resolved = resolver.resolve_edges(edges)

        assert len(resolved) == 2
        assert resolved[0].confidence == EdgeConfidence.RESOLVED
        assert resolved[1].confidence == EdgeConfidence.INFERRED


class TestTsServerAvailability:
    def test_is_tsserver_available(self) -> None:
        """is_tsserver_available reflects actual system state."""
        result = is_tsserver_available()
        has_tsserver = shutil.which("typescript-language-server") is not None
        has_npx = shutil.which("npx") is not None
        assert result == (has_tsserver or has_npx)

    def test_find_command(self) -> None:
        """_find_command returns a valid command list."""
        cmd = TsServerResolver._find_command()
        if is_tsserver_available():
            assert cmd is not None
            assert "--stdio" in cmd
        else:
            assert cmd is None


class TestTsServerLifecycle:
    def test_context_manager(self, project: Path) -> None:
        """Context manager properly starts and shuts down."""
        with TsServerResolver(project) as resolver:
            assert resolver._initialized is False  # lazy start
            # Trigger start
            service_file = str(project / "service.ts")
            edges = [
                _make_edge(
                    "service.foo", "helper",
                    line=6, file_path=service_file,
                ),
            ]
            resolver.resolve_edges(edges, settle_time=2)
            assert resolver._initialized is True

        # After context exit
        assert resolver._initialized is False

    def test_close_idempotent(self, project: Path) -> None:
        """Calling close multiple times is safe."""
        resolver = TsServerResolver(project)
        resolver.close()
        resolver.close()  # Should not raise
