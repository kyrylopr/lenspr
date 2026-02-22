"""Tests for lenspr/resolvers/infra_mapper.py — Docker/infrastructure mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr.models import EdgeType, Node, NodeType
from lenspr.resolvers.infra_mapper import (
    InfraMapper,
    ServiceInfo,
    _parse_compose_minimal,
)


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
# Tests — Docker Compose parsing
# ---------------------------------------------------------------------------

SAMPLE_COMPOSE = """\
version: "3.8"

services:
  web:
    build: ./backend
    ports:
      - "8080:80"
    depends_on:
      - db
      - redis
    environment:
      - DB_HOST=db
      - REDIS_URL=redis://redis:6379

  db:
    image: postgres:15
    ports:
      - "5432:5432"
    environment:
      POSTGRES_DB: myapp
      POSTGRES_USER: admin

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
"""


class TestComposeParser:
    def test_parses_services(self) -> None:
        """All three services are detected."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert set(services.keys()) == {"web", "db", "redis"}

    def test_service_image(self) -> None:
        """Service image is extracted."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert services["db"].image == "postgres:15"
        assert services["redis"].image == "redis:7-alpine"

    def test_service_build(self) -> None:
        """Service build context is extracted."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert services["web"].build_context == "./backend"

    def test_service_ports(self) -> None:
        """Service ports are extracted."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert "8080:80" in services["web"].ports
        assert "5432:5432" in services["db"].ports

    def test_depends_on_list(self) -> None:
        """depends_on list items are extracted."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert set(services["web"].depends_on) == {"db", "redis"}

    def test_environment_dash_format(self) -> None:
        """Environment vars in '- KEY=VALUE' format."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert services["web"].environment["DB_HOST"] == "db"

    def test_environment_colon_format(self) -> None:
        """Environment vars in 'KEY: VALUE' format."""
        services = _parse_compose_minimal(SAMPLE_COMPOSE)
        assert services["db"].environment["POSTGRES_DB"] == "myapp"

    def test_inline_depends_on(self) -> None:
        """depends_on: [db, redis] inline format."""
        text = """\
services:
  web:
    depends_on: [db, redis]
  db:
    image: postgres
  redis:
    image: redis
