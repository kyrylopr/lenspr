"""Tests for lenspr/resolvers/api_mapper.py — Cross-language API mapping."""

from __future__ import annotations

import pytest

from lenspr.models import EdgeConfidence, EdgeType, Node, NodeType
from lenspr.resolvers.api_mapper import ApiMapper

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    source: str,
    file_path: str = "app.py",
    node_type: str = "function",
    start_line: int = 1,
) -> Node:
    return Node(
        id=node_id,
        type=NodeType(node_type),
        name=node_id.split(".")[-1],
        qualified_name=node_id,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + source.count("\n"),
        source_code=source,
    )


# ---------------------------------------------------------------------------
# Tests — Route extraction (Python backend)
# ---------------------------------------------------------------------------


class TestRouteExtraction:
    def test_fastapi_decorator_get(self) -> None:
        """@app.get('/api/users') detected as GET route."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "app.get_users",
                '@app.get("/api/users")\n'
                "def get_users():\n"
                "    return []",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/api/users"
        assert routes[0].handler_node_id == "app.get_users"

    def test_fastapi_router_post(self) -> None:
        """@router.post('/api/auth/login') detected as POST route."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "auth.login",
                '@router.post("/api/auth/login")\n'
                "def login():\n"
                "    pass",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "POST"
        assert routes[0].path == "/api/auth/login"

    def test_flask_route_decorator(self) -> None:
        """@app.route('/api/health') detected as ANY route."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "app.health",
                '@app.route("/api/health")\n'
                "def health():\n"
                '    return {"status": "ok"}',
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "ANY"
        assert routes[0].path == "/api/health"

    def test_router_prefix_concatenation(self) -> None:
        """APIRouter(prefix='/api/auth') + @router.post('/login') = /api/auth/login."""
        mapper = ApiMapper()
        nodes = [
            # Block node with prefix definition
            _make_node(
                "auth_routes.block_1",
                'router = APIRouter(prefix="/api/auth")',
                file_path="auth_routes.py",
                node_type="block",
            ),
            # Handler in same file
            _make_node(
                "auth_routes.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="auth_routes.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/auth/login"

    def test_path_param_normalized(self) -> None:
        """@app.get('/api/users/{user_id}') normalized to /api/users/:param."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "app.get_user",
                '@app.get("/api/users/{user_id}")\n'
                "def get_user(user_id):\n"
                "    pass",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/users/:param"

    def test_multiple_routes_from_multiple_decorators(self) -> None:
        """Multiple decorators on different handlers."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "app.list_users",
                '@app.get("/api/users")\ndef list_users(): pass',
            ),
            _make_node(
                "app.create_user",
                '@app.post("/api/users")\ndef create_user(): pass',
            ),
            _make_node(
                "app.delete_user",
                '@app.delete("/api/users/{id}")\ndef delete_user(): pass',
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 3
        methods = {r.method for r in routes}
        assert methods == {"GET", "POST", "DELETE"}

    def test_skips_non_function_nodes(self) -> None:
        """Only function/method nodes are checked for route decorators."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "models.User",
                'class User:\n    pass\n',
                node_type="class",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 0

    def test_skips_nodes_without_source(self) -> None:
        """Nodes with no source code are skipped gracefully."""
        mapper = ApiMapper()
        node = _make_node("app.handler", "")
        node.source_code = None
        routes = mapper.extract_routes([node])
        assert len(routes) == 0


# ---------------------------------------------------------------------------
# Tests — API call extraction (TypeScript/JavaScript frontend)
# ---------------------------------------------------------------------------


