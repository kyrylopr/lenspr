"""Tests for lenspr/resolvers/pyright_resolver.py — PyrightResolver."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lenspr.models import Edge, EdgeConfidence, EdgeSource, EdgeType
from lenspr.resolvers.pyright_resolver import PyrightResolver, is_pyright_available

# Skip all tests if pyright is not installed
pytestmark = pytest.mark.skipif(
    not is_pyright_available(),
    reason="pyright-langserver not installed",
)


def _make_edge(
    from_node: str,
    to_node: str,
    line: int,
    col: int | None = None,
    confidence: EdgeConfidence = EdgeConfidence.INFERRED,
    edge_type: EdgeType = EdgeType.CALLS,
) -> Edge:
    return Edge(
        id=f"e_{from_node}_{to_node}",
        from_node=from_node,
        to_node=to_node,
        type=edge_type,
        line_number=line,
        column=col,
        confidence=confidence,
        source=EdgeSource.STATIC,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a minimal Python project for pyright to analyze."""
    (tmp_path / "utils.py").write_text(
        "def helper(x: int) -> str:\n"
        "    return str(x)\n"
    )

    (tmp_path / "models.py").write_text(
        "class User:\n"
        "    def __init__(self, name: str):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self) -> str:\n"
        '        return f"Hello, {self.name}"\n'
        "\n"
        "    def full_greeting(self) -> str:\n"
        "        return self.greet() + '!'\n"
    )

    (tmp_path / "service.py").write_text(
        "from utils import helper\n"
        "from models import User\n"
        "\n"
        "def create_greeting(name: str) -> str:\n"
        "    user = User(name)\n"
        "    result = helper(42)\n"
        "    return user.greet()\n"
    )
    return tmp_path


@pytest.fixture
def resolver(project: Path) -> PyrightResolver:
    r = PyrightResolver(project)
    yield r
    r.close()


# ---------------------------------------------------------------------------
# Tests — Name extraction helper
# ---------------------------------------------------------------------------


class TestExtractName:
    def test_function_def(self) -> None:
        assert PyrightResolver._extract_name_from_def("def foo(x):") == "foo"

    def test_async_function_def(self) -> None:
        assert PyrightResolver._extract_name_from_def("async def bar():") == "bar"

    def test_class_def(self) -> None:
        assert PyrightResolver._extract_name_from_def("class MyClass:") == "MyClass"

    def test_class_with_bases(self) -> None:
        assert PyrightResolver._extract_name_from_def("class Foo(Bar):") == "Foo"

    def test_variable_assignment(self) -> None:
        assert PyrightResolver._extract_name_from_def("x = 42") == "x"

    def test_type_annotated_variable(self) -> None:
        assert PyrightResolver._extract_name_from_def("x: int = 42") == "x"


# ---------------------------------------------------------------------------
# Tests — Integration with real pyright-langserver
# ---------------------------------------------------------------------------


class TestPyrightResolution:
    def test_resolves_module_function_call(
        self, resolver: PyrightResolver, project: Path
    ) -> None:
        """helper(42) in service.py → resolves to utils.helper."""
        edges = [
            _make_edge("service.create_greeting", "helper", line=6, col=13),
        ]
        resolver.resolve_edges(edges, str(project / "service.py"), settle_time=3)

        assert edges[0].confidence == EdgeConfidence.RESOLVED
        assert edges[0].to_node == "utils.helper"

    def test_resolves_self_method_call(
        self, resolver: PyrightResolver, project: Path
    ) -> None:
        """self.greet() in models.py → resolves to models.User.greet."""
        edges = [
            _make_edge(
                "models.User.full_greeting", "self.greet",
                line=9, col=20,
            ),
        ]
        resolver.resolve_edges(edges, str(project / "models.py"), settle_time=3)

        assert edges[0].confidence == EdgeConfidence.RESOLVED
        assert "greet" in edges[0].to_node
        # Should resolve to models.User.greet (or User.greet)

    def test_resolves_instance_method_call(
        self, resolver: PyrightResolver, project: Path
    ) -> None:
        """user.greet() in service.py → resolves to models.User.greet."""
        edges = [
            _make_edge(
                "service.create_greeting", "user.greet",
                line=7, col=11,
            ),
        ]
        resolver.resolve_edges(edges, str(project / "service.py"), settle_time=3)

        assert edges[0].confidence == EdgeConfidence.RESOLVED
        assert "greet" in edges[0].to_node

    def test_skips_already_resolved_edges(
        self, resolver: PyrightResolver, project: Path
    ) -> None:
        """Edges that are already RESOLVED are not touched."""
        edges = [
            _make_edge(
                "service.create_greeting", "utils.helper",
                line=6, col=13,
                confidence=EdgeConfidence.RESOLVED,
            ),
        ]
        resolver.resolve_edges(edges, str(project / "service.py"))
        assert edges[0].to_node == "utils.helper"
        assert edges[0].confidence == EdgeConfidence.RESOLVED

    def test_marks_external_stdlib(
        self, resolver: PyrightResolver, project: Path
    ) -> None:
        """Calls to stdlib functions are marked EXTERNAL."""
        # Add a file with a stdlib call
        (project / "stdlib_user.py").write_text(
            "import os\n"
            "\n"
            "def get_cwd() -> str:\n"
            "    return os.getcwd()\n"
        )
        edges = [
            _make_edge("stdlib_user.get_cwd", "os.getcwd", line=4, col=11),
        ]
        resolver.resolve_edges(edges, str(project / "stdlib_user.py"), settle_time=2)

        assert edges[0].confidence == EdgeConfidence.EXTERNAL

    def test_constructor_call(
        self, resolver: PyrightResolver, project: Path
    ) -> None:
        """User(name) in service.py → resolves to models.User."""
        edges = [
            _make_edge("service.create_greeting", "User", line=5, col=11),
        ]
        resolver.resolve_edges(edges, str(project / "service.py"), settle_time=3)

        assert edges[0].confidence == EdgeConfidence.RESOLVED
        assert "User" in edges[0].to_node


class TestPyrightAvailability:
    def test_is_pyright_available(self) -> None:
        """is_pyright_available reflects actual system state."""
        result = is_pyright_available()
        has_binary = shutil.which("pyright-langserver") is not None
        assert result == has_binary
