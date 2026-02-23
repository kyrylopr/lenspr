"""Infrastructure mapper for Docker Compose, Dockerfiles, and environment files.

Creates virtual nodes for infrastructure services and edges for their relationships.

Patterns recognized:

docker-compose.yml:
  services:
    web:
      depends_on: [db, redis]    → DEPENDS_ON edges
      ports: ["8080:80"]         → EXPOSES_PORT metadata
      build: ./backend           → links service to code directory
      environment:
        - DB_HOST=db             → USES_ENV edges

Dockerfile:
  FROM python:3.12               → base image metadata
  EXPOSE 8080                    → port metadata
  ENV APP_ENV=production         → environment variable

.env / .env.example:
  DB_HOST=localhost              → env var definitions
  SECRET_KEY=changeme            → secret detection

Code (Python/TypeScript):
  os.environ["DB_HOST"]          → USES_ENV edge to env var
  os.getenv("SECRET_KEY")        → USES_ENV edge
  process.env.DB_HOST            → USES_ENV edge (TypeScript)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from lenspr.models import Edge, EdgeConfidence, EdgeSource, EdgeType, Node, NodeType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class ServiceInfo:
    """A Docker Compose service."""

    name: str  # e.g., "web", "db", "redis"
    node_id: str  # e.g., "infra.service.web"
    image: str = ""
    build_context: str = ""
    ports: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    file_path: str = ""


@dataclass
class EnvVarDef:
    """An environment variable definition from .env or docker-compose."""

    name: str
    value: str
    source_file: str
    line: int


@dataclass
class EnvVarUsage:
    """An environment variable usage in code."""

    name: str
    caller_node_id: str
    file_path: str
    line: int


# ---------------------------------------------------------------------------
# Patterns for env var usage in code
# ---------------------------------------------------------------------------

# Python: os.environ["KEY"], os.environ.get("KEY"), os.getenv("KEY")
_PY_ENVIRON_RE = re.compile(
    r"""os\.environ(?:\[['"](\w+)['"]\]|\.get\s*\(\s*['"](\w+)['"])""",
)
_PY_GETENV_RE = re.compile(
    r"""os\.getenv\s*\(\s*['"](\w+)['"]""",
)

# Python: environ.get("KEY") (from os import environ) — but NOT os.environ
_PY_ENVIRON_GET_RE = re.compile(
    r"""(?<!os\.)environ(?:\[['"](\w+)['"]\]|\.get\s*\(\s*['"](\w+)['"])""",
)

# TypeScript/JavaScript: process.env.KEY or process.env["KEY"]
_TS_PROCESS_ENV_RE = re.compile(
    r"""process\.env\.(\w+)|process\.env\[['"](\w+)['"]\]""",
)

# Vite: import.meta.env.VITE_API_URL
_TS_IMPORT_META_ENV_RE = re.compile(
    r"""import\.meta\.env\.(\w+)""",
)

# .env file: KEY=value or KEY="value"
_DOTENV_RE = re.compile(
    r"""^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$""",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Docker Compose YAML parser (minimal, no PyYAML dependency)
# ---------------------------------------------------------------------------


def _parse_compose_minimal(text: str) -> dict[str, ServiceInfo]:
    """Parse docker-compose.yml without PyYAML.

    Handles the most common patterns via line-by-line parsing.
    Not a full YAML parser — covers the 80% case.
    """
    services: dict[str, ServiceInfo] = {}
    lines = text.splitlines()

    in_services = False
    current_service: str | None = None
    current_section: str | None = None  # "depends_on", "ports", "environment"
    indent_level = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Detect indentation
        leading = len(line) - len(line.lstrip())

        # Top-level "services:" key
        if stripped == "services:" and leading == 0:
            in_services = True
            continue

        if not in_services:
            continue

        # Another top-level key → end of services
        if leading == 0 and stripped.endswith(":"):
            in_services = False
            continue

        # Service name (indent level 2)
        if leading == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            service_name = stripped.rstrip(":")
            current_service = service_name
            current_section = None
            services[service_name] = ServiceInfo(
                name=service_name,
                node_id=f"infra.service.{service_name}",
            )
            continue

        if current_service is None:
            continue

        svc = services[current_service]

        # Service properties (indent level 4+)
        if leading >= 4:
            # Section headers
            if stripped in ("depends_on:", "ports:", "environment:"):
                current_section = stripped.rstrip(":")
                continue

            # image: xxx
            if stripped.startswith("image:"):
                svc.image = stripped.split(":", 1)[1].strip()
                current_section = None
                continue

            # build: xxx
            if stripped.startswith("build:"):
                svc.build_context = stripped.split(":", 1)[1].strip()
                current_section = None
                continue

            # List items under sections
            if stripped.startswith("- "):
                item = stripped[2:].strip()

                if current_section == "depends_on":
                    svc.depends_on.append(item)
                elif current_section == "ports":
                    svc.ports.append(item.strip('"').strip("'"))
                elif current_section == "environment":
                    # - KEY=VALUE or - KEY
                    if "=" in item:
                        key, _, val = item.partition("=")
                        svc.environment[key.strip()] = val.strip()
                    else:
                        svc.environment[item.strip()] = ""
                continue

            # Dict-style depends_on (with conditions):
            #   depends_on:
            #     db:
            #       condition: service_healthy
            if current_section == "depends_on" and stripped.endswith(":") and not stripped.startswith("-"):
                dep_name = stripped[:-1].strip()
                if dep_name and dep_name.isidentifier():
                    svc.depends_on.append(dep_name)
                continue

            # Inline depends_on: [db, redis]
            if stripped.startswith("depends_on:"):
                rest = stripped.split(":", 1)[1].strip()
                if rest.startswith("["):
                    deps = rest.strip("[]").split(",")
                    svc.depends_on = [d.strip().strip("'\"") for d in deps if d.strip()]
                current_section = None
                continue

            # Environment key: value
            if current_section == "environment" and ":" in stripped:
                key, _, val = stripped.partition(":")
                svc.environment[key.strip()] = val.strip()

    return services


# ---------------------------------------------------------------------------
# Infrastructure mapper
# ---------------------------------------------------------------------------


class InfraMapper:
    """Parse infrastructure files and create edges."""

    def __init__(self) -> None:
        self._services: dict[str, ServiceInfo] = {}
        self._env_vars: list[EnvVarDef] = []
        self._env_usages: list[EnvVarUsage] = []
        self._edge_counter = 0

    def parse_compose(self, file_path: Path) -> dict[str, ServiceInfo]:
        """Parse a docker-compose.yml file."""
        if not file_path.exists():
            return {}
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {}

        services = _parse_compose_minimal(text)

        for svc in services.values():
            svc.file_path = str(file_path)

        self._services.update(services)
        return services

    def parse_env_file(self, file_path: Path) -> list[EnvVarDef]:
        """Parse a .env or .env.example file."""
        if not file_path.exists():
            return []
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        env_vars: list[EnvVarDef] = []
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _DOTENV_RE.match(stripped)
            if match:
                env_vars.append(EnvVarDef(
                    name=match.group(1),
                    value=match.group(2).strip().strip("'\""),
                    source_file=str(file_path),
                    line=i,
                ))

        self._env_vars.extend(env_vars)
        return env_vars

    def extract_env_usages(self, nodes: list[Node]) -> list[EnvVarUsage]:
        """Extract environment variable references from code nodes."""
        usages: list[EnvVarUsage] = []

        for node in nodes:
            if not node.source_code:
                continue
            if node.type.value not in ("function", "method"):
                continue

            source = node.source_code
            lines = source.splitlines()

            for i, line in enumerate(lines):
                line_num = node.start_line + i

                # Python patterns
                for match in _PY_ENVIRON_RE.finditer(line):
                    name = match.group(1) or match.group(2)
                    if name:
                        usages.append(EnvVarUsage(
                            name=name,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                for match in _PY_GETENV_RE.finditer(line):
                    usages.append(EnvVarUsage(
                        name=match.group(1),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

                for match in _PY_ENVIRON_GET_RE.finditer(line):
                    name = match.group(1) or match.group(2)
                    if name:
                        usages.append(EnvVarUsage(
                            name=name,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                # TypeScript patterns
                for match in _TS_PROCESS_ENV_RE.finditer(line):
                    name = match.group(1) or match.group(2)
                    if name:
                        usages.append(EnvVarUsage(
                            name=name,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                # Vite: import.meta.env.VITE_API_URL
                for match in _TS_IMPORT_META_ENV_RE.finditer(line):
                    usages.append(EnvVarUsage(
                        name=match.group(1),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

        self._env_usages = usages
        return usages

    def match(self) -> list[Edge]:
        """Create edges from infrastructure relationships."""
        edges: list[Edge] = []

        # Service dependency edges
        for svc in self._services.values():
            for dep in svc.depends_on:
                dep_id = f"infra.service.{dep}"
                self._edge_counter += 1
                edges.append(Edge(
                    id=f"infra_edge_{self._edge_counter}",
                    from_node=svc.node_id,
                    to_node=dep_id,
                    type=EdgeType.DEPENDS_ON,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                    metadata={
                        "source_service": svc.name,
                        "target_service": dep,
                    },
                ))

        # Env var usage edges (deduped)
        seen: set[tuple[str, str]] = set()
        for usage in self._env_usages:
            key = (usage.caller_node_id, usage.name)
            if key in seen:
                continue
            seen.add(key)

            self._edge_counter += 1
            edges.append(Edge(
                id=f"infra_edge_{self._edge_counter}",
                from_node=usage.caller_node_id,
                to_node=f"env.{usage.name}",
                type=EdgeType.USES_ENV,
                line_number=usage.line,
                confidence=EdgeConfidence.RESOLVED,
                source=EdgeSource.STATIC,
                metadata={
                    "env_var": usage.name,
                },
            ))

        if edges:
            logger.info(
                "Infra mapper: %d edges (%d services, %d env vars, %d env usages)",
                len(edges),
                len(self._services),
                len(self._env_vars),
                len(self._env_usages),
            )

        return edges

    def get_service_nodes(self) -> list[Node]:
        """Create virtual nodes for Docker Compose services."""
        nodes: list[Node] = []
        for svc in self._services.values():
            metadata = {}
            if svc.image:
                metadata["image"] = svc.image
            if svc.build_context:
                metadata["build_context"] = svc.build_context
            if svc.ports:
                metadata["ports"] = svc.ports

            source_parts = [f"# Docker service: {svc.name}"]
            if svc.image:
                source_parts.append(f"image: {svc.image}")
            if svc.build_context:
                source_parts.append(f"build: {svc.build_context}")
            if svc.ports:
                source_parts.append(f"ports: {svc.ports}")
            if svc.depends_on:
                source_parts.append(f"depends_on: {svc.depends_on}")

            nodes.append(Node(
                id=svc.node_id,
                type=NodeType.BLOCK,
                name=svc.name,
                qualified_name=svc.node_id,
                file_path=svc.file_path,
                start_line=1,
                end_line=1,
                source_code="\n".join(source_parts),
                docstring=f"Docker Compose service: {svc.name}",
                metadata=metadata,
            ))

        return nodes