"""
        services = _parse_compose_minimal(text)
        assert set(services["web"].depends_on) == {"db", "redis"}

    def test_empty_compose(self) -> None:
        """Empty/no services compose."""
        services = _parse_compose_minimal("")
        assert services == {}

    def test_no_services_section(self) -> None:
        """YAML without services section."""
        text = "version: '3'\nnetworks:\n  default:\n"
        services = _parse_compose_minimal(text)
        assert services == {}


# ---------------------------------------------------------------------------
# Tests — .env file parsing
# ---------------------------------------------------------------------------


class TestEnvFileParsing:
    def test_basic_env_file(self, tmp_path: Path) -> None:
        """Standard .env file is parsed."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DB_HOST=localhost\n"
            "DB_PORT=5432\n"
            "SECRET_KEY=supersecret\n"
        )
        mapper = InfraMapper()
        vars_ = mapper.parse_env_file(env_file)
        assert len(vars_) == 3
        names = {v.name for v in vars_}
        assert names == {"DB_HOST", "DB_PORT", "SECRET_KEY"}

    def test_env_with_comments(self, tmp_path: Path) -> None:
        """.env with comments and empty lines."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# Database config\n"
            "DB_HOST=localhost\n"
            "\n"
            "# Redis\n"
            "REDIS_URL=redis://localhost\n"
        )
        mapper = InfraMapper()
        vars_ = mapper.parse_env_file(env_file)
        assert len(vars_) == 2

    def test_env_with_quotes(self, tmp_path: Path) -> None:
        """.env values with quotes are stripped."""
        env_file = tmp_path / ".env"
        env_file.write_text('API_KEY="my-api-key"\n')
        mapper = InfraMapper()
        vars_ = mapper.parse_env_file(env_file)
        assert len(vars_) == 1
        assert vars_[0].value == "my-api-key"

    def test_missing_env_file(self, tmp_path: Path) -> None:
        """Non-existent .env file returns empty."""
        mapper = InfraMapper()
        vars_ = mapper.parse_env_file(tmp_path / ".env.missing")
        assert vars_ == []


# ---------------------------------------------------------------------------
# Tests — Env var usage extraction from code
# ---------------------------------------------------------------------------


class TestEnvVarUsage:
    def test_os_environ_bracket(self) -> None:
        """os.environ['KEY'] detected."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.get_db",
                'def get_db():\n'
                "    return os.environ['DB_HOST']\n",
            ),
        ])
        assert len(usages) == 1
        assert usages[0].name == "DB_HOST"

    def test_os_environ_get(self) -> None:
        """os.environ.get('KEY') detected."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.get_key",
                'def get_key():\n'
                '    return os.environ.get("SECRET_KEY")\n',
            ),
        ])
        assert len(usages) == 1
        assert usages[0].name == "SECRET_KEY"

    def test_os_getenv(self) -> None:
        """os.getenv('KEY') detected."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.get_port",
                'def get_port():\n'
                '    return os.getenv("PORT")\n',
            ),
        ])
        assert len(usages) == 1
        assert usages[0].name == "PORT"

    def test_process_env_dot(self) -> None:
        """process.env.KEY detected (TypeScript)."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.getApiUrl",
                'function getApiUrl() {\n'
                '    return process.env.API_URL;\n'
                '}',
                file_path="config.ts",
            ),
        ])
        assert len(usages) == 1
        assert usages[0].name == "API_URL"

    def test_process_env_bracket(self) -> None:
        """process.env['KEY'] detected (TypeScript)."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.getSecret",
                'function getSecret() {\n'
                '    return process.env["SECRET"];\n'
                '}',
                file_path="config.ts",
            ),
        ])
        assert len(usages) == 1
        assert usages[0].name == "SECRET"

    def test_multiple_env_vars_in_one_function(self) -> None:
        """Multiple env var references in one function."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.setup",
                'def setup():\n'
                '    host = os.environ["DB_HOST"]\n'
                '    port = os.getenv("DB_PORT")\n'
                '    key = os.environ.get("SECRET_KEY")\n',
            ),
        ])
        assert len(usages) == 3
        names = {u.name for u in usages}
        assert names == {"DB_HOST", "DB_PORT", "SECRET_KEY"}

    def test_no_env_vars(self) -> None:
        """Function without env var usage."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node("utils.add", "def add(a, b):\n    return a + b\n"),
        ])
        assert len(usages) == 0


# ---------------------------------------------------------------------------
# Tests — Edge creation
# ---------------------------------------------------------------------------


