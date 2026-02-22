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

# FastAPI include_router: app.include_router(auth_router, prefix="/api/auth")
# Captures everything inside parens to handle multiline calls and kwargs in any order
_INCLUDE_ROUTER_RE = re.compile(
    r"""\.include_router\s*\(([^)]+)\)""",
    re.DOTALL,
)

# prefix keyword argument: prefix="/api/auth"
_PREFIX_KWARG_RE = re.compile(
    r"""prefix\s*=\s*["']([^"']+)["']""",
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

        # First pass (a): find router prefixes from APIRouter(prefix="...")
        prefixes: dict[str, str] = {}  # file_path → prefix
        for node in nodes:
            if not node.source_code:
                continue
            for match in _ROUTER_PREFIX_RE.finditer(node.source_code):
                if self._is_in_comment(node.source_code, match):
                    continue
                prefixes[node.file_path] = match.group(1).rstrip("/")
                break

        # First pass (b): find prefixes from include_router() calls
        # Handles two patterns:
        # 1. app.include_router(auth_router, prefix="/api/auth")  — explicit prefix
        # 2. parent_router.include_router(sub_router)  — inherits parent's APIRouter prefix
        known_files = {n.file_path for n in nodes}
        seen_includes: set[tuple[str, str]] = set()  # (file_path, router_ref) dedup
        for node in nodes:
            if not node.source_code:
                continue
            for ir_match in _INCLUDE_ROUTER_RE.finditer(node.source_code):
                if self._is_in_comment(node.source_code, ir_match):
                    continue
                args_body = ir_match.group(1)
                # First positional arg = router reference
                first_arg = args_body.split(",")[0].strip()
                # Dedup: module and block nodes can contain the same source
                key = (node.file_path, first_arg)
                if key in seen_includes:
                    continue
                seen_includes.add(key)
                # Find prefix= kwarg in the include_router call
                prefix_match = _PREFIX_KWARG_RE.search(args_body)
                ir_prefix = prefix_match.group(1).rstrip("/") if prefix_match else ""
                # Parent prefix: the calling file's own APIRouter(prefix=...)
                parent_prefix = prefixes.get(node.file_path, "")
                # Combined: parent prefix + include_router prefix
                combined = parent_prefix + ir_prefix
                if not combined:
                    continue
                # Resolve router reference to target file
                target_file = self._resolve_router_ref(
                    first_arg, node, nodes, known_files,
                )
                if not target_file:
                    continue
                # Apply combined prefix (wraps any existing APIRouter prefix in target)
                existing = prefixes.get(target_file, "")
                prefixes[target_file] = combined + existing

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

    @staticmethod
    def _build_import_map(
        file_path: str,
        all_nodes: list["Node"],
    ) -> dict[str, str]:
        """Build local name -> module path mapping from imports in a file.

        Scans block/module nodes in the same file for import statements
        and returns a dict mapping local variable names to their module paths.

        Examples:
            from app.routers import auth       -> {"auth": "app.routers.auth"}
            from app.routers.auth import router -> {"router": "app.routers.auth.router"}
            import app.routers.auth as auth     -> {"auth": "app.routers.auth"}
        """
        import_map: dict[str, str] = {}

        for node in all_nodes:
            if node.file_path != file_path or not node.source_code:
                continue

            # from X.Y.Z import A, B as C, ...
            for m in re.finditer(
                r"from\s+([\w.]+)\s+import\s+([^#\n]+)", node.source_code,
            ):
                module = m.group(1)
                names_str = m.group(2).strip().rstrip("\\").strip("()")
                for part in names_str.split(","):
                    part = part.strip().strip("()")
                    if not part:
                        continue
                    if " as " in part:
                        _original, alias = part.split(" as ", 1)
                        import_map[alias.strip()] = module + "." + _original.strip()
                    else:
                        name = part.strip()
                        if name:
                            import_map[name] = module + "." + name

            # import X.Y.Z [as alias]
            for m in re.finditer(
                r"^import\s+([\w.]+)(?:\s+as\s+(\w+))?",
                node.source_code,
                re.MULTILINE,
            ):
                module = m.group(1)
                alias = m.group(2) or module.rsplit(".", 1)[-1]
                import_map[alias] = module

        return import_map

    @staticmethod
    def _resolve_router_ref(
        ref: str,
        source_node: "Node",
        all_nodes: list["Node"],
        known_files: set[str],
    ) -> str | None:
        """Resolve a router reference to a file path.

        Handles patterns like:
            auth.router   -> look up 'auth' in import map -> app/routers/auth.py
            auth_router   -> look up 'auth_router' in import map -> app/routers/auth.py
        """
        # Strip .router / .app suffix if present (e.g., auth.router -> auth)
        base_ref = ref.split(".")[0] if "." in ref else ref

        import_map = ApiMapper._build_import_map(
            source_node.file_path, all_nodes,
        )

        if base_ref not in import_map:
            return None

        module_path = import_map[base_ref]

        # Try converting module path to file path
        # e.g., app.routers.auth -> app/routers/auth.py
        candidates = [
            module_path.replace(".", "/") + ".py",
            module_path.replace(".", "/") + "/__init__.py",
        ]
        # If not found, try parent module — the import might be an object,
        # not a module.
        # e.g., from app.routers.auth import router
        #   -> module_path = app.routers.auth.router
        #   -> but the file is app/routers/auth.py
        if "." in module_path:
            parent = module_path.rsplit(".", 1)[0]
            candidates.extend([
                parent.replace(".", "/") + ".py",
                parent.replace(".", "/") + "/__init__.py",
            ])

        for candidate in candidates:
            if candidate in known_files:
                return candidate

        # Monorepo: file paths have a package prefix (e.g., mosquito-backend/)
        # that isn't in the Python import path. Try prepending every known
        # top-level directory as prefix.
        source_prefix = source_node.file_path.split("/")[0] + "/"
        for candidate in candidates:
            prefixed = source_prefix + candidate
            if prefixed in known_files:
                return prefixed

        return None

    def extract_api_calls(self, nodes: list[Node]) -> list[ApiCallInfo]:
        """Extract frontend API calls from TypeScript/JavaScript nodes."""
        calls: list[ApiCallInfo] = []

        # Skip test files — they contain URL string literals that produce false positives
        nodes = [n for n in nodes if not _is_test_file(n.file_path)]

        # Process function/method nodes first (most specific caller), then
        # module/block nodes (catch API calls in arrow fns inside object literals).
        # Dedup by (file_path, line, path) so we don't double-count.
        _type_priority = {"function": 0, "method": 0, "block": 1, "module": 2}
        sorted_nodes = sorted(
            nodes,
            key=lambda n: _type_priority.get(n.type.value, 99),
        )
        seen_calls: set[tuple[str, int, str]] = set()  # (file, line, path)

        for node in sorted_nodes:
            if not node.source_code:
                continue
            if node.type.value not in _type_priority:
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
                    norm_path = self._normalize_path(path)
                    call_key = (node.file_path, line_num, norm_path)
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)
                    method = self._extract_method_from_context(lines, i)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=norm_path,
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                # Template literal fetch
                for match in _TS_TEMPLATE_FETCH_RE.finditer(line):
                    path = match.group(1)
                    if not path.startswith("/"):
                        continue
                    norm_path = self._normalize_path(path)
                    call_key = (node.file_path, line_num, norm_path)
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)
                    method = self._extract_method_from_context(lines, i)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=norm_path,
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
                    norm_path = self._normalize_path(path)
                    call_key = (node.file_path, line_num, norm_path)
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=norm_path,
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
                    norm_path = self._normalize_path(path)
                    call_key = (node.file_path, line_num, norm_path)
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=norm_path,
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                # Wrapper function calls
                for match in _TS_WRAPPER_RE.finditer(line):
                    path = match.group(1)
                    if not path.startswith("/"):
                        continue
                    norm_path = self._normalize_path(path)
                    call_key = (node.file_path, line_num, norm_path)
                    if call_key in seen_calls:
                        continue
                    seen_calls.add(call_key)
                    method = self._extract_method_from_context(lines, i)
                    calls.append(ApiCallInfo(
                        method=method,
                        path=norm_path,
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
