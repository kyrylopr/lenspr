"""Tests for TypeScript/JavaScript parser.

Tests cover:
1. Basic function parsing (regular, arrow, async)
2. Class parsing with methods
3. React components (function and class-based)
4. Import/export tracking
5. JSX component usage as calls
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip all tests if tree-sitter is not installed
ts = pytest.importorskip("tree_sitter", reason="tree-sitter not installed")


@pytest.fixture
def parser():
    """Create a TypeScript parser instance."""
    from lenspr.parsers.typescript_parser import TypeScriptParser

    return TypeScriptParser()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory."""
    return tmp_path


class TestBasicParsing:
    """Test basic JavaScript/TypeScript parsing."""

    def test_parse_function_declaration(self, parser, tmp_project: Path) -> None:
        """Parse a simple function declaration."""
        src = tmp_project / "utils.js"
        src.write_text(
            "function greet(name) {\n"
            "    return `Hello, ${name}`;\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Should have module + function
        assert len(nodes) >= 2
        func_nodes = [n for n in nodes if n.name == "greet"]
        assert len(func_nodes) == 1
        assert func_nodes[0].type.value == "function"

    def test_parse_arrow_function(self, parser, tmp_project: Path) -> None:
        """Parse an arrow function assigned to const."""
        src = tmp_project / "utils.js"
        src.write_text(
            "const add = (a, b) => {\n"
            "    return a + b;\n"
            "};\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        func_nodes = [n for n in nodes if n.name == "add"]
        assert len(func_nodes) == 1
        assert func_nodes[0].type.value == "function"

    def test_parse_async_function(self, parser, tmp_project: Path) -> None:
        """Parse an async function."""
        src = tmp_project / "api.js"
        src.write_text(
            "async function fetchData(url) {\n"
            "    const response = await fetch(url);\n"
            "    return response.json();\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        func_nodes = [n for n in nodes if n.name == "fetchData"]
        assert len(func_nodes) == 1
        assert func_nodes[0].metadata.get("is_async") is True


class TestClassParsing:
    """Test class and method parsing."""

    def test_parse_class(self, parser, tmp_project: Path) -> None:
        """Parse a class with methods."""
        src = tmp_project / "user.ts"
        src.write_text(
            "class User {\n"
            "    constructor(name: string) {\n"
            "        this.name = name;\n"
            "    }\n"
            "\n"
            "    greet() {\n"
            "        return `Hello, ${this.name}`;\n"
            "    }\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Should have class and methods (filter out module)
        class_nodes = [n for n in nodes if n.name == "User" and n.type.value == "class"]
        assert len(class_nodes) == 1

        method_nodes = [
            n for n in nodes
            if n.name in ("constructor", "greet") and n.type.value == "method"
        ]
        assert len(method_nodes) == 2

    def test_parse_class_inheritance(self, parser, tmp_project: Path) -> None:
        """Parse class extending another class."""
        src = tmp_project / "admin.ts"
        src.write_text(
            "class Admin extends User {\n"
            "    isAdmin() {\n"
            "        return true;\n"
            "    }\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        class_nodes = [n for n in nodes if n.name == "Admin"]
        assert len(class_nodes) == 1
        assert class_nodes[0].metadata.get("extends") == "User"

        # Should have inheritance edge
        inherit_edges = [e for e in edges if e.type.value == "inherits"]
        assert len(inherit_edges) == 1
        assert inherit_edges[0].to_node == "User"


class TestImportTracking:
    """Test import statement tracking."""

    def test_parse_named_imports(self, parser, tmp_project: Path) -> None:
        """Parse named imports."""
        src = tmp_project / "app.ts"
        src.write_text(
            "import { useState, useEffect } from 'react';\n"
            "\n"
            "function App() {\n"
            "    const [count, setCount] = useState(0);\n"
            "    return count;\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Should have import edge
        import_edges = [e for e in edges if e.type.value == "imports"]
        assert len(import_edges) >= 1
        assert any(e.to_node == "react" for e in import_edges)

    def test_parse_default_import(self, parser, tmp_project: Path) -> None:
        """Parse default import."""
        src = tmp_project / "app.ts"
        src.write_text(
            "import React from 'react';\n"
            "\n"
            "function App() {\n"
            "    return React.createElement('div');\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        import_edges = [e for e in edges if e.type.value == "imports"]
        assert len(import_edges) >= 1


class TestReactComponents:
    """Test React component parsing."""

    def test_parse_function_component(self, parser, tmp_project: Path) -> None:
        """Parse a React function component."""
        src = tmp_project / "Button.tsx"
        src.write_text(
            "function Button({ onClick, children }) {\n"
            "    return <button onClick={onClick}>{children}</button>;\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Filter to function type only (not module)
        func_nodes = [n for n in nodes if n.name == "Button" and n.type.value == "function"]
        assert len(func_nodes) == 1
        # PascalCase + returns JSX = React component
        assert func_nodes[0].metadata.get("is_react_component") is True

    def test_parse_arrow_component(self, parser, tmp_project: Path) -> None:
        """Parse a React arrow function component."""
        src = tmp_project / "Card.tsx"
        src.write_text(
            "const Card = ({ title, children }) => {\n"
            "    return (\n"
            "        <div className='card'>\n"
            "            <h2>{title}</h2>\n"
            "            {children}\n"
            "        </div>\n"
            "    );\n"
            "};\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Filter to function type only (not module)
        func_nodes = [n for n in nodes if n.name == "Card" and n.type.value == "function"]
        assert len(func_nodes) == 1
        assert func_nodes[0].metadata.get("is_react_component") is True

    def test_parse_class_component(self, parser, tmp_project: Path) -> None:
        """Parse a React class component."""
        src = tmp_project / "Counter.tsx"
        src.write_text(
            "import React from 'react';\n"
            "\n"
            "class Counter extends React.Component {\n"
            "    render() {\n"
            "        return <div>{this.props.count}</div>;\n"
            "    }\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Filter to class type only (not module)
        class_nodes = [n for n in nodes if n.name == "Counter" and n.type.value == "class"]
        assert len(class_nodes) == 1
        # extends React.Component = React class component
        assert class_nodes[0].metadata.get("is_react_component") is True

    def test_jsx_component_usage_creates_edge(self, parser, tmp_project: Path) -> None:
        """Using a component in JSX should create a call edge."""
        src = tmp_project / "App.tsx"
        src.write_text(
            "import Button from './Button';\n"
            "\n"
            "function App() {\n"
            "    return (\n"
            "        <div>\n"
            "            <Button onClick={() => {}} />\n"
            "        </div>\n"
            "    );\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Should have call edge from App to Button
        call_edges = [e for e in edges if e.type.value == "calls"]
        button_calls = [e for e in call_edges if "Button" in e.to_node]
        assert len(button_calls) >= 1


class TestFunctionCalls:
    """Test function call edge extraction."""

    def test_simple_call(self, parser, tmp_project: Path) -> None:
        """Parse a simple function call."""
        src = tmp_project / "app.js"
        src.write_text(
            "function helper() {\n"
            "    return 42;\n"
            "}\n"
            "\n"
            "function main() {\n"
            "    return helper();\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        call_edges = [e for e in edges if e.type.value == "calls"]
        helper_calls = [e for e in call_edges if "helper" in e.to_node]
        assert len(helper_calls) >= 1

    def test_method_call(self, parser, tmp_project: Path) -> None:
        """Parse method calls."""
        src = tmp_project / "app.js"
        src.write_text(
            "function process(data) {\n"
            "    return data.map(x => x * 2).filter(x => x > 5);\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        call_edges = [e for e in edges if e.type.value == "calls"]
        # Should have calls to map and filter
        assert len(call_edges) >= 2


class TestMultiParser:
    """Test that MultiParser correctly delegates to TypeScriptParser."""

    def test_multi_parser_handles_js(self, tmp_project: Path) -> None:
        """MultiParser should handle .js files."""
        from lenspr.parsers.multi import MultiParser

        parser = MultiParser()

        src = tmp_project / "app.js"
        src.write_text("function hello() { return 'world'; }\n")

        nodes, edges = parser.parse_file(src, tmp_project)

        assert len(nodes) >= 2  # module + function
        func_nodes = [n for n in nodes if n.name == "hello"]
        assert len(func_nodes) == 1

    def test_multi_parser_handles_tsx(self, tmp_project: Path) -> None:
        """MultiParser should handle .tsx files."""
        from lenspr.parsers.multi import MultiParser

        parser = MultiParser()

        src = tmp_project / "App.tsx"
        src.write_text(
            "function App() {\n"
            "    return <div>Hello</div>;\n"
            "}\n"
        )

        nodes, edges = parser.parse_file(src, tmp_project)

        # Filter to function type only (not module)
        func_nodes = [n for n in nodes if n.name == "App" and n.type.value == "function"]
        assert len(func_nodes) == 1

    def test_multi_parser_extensions(self) -> None:
        """MultiParser should support both Python and JS/TS extensions."""
        from lenspr.parsers.multi import MultiParser

        parser = MultiParser()
        extensions = parser.get_file_extensions()

        # Python
        assert ".py" in extensions

        # JavaScript/TypeScript
        assert ".js" in extensions
        assert ".jsx" in extensions
        assert ".ts" in extensions
        assert ".tsx" in extensions


class TestEvalProjectBaseline:
    """Behavioral regression: parse the eval test project and verify counts.

    Baseline captured 2026-02-19 after _TreeSitterVisitor refactor:
    10 TS/TSX files → 43 nodes, 56 edges.
    """

    EVAL_ROOT = Path("eval/test_projects/with_lenspr/taskflow")

    @pytest.fixture
    def eval_result(self, parser):
        if not self.EVAL_ROOT.exists():
            pytest.skip("eval test project not present")
        ts_files = sorted(self.EVAL_ROOT.rglob("*.ts")) + sorted(
            self.EVAL_ROOT.rglob("*.tsx")
        )
        all_nodes, all_edges = [], []
        for f in ts_files:
            nodes, edges = parser.parse_file(f, self.EVAL_ROOT)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        return all_nodes, all_edges

    def test_total_file_count(self, parser):
        if not self.EVAL_ROOT.exists():
            pytest.skip("eval test project not present")
        ts_files = sorted(self.EVAL_ROOT.rglob("*.ts")) + sorted(
            self.EVAL_ROOT.rglob("*.tsx")
        )
        assert len(ts_files) == 10

    def test_total_node_count(self, eval_result):
        nodes, _ = eval_result
        assert len(nodes) == 43, f"Expected 43 nodes, got {len(nodes)}"

    def test_total_edge_count(self, eval_result):
        _, edges = eval_result
        assert len(edges) == 56, f"Expected 56 edges, got {len(edges)}"

    def test_auth_api_nodes(self, eval_result):
        """frontend/api/auth.ts should produce 7 nodes: module + class + 5 methods."""
        nodes, _ = eval_result
        auth_nodes = [n for n in nodes if n.file_path == "frontend/api/auth.ts"]
        assert len(auth_nodes) == 7, (
            f"Expected 7 nodes in auth.ts, got {len(auth_nodes)}: "
            f"{[n.name for n in auth_nodes]}"
        )
        assert any(n.name == "AuthApi" and n.type.value == "class" for n in auth_nodes)

    def test_use_auth_hook_nodes(self, eval_result):
        """frontend/hooks/useAuth.ts should produce 5 nodes: module + useAuth + 3 nested."""
        nodes, _ = eval_result
        hook_nodes = [n for n in nodes if n.file_path == "frontend/hooks/useAuth.ts"]
        assert len(hook_nodes) == 5, (
            f"Expected 5 nodes in useAuth.ts, got {len(hook_nodes)}: "
            f"{[n.name for n in hook_nodes]}"
        )
        names = {n.name for n in hook_nodes}
        assert {"useAuth", "login", "logout", "getUser"} <= names

    def test_task_card_component(self, eval_result):
        """frontend/components/TaskCard.tsx should produce 3 nodes."""
        nodes, _ = eval_result
        card_nodes = [
            n for n in nodes if n.file_path == "frontend/components/TaskCard.tsx"
        ]
        assert len(card_nodes) == 3
        func_nodes = [
            n for n in card_nodes
            if n.name == "TaskCard" and n.type.value == "function"
        ]
        assert len(func_nodes) == 1


class TestErrorTolerance:
    """Test that parser handles errors gracefully."""

    def test_syntax_error_partial_parse(self, parser, tmp_project: Path) -> None:
        """Parser should handle files with syntax errors."""
        src = tmp_project / "broken.js"
        src.write_text(
            "function valid() {\n"
            "    return 1;\n"
            "}\n"
            "\n"
            "function broken( {\n"  # Syntax error
            "    return 2;\n"
            "}\n"
        )

        # Should not raise - tree-sitter is error-tolerant
        nodes, edges = parser.parse_file(src, tmp_project)

        # Should still extract the valid function
        func_nodes = [n for n in nodes if n.name == "valid"]
        assert len(func_nodes) == 1

    def test_empty_file(self, parser, tmp_project: Path) -> None:
        """Parser should handle empty files."""
        src = tmp_project / "empty.js"
        src.write_text("")

        nodes, edges = parser.parse_file(src, tmp_project)

        # Should have at least the module node
        assert len(nodes) >= 1
