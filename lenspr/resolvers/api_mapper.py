"""Cross-language API mapper.

Detects backend route definitions (Python) and frontend HTTP calls (TypeScript/JS),
then creates CALLS_API edges connecting them across language boundaries.

Patterns recognized:

Backend (Python):
  - @app.get("/api/users")           → FastAPI/Flask decorator
  - @router.post("/api/auth/login")  → FastAPI APIRouter
  - route_map = {"/api/x": handler}  → dict-based routing

Frontend (TypeScript/JavaScript):
  - fetch("/api/users")              → Fetch API
  - fetch(`/api/users/${id}`)        → Template literal
  - axios.get("/api/users")          → Axios
  - this.client.post("/api/auth")    → Class-based API client
  - apiRequest("/api/chat")          → Custom wrapper
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from lenspr.models import Edge, EdgeConfidence, EdgeSource, EdgeType, Node

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RouteInfo:
    """A backend route definition."""

    method: str  # GET, POST, PUT, DELETE, ANY
    path: str  # /api/users, /api/auth/login
    handler_node_id: str  # Node ID of the handler function
    file_path: str
    line: int


@dataclass
class ApiCallInfo:
    """A frontend HTTP API call."""

    method: str  # GET, POST, PUT, DELETE, ANY
    path: str  # /api/users, /api/auth/${userId}
    caller_node_id: str  # Node ID of the calling function
    file_path: str
    line: int


# ---------------------------------------------------------------------------
# Route patterns
# ---------------------------------------------------------------------------

# Python decorators: @app.get("/path"), @router.post("/path")
_DECORATOR_ROUTE_RE = re.compile(
    r"@\w+\.(get|post|put|delete|patch|head|options)\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

# Python route() decorator: @app.route("/path", methods=["GET"])
_ROUTE_DECORATOR_RE = re.compile(
    r"@\w+\.route\s*\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

# FastAPI APIRouter prefix: router = APIRouter(prefix="/api/auth")
_ROUTER_PREFIX_RE = re.compile(
    r"(?:APIRouter|Blueprint)\s*\(\s*(?:prefix\s*=\s*)?[\"']([^\"']+)[\"']",
)

# URL strings in Python: "/api/something" (inside dicts, variables, etc.)
_PYTHON_PATH_RE = re.compile(
    r"""[\"'](/api/[a-zA-Z0-9_/{}\-]+)[\"']""",
)

# TypeScript/JavaScript: fetch("/api/...") or fetch(`/api/...`)
_TS_FETCH_RE = re.compile(
    r"""fetch\s*\(\s*[`"']([^`"']+)[`"']""",
)

# TypeScript template: fetch(`/api/users/${id}`)
_TS_TEMPLATE_FETCH_RE = re.compile(
    r"""fetch\s*\(\s*`([^`]+)`""",
)

# Axios: axios.get("/api/..."), axios.post("/api/...")
_TS_AXIOS_RE = re.compile(
    r"""axios\.(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']+)[`"']""",
    re.IGNORECASE,
)

# Generic client call: this.client.get("/api/..."), client.post("/api/...")
_TS_CLIENT_RE = re.compile(
    r"""(?:this\.)?(?:client|api|http)\.(get|post|put|delete|patch)\s*\(\s*[`"']([^`"']+)[`"']""",
    re.IGNORECASE,
)

# apiRequest/fetchApi wrapper: apiRequest("/api/...")
_TS_WRAPPER_RE = re.compile(
    r"""(?:apiRequest|fetchApi|request)\s*(?:<[^>]+>)?\s*\(\s*[`"']([^`"']+)[`"']""",
)

# HTTP method in options: { method: 'POST' } or { method: "GET" }
_TS_METHOD_RE = re.compile(
    r"""method\s*:\s*['"](GET|POST|PUT|DELETE|PATCH)['"]""",
    re.IGNORECASE,
)

def _is_test_file(file_path: str) -> bool:
    """Check if a file path belongs to a test file."""
    import os
    basename = os.path.basename(file_path)
    return basename.startswith("test_") or basename == "conftest.py"



# ---------------------------------------------------------------------------
# API Mapper
# ---------------------------------------------------------------------------