class TestApiCallExtraction:
    def test_fetch_string_literal(self) -> None:
        """fetch('/api/users') detected as GET call."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "hooks.fetchUsers",
                'async function fetchUsers() {\n'
                '    const res = await fetch("/api/users");\n'
                '    return res.json();\n'
                '}',
                file_path="hooks.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 1
        assert calls[0].method == "GET"  # default
        assert calls[0].path == "/api/users"
        assert calls[0].caller_node_id == "hooks.fetchUsers"

    def test_fetch_template_literal(self) -> None:
        """fetch(`/api/users/${id}`) detected with param normalized."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "hooks.getUser",
                'async function getUser(id: string) {\n'
                '    return fetch(`/api/users/${id}`);\n'
                '}',
                file_path="hooks.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) >= 1
        # Path should be normalized
        assert calls[0].path == "/api/users/:param"

    def test_fetch_with_method_option(self) -> None:
        """fetch('/api/auth', {method: 'POST'}) detected as POST."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "auth.login",
                'async function login() {\n'
                '    return fetch("/api/auth/login", {\n'
                "        method: 'POST',\n"
                '        body: JSON.stringify(data),\n'
                '    });\n'
                '}',
                file_path="auth.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].path == "/api/auth/login"

    def test_axios_get(self) -> None:
        """axios.get('/api/users') detected as GET."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "api.getUsers",
                'function getUsers() {\n'
                '    return axios.get("/api/users");\n'
                '}',
                file_path="api.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 1
        assert calls[0].method == "GET"
        assert calls[0].path == "/api/users"

    def test_axios_post(self) -> None:
        """axios.post('/api/users') detected as POST."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "api.createUser",
                'function createUser(data) {\n'
                '    return axios.post("/api/users", data);\n'
                '}',
                file_path="api.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 1
        assert calls[0].method == "POST"

    def test_client_method_call(self) -> None:
        """this.client.post('/api/auth') detected as POST."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "AuthService.login",
                'async login(email: string) {\n'
                '    return this.client.post("/api/auth/login", {email});\n'
                '}',
                file_path="services/auth.ts",
                node_type="method",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].path == "/api/auth/login"

    def test_wrapper_function(self) -> None:
        """apiRequest('/api/chat') detected."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "chat.sendMessage",
                'function sendMessage(msg: string) {\n'
                '    return apiRequest("/api/chat/send", {\n'
                "        method: 'POST',\n"
                '        body: JSON.stringify({msg}),\n'
                '    });\n'
                '}',
                file_path="chat.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert calls[0].path == "/api/chat/send"

    def test_skips_non_api_urls(self) -> None:
        """fetch('https://example.com') is not an API call (no leading /)."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "ext.fetchExternal",
                'function fetchExternal() {\n'
                '    return fetch("https://example.com/api/data");\n'
                '}',
                file_path="ext.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 0

    def test_multiple_calls_in_one_function(self) -> None:
        """Multiple API calls in one function are all detected."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "dashboard.loadData",
                'async function loadData() {\n'
                '    const users = await fetch("/api/users");\n'
                '    const tasks = await fetch("/api/tasks");\n'
                '    return {users, tasks};\n'
                '}',
                file_path="dashboard.ts",
            ),
        ]
        calls = mapper.extract_api_calls(nodes)
        assert len(calls) == 2
        paths = {c.path for c in calls}
        assert paths == {"/api/users", "/api/tasks"}


# ---------------------------------------------------------------------------
# Tests — Path normalization
# ---------------------------------------------------------------------------


class TestPathNormalization:
    def test_fastapi_curly_braces(self) -> None:
        assert ApiMapper._normalize_path("/api/users/{user_id}") == "/api/users/:param"

    def test_js_template_dollar_braces(self) -> None:
        assert ApiMapper._normalize_path("/api/users/${userId}") == "/api/users/:param"

    def test_express_colon_param(self) -> None:
        assert ApiMapper._normalize_path("/api/users/:userId") == "/api/users/:param"

    def test_trailing_slash_removed(self) -> None:
        assert ApiMapper._normalize_path("/api/users/") == "/api/users"

    def test_no_params(self) -> None:
        assert ApiMapper._normalize_path("/api/health") == "/api/health"

    def test_multiple_params(self) -> None:
        result = ApiMapper._normalize_path("/api/users/{uid}/tasks/{tid}")
        assert result == "/api/users/:param/tasks/:param"


# ---------------------------------------------------------------------------
# Tests — Path matching
# ---------------------------------------------------------------------------


class TestPathMatching:
    def test_exact_match(self) -> None:
        assert ApiMapper._paths_match("/api/users", "/api/users") is True

    def test_param_wildcard_match(self) -> None:
        assert ApiMapper._paths_match("/api/users/:param", "/api/users/:param") is True

    def test_different_paths(self) -> None:
        assert ApiMapper._paths_match("/api/users", "/api/tasks") is False

    def test_different_lengths(self) -> None:
        assert ApiMapper._paths_match("/api/users", "/api/users/123") is False

    def test_param_matches_literal(self) -> None:
        """A :param segment matches any literal segment."""
        assert ApiMapper._paths_match("/api/users/:param", "/api/users/abc") is True

    def test_method_match_exact(self) -> None:
        assert ApiMapper._methods_match("GET", "GET") is True

    def test_method_match_any(self) -> None:
        assert ApiMapper._methods_match("POST", "ANY") is True
        assert ApiMapper._methods_match("ANY", "DELETE") is True

    def test_method_mismatch(self) -> None:
        assert ApiMapper._methods_match("GET", "POST") is False


# ---------------------------------------------------------------------------
# Tests — End-to-end matching
# ---------------------------------------------------------------------------


class TestEndToEndMatching:
    def test_fetch_matches_fastapi_route(self) -> None:
        """fetch('/api/users') → @app.get('/api/users') creates CALLS_API edge."""
        mapper = ApiMapper()

        backend_nodes = [
            _make_node(
                "backend.api.get_users",
                '@app.get("/api/users")\ndef get_users(): return []',
                file_path="backend/api.py",
            ),
        ]
        frontend_nodes = [
            _make_node(
                "frontend.hooks.fetchUsers",
                'async function fetchUsers() {\n'
                '    return fetch("/api/users");\n'
                '}',
                file_path="frontend/hooks.ts",
            ),
        ]

        mapper.extract_routes(backend_nodes)
        mapper.extract_api_calls(frontend_nodes)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].type == EdgeType.CALLS_API
        assert edges[0].from_node == "frontend.hooks.fetchUsers"
        assert edges[0].to_node == "backend.api.get_users"
        assert edges[0].confidence == EdgeConfidence.INFERRED
        assert edges[0].metadata["http_method"] == "GET"
        assert edges[0].metadata["path"] == "/api/users"

    def test_post_matches_post_route(self) -> None:
        """axios.post('/api/chat') → @router.post('/api/chat') matches."""
        mapper = ApiMapper()

        backend = [
            _make_node(
                "chat.send_message",
                '@router.post("/api/chat")\ndef send_message(): pass',
                file_path="chat.py",
            ),
        ]
        frontend = [
            _make_node(
                "ChatService.send",
                'async send(msg: string) {\n'
                '    return axios.post("/api/chat", {msg});\n'
                '}',
                file_path="chat.ts",
                node_type="method",
            ),
        ]

        mapper.extract_routes(backend)
        mapper.extract_api_calls(frontend)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].from_node == "ChatService.send"
        assert edges[0].to_node == "chat.send_message"

    def test_param_path_matching(self) -> None:
        """fetch(`/api/users/${id}`) → @app.get('/api/users/{user_id}') matches."""
        mapper = ApiMapper()

        backend = [
            _make_node(
                "api.get_user",
                '@app.get("/api/users/{user_id}")\ndef get_user(user_id): pass',
                file_path="api.py",
            ),
        ]
        frontend = [
            _make_node(
                "hooks.getUser",
                'function getUser(id: string) {\n'
                '    return fetch(`/api/users/${id}`);\n'
                '}',
                file_path="hooks.ts",
            ),
        ]

        mapper.extract_routes(backend)
        mapper.extract_api_calls(frontend)
        edges = mapper.match()

        assert len(edges) >= 1
        assert edges[0].from_node == "hooks.getUser"
        assert edges[0].to_node == "api.get_user"

    def test_method_mismatch_no_edge(self) -> None:
        """GET call to POST-only route creates no edge."""
        mapper = ApiMapper()

        backend = [
            _make_node(
                "api.create_user",
                '@app.post("/api/users")\ndef create_user(): pass',
                file_path="api.py",
            ),
        ]
        frontend = [
            _make_node(
                "hooks.getUsers",
                'function getUsers() {\n'
                '    return fetch("/api/users");\n'  # defaults to GET
                '}',
                file_path="hooks.ts",
            ),
        ]

        mapper.extract_routes(backend)
        mapper.extract_api_calls(frontend)
        edges = mapper.match()

        assert len(edges) == 0

    def test_flask_route_any_matches_all_methods(self) -> None:
        """@app.route('/api/health') with ANY matches GET call."""
        mapper = ApiMapper()

        backend = [
            _make_node(
                "api.health",
                '@app.route("/api/health")\ndef health(): return "ok"',
                file_path="api.py",
            ),
        ]
        frontend = [
            _make_node(
                "hooks.checkHealth",
                'function checkHealth() {\n'
                '    return fetch("/api/health");\n'
                '}',
                file_path="hooks.ts",
            ),
        ]

        mapper.extract_routes(backend)
        mapper.extract_api_calls(frontend)
        edges = mapper.match()

        assert len(edges) == 1

    def test_multiple_matches_creates_multiple_edges(self) -> None:
        """Multiple frontend calls to same route each create an edge."""
        mapper = ApiMapper()

        backend = [
            _make_node(
                "api.get_users",
                '@app.get("/api/users")\ndef get_users(): return []',
                file_path="api.py",
            ),
        ]
        frontend = [
            _make_node(
                "component_a.load",
                'function load() { return fetch("/api/users"); }',
                file_path="a.ts",
            ),
            _make_node(
                "component_b.refresh",
                'function refresh() { return fetch("/api/users"); }',
                file_path="b.ts",
            ),
        ]

        mapper.extract_routes(backend)
        mapper.extract_api_calls(frontend)
        edges = mapper.match()

        assert len(edges) == 2
        from_nodes = {e.from_node for e in edges}
        assert from_nodes == {"component_a.load", "component_b.refresh"}

    def test_no_routes_no_edges(self) -> None:
        """No routes extracted → no edges even with API calls."""
        mapper = ApiMapper()
        mapper.extract_routes([])
        mapper.extract_api_calls([
            _make_node(
                "hooks.fetch",
                'function f() { return fetch("/api/users"); }',
                file_path="hooks.ts",
            ),
        ])
        edges = mapper.match()
        assert len(edges) == 0

    def test_no_calls_no_edges(self) -> None:
        """No API calls → no edges even with routes."""
        mapper = ApiMapper()
        mapper.extract_routes([
            _make_node(
                "api.get",
                '@app.get("/api/users")\ndef get(): pass',
                file_path="api.py",
            ),
        ])
        mapper.extract_api_calls([])
        edges = mapper.match()
        assert len(edges) == 0

    def test_router_prefix_full_integration(self) -> None:
        """APIRouter prefix + route + fetch → CALLS_API edge."""
        mapper = ApiMapper()

        backend = [
            _make_node(
                "auth_routes.block_1",
                'router = APIRouter(prefix="/api/auth")',
                file_path="auth_routes.py",
                node_type="block",
            ),
            _make_node(
                "auth_routes.login",
                '@router.post("/login")\ndef login(email, password): pass',
                file_path="auth_routes.py",
            ),
        ]
        frontend = [
            _make_node(
                "AuthService.login",
                'async login(email: string, password: string) {\n'
                '    return this.client.post("/api/auth/login", {email, password});\n'
                '}',
                file_path="services/auth.ts",
                node_type="method",
            ),
        ]

        mapper.extract_routes(backend)
        mapper.extract_api_calls(frontend)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].from_node == "AuthService.login"
        assert edges[0].to_node == "auth_routes.login"
        assert edges[0].metadata["path"] == "/api/auth/login"


# ---------------------------------------------------------------------------
# Tests — Edge metadata
# ---------------------------------------------------------------------------


class TestEdgeMetadata:
    def test_edge_has_correct_metadata(self) -> None:
        """CALLS_API edge includes http_method, path, and route_path."""
        mapper = ApiMapper()

        mapper.extract_routes([
            _make_node(
                "api.users",
                '@app.get("/api/users/{id}")\ndef users(id): pass',
                file_path="api.py",
            ),
        ])
        mapper.extract_api_calls([
            _make_node(
                "hooks.getUser",
                'function getUser(id) { return fetch(`/api/users/${id}`); }',
                file_path="hooks.ts",
            ),
        ])
        edges = mapper.match()

        assert len(edges) >= 1
        edge = edges[0]
        assert edge.metadata["http_method"] in ("GET", "ANY")
        assert "path" in edge.metadata
        assert "route_path" in edge.metadata

    def test_edge_ids_are_unique(self) -> None:
        """Each edge gets a unique ID."""
        mapper = ApiMapper()

        mapper.extract_routes([
            _make_node("api.a", '@app.get("/api/a")\ndef a(): pass'),
            _make_node("api.b", '@app.get("/api/b")\ndef b(): pass'),
        ])
        mapper.extract_api_calls([
            _make_node(
                "client.load",
                'function load() {\n'
                '    fetch("/api/a");\n'
                '    fetch("/api/b");\n'
                '}',
                file_path="client.ts",
            ),
        ])
        edges = mapper.match()

        assert len(edges) == 2
        assert edges[0].id != edges[1].id


# ---------------------------------------------------------------------------
# Tests — Integration with MultiParser.parse_project
# ---------------------------------------------------------------------------


try:
    import tree_sitter  # noqa: F401
    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False


@pytest.mark.skipif(
    not _HAS_TREE_SITTER,
    reason="tree-sitter required for TS parsing",
)
class TestParseProjectIntegration:
    def test_parse_project_creates_calls_api_edges(self, tmp_path) -> None:
        """parse_project produces CALLS_API edges for a mixed Python+TS project."""
        from lenspr.parsers.multi import MultiParser

        # Backend: FastAPI-style Python
        backend_dir = tmp_path / "backend"
        backend_dir.mkdir()
        (backend_dir / "__init__.py").write_text("")
        (backend_dir / "api.py").write_text(
            'from fastapi import FastAPI\n'
            '\n'
            'app = FastAPI()\n'
            '\n'
            '@app.get("/api/users")\n'
            'def list_users():\n'
            '    return []\n'
            '\n'
            '@app.post("/api/users")\n'
            'def create_user(name: str):\n'
            '    return {"id": 1, "name": name}\n'
            '\n'
            '@app.get("/api/users/{user_id}")\n'
            'def get_user(user_id: int):\n'
            '    return {"id": user_id}\n'
        )

        # Frontend: TypeScript with fetch and axios
        frontend_dir = tmp_path / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "api.ts").write_text(
            'export async function fetchUsers() {\n'
            '    return fetch("/api/users");\n'
            '}\n'
            '\n'
            'export async function createUser(name: string) {\n'
            '    return fetch("/api/users", {\n'
            "        method: 'POST',\n"
            '        body: JSON.stringify({name}),\n'
            '    });\n'
            '}\n'
            '\n'
            'export async function getUser(id: number) {\n'
            '    return fetch(`/api/users/${id}`);\n'
            '}\n'
        )

        parser = MultiParser()
        nodes, edges, _ = parser.parse_project(tmp_path)

        api_edges = [e for e in edges if e.type == EdgeType.CALLS_API]
        assert len(api_edges) >= 3, (
            f"Expected at least 3 CALLS_API edges, got {len(api_edges)}: "
            f"{[(e.from_node, e.to_node) for e in api_edges]}"
        )

        # Check specific matches
        edge_pairs = {(e.from_node, e.to_node) for e in api_edges}
        assert any(
            "fetchUsers" in f and "list_users" in t
            for f, t in edge_pairs
        ), f"fetchUsers -> list_users not found in {edge_pairs}"
        assert any(
            "createUser" in f and "create_user" in t
            for f, t in edge_pairs
        ), f"createUser -> create_user not found in {edge_pairs}"

    def test_parse_project_no_crash_without_api_patterns(self, tmp_path) -> None:
        """parse_project doesn't crash when there are no API patterns."""
        from lenspr.parsers.multi import MultiParser

        (tmp_path / "utils.py").write_text(
            "def helper(x):\n    return x * 2\n"
        )

        parser = MultiParser()
        nodes, edges, _ = parser.parse_project(tmp_path)

        api_edges = [e for e in edges if e.type == EdgeType.CALLS_API]
        assert len(api_edges) == 0