class TestEdgeCreation:
    def test_depends_on_creates_edges(self, tmp_path: Path) -> None:
        """Service depends_on → DEPENDS_ON edge."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(SAMPLE_COMPOSE)

        mapper = InfraMapper()
        mapper.parse_compose(compose)
        edges = mapper.match()

        dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
        assert len(dep_edges) == 2  # web → db, web → redis

        targets = {e.to_node for e in dep_edges}
        assert targets == {"infra.service.db", "infra.service.redis"}
        assert all(e.from_node == "infra.service.web" for e in dep_edges)

    def test_env_usage_creates_edges(self) -> None:
        """Env var usage in code → USES_ENV edge."""
        mapper = InfraMapper()
        mapper.extract_env_usages([
            _make_node(
                "config.setup",
                'def setup():\n'
                '    return os.environ["DB_HOST"]\n',
            ),
        ])
        edges = mapper.match()

        env_edges = [e for e in edges if e.type == EdgeType.USES_ENV]
        assert len(env_edges) == 1
        assert env_edges[0].from_node == "config.setup"
        assert env_edges[0].to_node == "env.DB_HOST"
        assert env_edges[0].metadata["env_var"] == "DB_HOST"

    def test_dedup_env_same_function(self) -> None:
        """Multiple reads of same env var in same function → one edge."""
        mapper = InfraMapper()
        mapper.extract_env_usages([
            _make_node(
                "config.setup",
                'def setup():\n'
                '    a = os.environ["DB_HOST"]\n'
                '    b = os.getenv("DB_HOST")\n',
            ),
        ])
        edges = mapper.match()

        env_edges = [e for e in edges if e.type == EdgeType.USES_ENV]
        assert len(env_edges) == 1

    def test_no_infra_no_edges(self) -> None:
        """No infrastructure → no edges."""
        mapper = InfraMapper()
        edges = mapper.match()
        assert len(edges) == 0

    def test_service_nodes_created(self, tmp_path: Path) -> None:
        """Virtual service nodes are created."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(SAMPLE_COMPOSE)

        mapper = InfraMapper()
        mapper.parse_compose(compose)
        nodes = mapper.get_service_nodes()

        assert len(nodes) == 3
        node_ids = {n.id for n in nodes}
        assert node_ids == {
            "infra.service.web",
            "infra.service.db",
            "infra.service.redis",
        }

    def test_edge_ids_unique(self, tmp_path: Path) -> None:
        """All edge IDs are unique."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(SAMPLE_COMPOSE)

        mapper = InfraMapper()
        mapper.parse_compose(compose)
        mapper.extract_env_usages([
            _make_node(
                "config.setup",
                'def setup():\n    return os.getenv("DB_HOST")\n',
            ),
        ])
        edges = mapper.match()

        ids = {e.id for e in edges}
        assert len(ids) == len(edges)


# ---------------------------------------------------------------------------
# Tests — Full integration
# ---------------------------------------------------------------------------


class TestFullIntegration:
    def test_compose_plus_code(self, tmp_path: Path) -> None:
        """Docker Compose + code env references → combined edges."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text(
            "services:\n"
            "  api:\n"
            "    build: .\n"
            "    depends_on:\n"
            "      - db\n"
            "    environment:\n"
            "      - DB_HOST=db\n"
            "  db:\n"
            "    image: postgres:15\n"
        )

        mapper = InfraMapper()
        mapper.parse_compose(compose)
        mapper.extract_env_usages([
            _make_node(
                "app.connect_db",
                'def connect_db():\n'
                '    host = os.environ["DB_HOST"]\n'
                '    return connect(host)\n',
            ),
        ])
        edges = mapper.match()

        dep_edges = [e for e in edges if e.type == EdgeType.DEPENDS_ON]
        env_edges = [e for e in edges if e.type == EdgeType.USES_ENV]

        assert len(dep_edges) == 1
        assert dep_edges[0].from_node == "infra.service.api"
        assert dep_edges[0].to_node == "infra.service.db"

        assert len(env_edges) == 1
        assert env_edges[0].from_node == "app.connect_db"
        assert env_edges[0].to_node == "env.DB_HOST"


# ---------------------------------------------------------------------------
# Tests — Vite import.meta.env detection
# ---------------------------------------------------------------------------


class TestViteImportMetaEnv:
    def test_import_meta_env_single(self) -> None:
        """import.meta.env.VITE_API_URL detected."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "api.getBaseUrl",
                'function getBaseUrl() {\n'
                '    return import.meta.env.VITE_API_URL;\n'
                '}',
                file_path="src/api.ts",
            ),
        ])
        assert len(usages) == 1
        assert usages[0].name == "VITE_API_URL"
        assert usages[0].caller_node_id == "api.getBaseUrl"

    def test_import_meta_env_multiple(self) -> None:
        """Multiple import.meta.env.VITE_* in same function."""
        mapper = InfraMapper()
        usages = mapper.extract_env_usages([
            _make_node(
                "config.init",
                'function init() {\n'
                '    const url = import.meta.env.VITE_API_URL;\n'
                '    const key = import.meta.env.VITE_PUBLIC_KEY;\n'
                '    const mode = import.meta.env.VITE_MODE;\n'
                '}',
                file_path="src/config.ts",
            ),
        ])
        names = {u.name for u in usages}
        assert names == {"VITE_API_URL", "VITE_PUBLIC_KEY", "VITE_MODE"}