class ApiMapper:
    """Extract routes and API calls, then create CALLS_API edges."""

    def __init__(self) -> None:
        self._routes: list[RouteInfo] = []
        self._api_calls: list[ApiCallInfo] = []
        self._edge_counter = 0

    def extract_routes(self, nodes: list[Node]) -> list[RouteInfo]:
        """Extract backend route definitions from Python nodes.

        Decorators like @app.get("/api/users") may not be part of the
        function node's source (Python parser stores them in the module).
        So we scan ALL nodes for decorator patterns and map each decorator
        to the nearest function node that follows it in the same file.
        """
        routes: list[RouteInfo] = []

        # Skip test files — they contain URL string literals that produce false positives
        nodes = [n for n in nodes if not _is_test_file(n.file_path)]

        # First pass: find router prefixes (skip matches inside comments)
        prefixes: dict[str, str] = {}  # file_path → prefix
        for node in nodes:
            if not node.source_code:
                continue
            for match in _ROUTER_PREFIX_RE.finditer(node.source_code):
                if self._is_in_comment(node.source_code, match):
                    continue
                prefixes[node.file_path] = match.group(1).rstrip("/")
                break

        # Build index: file_path → [function nodes sorted by start_line]
        func_index: dict[str, list[Node]] = {}
        for node in nodes:
            if node.type.value in ("function", "method"):
                func_index.setdefault(node.file_path, []).append(node)
        for funcs in func_index.values():
            funcs.sort(key=lambda n: n.start_line)

        # Second pass: scan function/method/module nodes for decorator patterns
        # Skip block nodes (constant definitions, imports — never contain decorators)
        seen: set[tuple[str, str, str]] = set()  # (handler_id, method, path) dedup

        for node in nodes:
            if not node.source_code:
                continue
            if node.type.value == "block":
                continue

            file_prefix = prefixes.get(node.file_path, "")
            source = node.source_code
            lines = source.splitlines()

            # Check decorator-based routes: @app.get("/path")
            for match in _DECORATOR_ROUTE_RE.finditer(source):
                # Real decorators have @ at start of line — skip comments/docstrings
                if not self._is_decorator_start(source, match):
                    continue
                method = match.group(1).upper()
                path = file_prefix + match.group(2)
                handler = self._find_handler(
                    node, match, lines, func_index,
                )
                if not handler:
                    continue
                key = (handler.id, method, path)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(RouteInfo(
                    method=method,
                    path=self._normalize_path(path),
                    handler_node_id=handler.id,
                    file_path=handler.file_path,
                    line=handler.start_line,
                ))

            # Check @app.route() style
            for match in _ROUTE_DECORATOR_RE.finditer(source):
                if not self._is_decorator_start(source, match):
                    continue
                path = file_prefix + match.group(1)
                handler = self._find_handler(
                    node, match, lines, func_index,
                )
                if not handler:
                    continue
                key = (handler.id, "ANY", path)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(RouteInfo(
                    method="ANY",
                    path=self._normalize_path(path),
                    handler_node_id=handler.id,
                    file_path=handler.file_path,
                    line=handler.start_line,
                ))

        self._routes = routes
        return routes

    @staticmethod
    def _find_handler(
        node: Node,
        match: re.Match,
        lines: list[str],
        func_index: dict[str, list[Node]],
    ) -> Node | None:
        """Find the function node decorated by a route pattern.

        If the node is already a function/method, return it directly.
        Otherwise (module node), calculate the absolute line of the decorator
        and find the first function in the same file that starts after it.
        """
        if node.type.value in ("function", "method"):
            return node

        # Calculate absolute line number of the decorator
        text_before = node.source_code[:match.start()]
        decorator_line = node.start_line + text_before.count("\n")

        # Find the function that starts right after the decorator
        for func in func_index.get(node.file_path, []):
            if func.start_line > decorator_line:
                return func

        return None

    @staticmethod
    def _is_decorator_start(source: str, match: re.Match) -> bool:
        """Check that a regex match is a real decorator (@ at start of line).

        Returns False if the match is inside a comment, docstring, or string
        literal — i.e. there is non-whitespace text before the @ on its line.
        """
        line_start = source.rfind("\n", 0, match.start()) + 1
        preceding = source[line_start:match.start()]
        return not preceding.strip()

    @staticmethod
    def _is_in_comment(source: str, match: re.Match) -> bool:
        """Check if a regex match falls within a comment line (# or //)."""
        line_start = source.rfind("\n", 0, match.start()) + 1
        line_prefix = source[line_start:match.start()].lstrip()
        return line_prefix.startswith("#") or line_prefix.startswith("//")

    def extract_api_calls(self, nodes: list[Node]) -> list[ApiCallInfo]:
        """Extract frontend API calls from TypeScript/JavaScript nodes."""
        calls: list[ApiCallInfo] = []

        # Skip test files — they contain URL string literals that produce false positives
        nodes = [n for n in nodes if not _is_test_file(n.file_path)]

        for node in nodes:
            if not node.source_code:
                continue
            if node.type.value not in ("function", "method"):
                continue

            source = node.source_code
            lines = source.splitlines()

            for i, line in enumerate(lines):
                line_num = node.start_line + i

                # fetch() calls
                for match in _TS_FETCH_RE.finditer(line):
                    path = match.group(1)
                    if not path.startswith("/"):
                        continue
                    method = self._extract_method_from_context(lines, i)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=self._normalize_path(path),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                # Template literal fetch
                for match in _TS_TEMPLATE_FETCH_RE.finditer(line):
                    path = match.group(1)
                    if not path.startswith("/"):
                        continue
                    method = self._extract_method_from_context(lines, i)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=self._normalize_path(path),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                # Axios calls
                for match in _TS_AXIOS_RE.finditer(line):
                    method = match.group(1).upper()
                    path = match.group(2)
                    if not path.startswith("/"):
                        continue
                    calls.append(ApiCallInfo(
                        method=method,
                        path=self._normalize_path(path),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                # Client method calls
                for match in _TS_CLIENT_RE.finditer(line):
                    method = match.group(1).upper()
                    path = match.group(2)
                    if not path.startswith("/"):
                        continue
                    calls.append(ApiCallInfo(
                        method=method,
                        path=self._normalize_path(path),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                # Wrapper function calls
                for match in _TS_WRAPPER_RE.finditer(line):
                    path = match.group(1)
                    if not path.startswith("/"):
                        continue
                    method = self._extract_method_from_context(lines, i)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=self._normalize_path(path),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

        self._api_calls = calls
        return calls

    def match(self) -> list[Edge]:
        """Match API calls to routes and create CALLS_API edges."""
        edges: list[Edge] = []

        for call in self._api_calls:
            for route in self._routes:
                if self._paths_match(call.path, route.path):
                    if self._methods_match(call.method, route.method):
                        self._edge_counter += 1
                        edges.append(Edge(
                            id=f"api_edge_{self._edge_counter}",
                            from_node=call.caller_node_id,
                            to_node=route.handler_node_id,
                            type=EdgeType.CALLS_API,
                            line_number=call.line,
                            confidence=EdgeConfidence.INFERRED,
                            source=EdgeSource.STATIC,
                            metadata={
                                "http_method": call.method,
                                "path": call.path,
                                "route_path": route.path,
                            },
                        ))

        if edges:
            logger.info(
                "API mapper: %d cross-language edges (%d routes, %d calls)",
                len(edges), len(self._routes), len(self._api_calls),
            )

        return edges

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize URL path parameters for matching.

        /users/{id}     → /users/:param
        /users/${userId} → /users/:param
        /users/:id      → /users/:param
        """
        # JS template: ${expression} — must come BEFORE {param} to avoid stray $
        path = re.sub(r"\$\{[^}]+\}", ":param", path)
        # FastAPI/Flask: {param_name}
        path = re.sub(r"\{[^}]+\}", ":param", path)
        # Express: :param_name
        path = re.sub(r":([a-zA-Z_]\w*)", ":param", path)
        # Remove trailing slash
        path = path.rstrip("/")
        return path

    @staticmethod
    def _paths_match(call_path: str, route_path: str) -> bool:
        """Check if a call path matches a route path."""
        # Exact match (after normalization)
        if call_path == route_path:
            return True

        # Split into segments and compare
        call_parts = call_path.strip("/").split("/")
        route_parts = route_path.strip("/").split("/")

        if len(call_parts) != len(route_parts):
            return False

        for cp, rp in zip(call_parts, route_parts):
            if cp == rp:
                continue
            if cp == ":param" or rp == ":param":
                continue
            return False

        return True

    @staticmethod
    def _methods_match(call_method: str, route_method: str) -> bool:
        """Check if HTTP methods match."""
        if call_method == "ANY" or route_method == "ANY":
            return True
        return call_method == route_method

    @staticmethod
    def _extract_method_from_context(lines: list[str], line_idx: int) -> str:
        """Try to determine HTTP method from surrounding context."""
        # Check nearby lines for method specification
        start = max(0, line_idx - 1)
        end = min(len(lines), line_idx + 3)
        context = "\n".join(lines[start:end])

        match = _TS_METHOD_RE.search(context)
        if match:
            return match.group(1).upper()

        return "GET"  # Default to GET