# ---------------------------------------------------------------------------
# Tests — include_router() prefix extraction
# ---------------------------------------------------------------------------


class TestIncludeRouterPrefix:
    """Test prefix extraction from FastAPI include_router() calls."""

    def test_include_router_basic(self) -> None:
        """include_router(auth_router, prefix="/api/auth") + @router.post("/login")."""
        mapper = ApiMapper()
        nodes = [
            # main.py — has the include_router call and import
            _make_node(
                "main.block_1",
                'from app.routers import auth\n'
                'app.include_router(auth.router, prefix="/api/auth")',
                file_path="main.py",
                node_type="block",
            ),
            # auth.py — has the route
            _make_node(
                "app.routers.auth.block_1",
                "router = APIRouter()",
                file_path="app/routers/auth.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/auth/login"
        assert routes[0].handler_node_id == "app.routers.auth.login"

    def test_include_router_dotted_ref(self) -> None:
        """include_router(auth.router, prefix="/api/auth") with dotted reference."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "main.block_1",
                'from app.routers import auth\n'
                'app.include_router(auth.router, prefix="/api/auth")',
                file_path="main.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.signup",
                '@router.post("/signup")\n'
                "def signup():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/auth/signup"

    def test_include_router_imported_router_object(self) -> None:
        """from app.routers.auth import router as auth_router."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "main.block_1",
                'from app.routers.auth import router as auth_router\n'
                'app.include_router(auth_router, prefix="/api/auth")',
                file_path="main.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/auth/login"

    def test_multiple_include_routers(self) -> None:
        """Multiple include_router calls with different prefixes."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "main.block_1",
                'from app.routers import auth, chat\n'
                'app.include_router(auth.router, prefix="/api/auth")\n'
                'app.include_router(chat.router, prefix="/api/chat")',
                file_path="main.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
            _make_node(
                "app.routers.chat.send",
                '@router.post("/send")\n'
                "def send():\n"
                "    pass",
                file_path="app/routers/chat.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 2
        paths = {r.path for r in routes}
        assert "/api/auth/login" in paths
        assert "/api/chat/send" in paths

    def test_include_router_without_prefix(self) -> None:
        """include_router(router) without prefix= — no prefix added."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "main.block_1",
                'from app.routers import auth\n'
                'app.include_router(auth.router)',
                file_path="main.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/login"

    def test_both_apirouter_and_include_router_prefix(self) -> None:
        """APIRouter(prefix="/v1") + include_router(prefix="/api") -> /api/v1."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "main.block_1",
                'from app.routers import auth\n'
                'app.include_router(auth.router, prefix="/api")',
                file_path="main.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.block_1",
                'router = APIRouter(prefix="/v1")',
                file_path="app/routers/auth.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.auth.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/v1/login"

    def test_include_router_end_to_end(self) -> None:
        """Frontend fetch matches backend route via include_router prefix."""
        mapper = ApiMapper()
        nodes = [
            # Backend: main.py with include_router
            _make_node(
                "main.block_1",
                'from app.routers import auth\n'
                'app.include_router(auth.router, prefix="/api/auth")',
                file_path="main.py",
                node_type="block",
            ),
            # Backend: router file
            _make_node(
                "app.routers.auth.login",
                '@router.post("/login")\n'
                "def login():\n"
                "    pass",
                file_path="app/routers/auth.py",
            ),
            # Frontend: fetch call
            _make_node(
                "frontend.auth.doLogin",
                'async function doLogin() {\n'
                '    const res = await fetch("/api/auth/login", {\n'
                '        method: "POST"\n'
                "    });\n"
                "}",
                file_path="frontend/auth.ts",
            ),
        ]
        mapper.extract_routes(nodes)
        mapper.extract_api_calls(nodes)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].from_node == "frontend.auth.doLogin"
        assert edges[0].to_node == "app.routers.auth.login"
        assert edges[0].type == EdgeType.CALLS_API

    def test_include_router_inherits_parent_prefix(self) -> None:
        """admin_router.include_router(sub) inherits parent's APIRouter prefix."""
        mapper = ApiMapper()
        nodes = [
            # admin/__init__.py: APIRouter(prefix="/api/admin") + include_router(files_router)
            _make_node(
                "app.routers.admin.block_1",
                'from app.routers.admin import files\n'
                'admin_router = APIRouter(prefix="/api/admin")\n'
                'admin_router.include_router(files.router)',
                file_path="app/routers/admin/__init__.py",
                node_type="block",
            ),
            # admin/files.py: router with NO prefix, just tags
            _make_node(
                "app.routers.admin.files.block_1",
                'router = APIRouter(tags=["Admin - Files"])',
                file_path="app/routers/admin/files.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.admin.files.upload",
                '@router.post("/upload")\n'
                "def upload():\n"
                "    pass",
                file_path="app/routers/admin/files.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].path == "/api/admin/upload"

    def test_include_router_parent_prefix_multiple_subrouters(self) -> None:
        """Parent prefix propagates to all included sub-routers."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "app.routers.admin.block_1",
                'from app.routers.admin import files, users\n'
                'admin_router = APIRouter(prefix="/api/admin")\n'
                'admin_router.include_router(files.router)\n'
                'admin_router.include_router(users.router)',
                file_path="app/routers/admin/__init__.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.admin.files.upload",
                '@router.post("/upload")\n'
                "def upload():\n"
                "    pass",
                file_path="app/routers/admin/files.py",
            ),
            _make_node(
                "app.routers.admin.users.list_users",
                '@router.get("/list")\n'
                "def list_users():\n"
                "    pass",
                file_path="app/routers/admin/users.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 2
        paths = {r.path for r in routes}
        assert "/api/admin/upload" in paths
        assert "/api/admin/list" in paths

    def test_include_router_parent_prefix_end_to_end(self) -> None:
        """Frontend fetch matches backend route via inherited parent prefix."""
        mapper = ApiMapper()
        nodes = [
            # Backend: admin __init__ with APIRouter prefix + include_router
            _make_node(
                "app.routers.admin.block_1",
                'from app.routers.admin import users\n'
                'admin_router = APIRouter(prefix="/api/admin")\n'
                'admin_router.include_router(users.router)',
                file_path="app/routers/admin/__init__.py",
                node_type="block",
            ),
            _make_node(
                "app.routers.admin.users.get_pending",
                '@router.get("/pending-users")\n'
                "def get_pending():\n"
                "    pass",
                file_path="app/routers/admin/users.py",
            ),
            # Frontend: fetch call
            _make_node(
                "frontend.admin.fetchPending",
                'async function fetchPending() {\n'
                '    const res = await fetch("/api/admin/pending-users");\n'
                "}",
                file_path="frontend/admin.ts",
            ),
        ]
        mapper.extract_routes(nodes)
        mapper.extract_api_calls(nodes)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].from_node == "frontend.admin.fetchPending"
        assert edges[0].to_node == "app.routers.admin.users.get_pending"


# ---------------------------------------------------------------------------
# Tests — Programmatic route extraction (Express, Fastify, Hono)
# ---------------------------------------------------------------------------


class TestProgrammaticRouteExtraction:
    """Test detection of app.get('/path', handler) style routes."""

    def test_express_app_get(self) -> None:
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "server.routes.getUsers",
                'app.get("/api/users", getUsers);',
                file_path="src/routes.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/api/users"

    def test_express_router_post(self) -> None:
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "auth.routes.login",
                'router.post("/login", loginHandler);',
                file_path="src/auth/routes.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "POST"
        assert routes[0].path == "/login"

    def test_fastify_get(self) -> None:
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "fastify.items.list",
                'fastify.get("/api/items", listItems);',
                file_path="src/items.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "GET"
        assert routes[0].path == "/api/items"

    def test_hono_post(self) -> None:
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "hono.users.create",
                'hono.post("/api/users", createUser);',
                file_path="src/users.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "POST"

    def test_express_delete(self) -> None:
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "server.routes.deleteUser",
                'app.delete("/api/users/:id", deleteUser);',
                file_path="src/routes.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1
        assert routes[0].method == "DELETE"
        # :id should be normalized to :param
        assert ":param" in routes[0].path

    def test_express_use_mount_prefix(self) -> None:
        mapper = ApiMapper()
        # Module-level: app.use("/api/v1", router)
        # Then: router.get("/users", handler)
        nodes = [
            _make_node(
                "server.app.mount",
                'app.use("/api/v1", router);',
                file_path="src/app.ts",
                node_type="module",
            ),
            _make_node(
                "server.routes.getUsers",
                'router.get("/users", getUsers);',
                file_path="src/routes.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        # The router.get should find its route; mount prefix applies to "router" var
        assert any(r.method == "GET" for r in routes)

    def test_multiple_express_routes(self) -> None:
        mapper = ApiMapper()
        source = (
            'app.get("/api/users", listUsers);\n'
            'app.post("/api/users", createUser);\n'
            'app.put("/api/users/:id", updateUser);\n'
            'app.delete("/api/users/:id", deleteUser);'
        )
        nodes = [
            _make_node(
                "server.routes",
                source,
                file_path="src/routes.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 4
        methods = {r.method for r in routes}
        assert methods == {"GET", "POST", "PUT", "DELETE"}

    def test_skips_python_files(self) -> None:
        """Programmatic routes should NOT match in Python files."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "app.main.setup",
                'app.get("/api/users", handler)',
                file_path="app/main.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        # Should be 0 — Python files use decorator patterns only
        assert len(routes) == 0

    def test_skips_test_files_ts(self) -> None:
        """Test files in TS should be skipped."""
        mapper = ApiMapper()
        nodes = [
            _make_node(
                "test.routes",
                'app.get("/api/test", handler);',
                file_path="src/__tests__/routes.test.ts",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 0

    def test_end_to_end_express_with_fetch(self) -> None:
        """Express route should match with frontend fetch call."""
        mapper = ApiMapper()
        backend = _make_node(
            "server.routes.getUsers",
            'app.get("/api/users", getUsers);',
            file_path="src/routes.ts",
        )
        frontend = _make_node(
            "client.api.fetchUsers",
            'const res = await fetch("/api/users");',
            file_path="src/client/api.ts",
        )
        mapper.extract_routes([backend, frontend])
        mapper.extract_api_calls([backend, frontend])
        edges = mapper.match()
        assert len(edges) == 1
        assert edges[0].type == EdgeType.CALLS_API
        assert edges[0].from_node == "client.api.fetchUsers"
        assert edges[0].to_node == "server.routes.getUsers"

    def test_no_duplicates_with_decorator(self) -> None:
        """Same route via decorator and programmatic should not duplicate."""
        mapper = ApiMapper()
        nodes = [
            # Python-style decorator — this won't match programmatic because it's .py
            _make_node(
                "app.users.get_users",
                '@app.get("/api/users")\ndef get_users():\n    pass',
                file_path="app/users.py",
            ),
        ]
        routes = mapper.extract_routes(nodes)
        assert len(routes) == 1  # Only one route, not duplicated
