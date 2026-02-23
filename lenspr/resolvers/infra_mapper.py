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


@dataclass
class DockerfileInfo:
    """Parsed Dockerfile information."""

    file_path: str
    node_id: str  # e.g., "infra.dockerfile.backend"
    name: str  # derived from path
    base_images: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    exposed_ports: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    arg_vars: dict[str, str] = field(default_factory=dict)
    copy_from_stages: list[str] = field(default_factory=list)
    entrypoint: str = ""


# ---------------------------------------------------------------------------
# Dockerfile patterns
# ---------------------------------------------------------------------------

# FROM image[:tag] [AS stage]
_DOCKERFILE_FROM_RE = re.compile(
    r"""^FROM\s+(\S+)(?:\s+AS\s+(\w+))?""",
    re.IGNORECASE | re.MULTILINE,
)

# EXPOSE port [port...]
_DOCKERFILE_EXPOSE_RE = re.compile(
    r"""^EXPOSE\s+(.+)$""",
    re.IGNORECASE | re.MULTILINE,
)

# ENV key=value or ENV key value
_DOCKERFILE_ENV_RE = re.compile(
    r"""^ENV\s+(\w+)(?:\s*=\s*|\s+)(\S.*)$""",
    re.IGNORECASE | re.MULTILINE,
)

# ARG name[=default]
_DOCKERFILE_ARG_RE = re.compile(
    r"""^ARG\s+(\w+)(?:=(.*))?$""",
    re.IGNORECASE | re.MULTILINE,
)

# COPY [--from=stage] source dest
_DOCKERFILE_COPY_FROM_RE = re.compile(
    r"""^COPY\s+--from=(\w+)""",
    re.IGNORECASE | re.MULTILINE,
)

# ENTRYPOINT or CMD
_DOCKERFILE_ENTRY_RE = re.compile(
    r"""^(?:ENTRYPOINT|CMD)\s+(.+)$""",
    re.IGNORECASE | re.MULTILINE,
)


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

# Pydantic BaseSettings: class Settings(BaseSettings):
_PYDANTIC_BASESETTINGS_RE = re.compile(
    r"""class\s+\w+\s*\([^)]*\bBaseSettings\b""",
)

# Pydantic Field with explicit env=: Field(..., env="ENV_NAME")
_PYDANTIC_FIELD_ENV_RE = re.compile(
    r"""Field\s*\([^)]*\benv\s*=\s*['"](\w+)['"]""",
)

# Pydantic attribute with primitive type annotation (env var by convention)
# Matches lowercase (database_url: str) and UPPER (DATABASE_URL: str)
# Skips nested model types (retry: RetryConfig) via primitive type filter
_PYDANTIC_ATTR_RE = re.compile(
    r"""^\s{4}(\w+)\s*:\s*(?:Optional\s*\[)?\s*(?:str|int|float|bool|Literal)""",
)

# env_prefix in pydantic Config inner class or model_config
_PYDANTIC_ENV_PREFIX_RE = re.compile(
    r"""env_prefix\s*=\s*['"](\w*)['"]""",
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

            # Any other section header at indent 4 (volumes:, healthcheck:, etc.)
            # Reset current_section so list items below don't leak into previous section
            if leading == 4 and stripped.endswith(":") and not stripped.startswith("-"):
                if stripped.rstrip(":") not in ("depends_on", "ports", "environment"):
                    current_section = None
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

        # Pydantic BaseSettings classes — each attribute maps to an env var
        for node in nodes:
            if not node.source_code:
                continue
            if node.type.value != "class":
                continue
            if not _PYDANTIC_BASESETTINGS_RE.search(node.source_code):
                continue

            # Detect env_prefix (e.g. env_prefix = "CRAWLER_")
            prefix_match = _PYDANTIC_ENV_PREFIX_RE.search(node.source_code)
            env_prefix = prefix_match.group(1) if prefix_match else ""

            lines = node.source_code.splitlines()
            for i, line in enumerate(lines):
                line_num = node.start_line + i
                # Check for Field(env="X") — explicit mapping
                field_match = _PYDANTIC_FIELD_ENV_RE.search(line)
                if field_match:
                    usages.append(EnvVarUsage(
                        name=field_match.group(1),
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))
                    continue
                # Typed attribute with primitive type → env var
                # pydantic-settings converts field name to UPPER_CASE
                attr_match = _PYDANTIC_ATTR_RE.match(line)
                if attr_match:
                    attr_name = attr_match.group(1)
                    env_name = env_prefix + attr_name.upper()
                    usages.append(EnvVarUsage(
                        name=env_name,
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

    # ------------------------------------------------------------------
    # Dockerfile parsing
    # ------------------------------------------------------------------

    def parse_dockerfile(self, file_path: Path, root_path: Path) -> DockerfileInfo | None:
        """Parse a Dockerfile and extract infrastructure information.

        Detects: base images (FROM), build stages, EXPOSE ports, ENV/ARG vars,
        COPY --from multi-stage references, ENTRYPOINT/CMD.
        """
        if not file_path.exists():
            return None
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        # Derive node name from path
        rel = file_path.relative_to(root_path)
        # E.g., "backend/Dockerfile" → "backend", "Dockerfile.dev" → "dev"
        if rel.name == "Dockerfile":
            name = str(rel.parent) if str(rel.parent) != "." else "root"
        else:
            # Dockerfile.dev → "dev", Dockerfile.prod → "prod"
            suffix = rel.name.replace("Dockerfile.", "").replace("Dockerfile", "root")
            parent = str(rel.parent)
            name = f"{parent}.{suffix}" if parent != "." else suffix

        name = name.replace("/", ".").replace("\\", ".")
        node_id = f"infra.dockerfile.{name}"

        info = DockerfileInfo(
            file_path=str(rel),
            node_id=node_id,
            name=name,
        )

        # FROM
        for m in _DOCKERFILE_FROM_RE.finditer(text):
            info.base_images.append(m.group(1))
            stage = m.group(2)
            if stage:
                info.stages.append(stage)

        # EXPOSE
        for m in _DOCKERFILE_EXPOSE_RE.finditer(text):
            for port in m.group(1).split():
                port = port.strip()
                if port:
                    info.exposed_ports.append(port)

        # ENV
        for m in _DOCKERFILE_ENV_RE.finditer(text):
            key = m.group(1)
            value = m.group(2).strip()
            info.env_vars[key] = value
            # Also add to env definitions for matching
            self._env_vars.append(EnvVarDef(
                name=key,
                value=value,
                source_file=str(rel),
                line=text[: m.start()].count("\n") + 1,
            ))

        # ARG
        for m in _DOCKERFILE_ARG_RE.finditer(text):
            info.arg_vars[m.group(1)] = m.group(2) or ""

        # COPY --from=stage
        for m in _DOCKERFILE_COPY_FROM_RE.finditer(text):
            info.copy_from_stages.append(m.group(1))

        # ENTRYPOINT/CMD
        for m in _DOCKERFILE_ENTRY_RE.finditer(text):
            info.entrypoint = m.group(1).strip()
            break  # Take the last one? Actually take first.

        if not hasattr(self, "_dockerfiles"):
            self._dockerfiles: list[DockerfileInfo] = []
        self._dockerfiles.append(info)

        return info

    def get_dockerfile_nodes(self) -> list[Node]:
        """Create virtual nodes for parsed Dockerfiles."""
        if not hasattr(self, "_dockerfiles"):
            return []

        nodes: list[Node] = []
        for df in self._dockerfiles:
            source_parts = [f"# Dockerfile: {df.name}"]
            if df.base_images:
                source_parts.append(f"FROM {df.base_images[0]}")
            if df.stages:
                source_parts.append(f"# Stages: {', '.join(df.stages)}")
            if df.exposed_ports:
                source_parts.append(f"EXPOSE {' '.join(df.exposed_ports)}")
            if df.env_vars:
                for k, v in list(df.env_vars.items())[:5]:
                    source_parts.append(f"ENV {k}={v}")
            if df.entrypoint:
                source_parts.append(f"ENTRYPOINT {df.entrypoint}")

            metadata: dict = {"is_virtual": True}
            if df.base_images:
                metadata["base_images"] = df.base_images
            if df.stages:
                metadata["stages"] = df.stages
            if df.exposed_ports:
                metadata["exposed_ports"] = df.exposed_ports

            nodes.append(Node(
                id=df.node_id,
                type=NodeType.BLOCK,
                name=df.name,
                qualified_name=df.node_id,
                file_path=df.file_path,
                start_line=1,
                end_line=1,
                source_code="\n".join(source_parts),
                docstring=f"Dockerfile: {df.name}",
                metadata=metadata,
            ))

        return nodes

    def get_dockerfile_edges(self) -> list[Edge]:
        """Create edges from Dockerfile relationships.

        Creates:
        - DEPENDS_ON: multi-stage COPY --from=stage
        """
        if not hasattr(self, "_dockerfiles"):
            return []

        edges: list[Edge] = []
        for df in self._dockerfiles:
            # Multi-stage COPY --from edges
            for stage in df.copy_from_stages:
                self._edge_counter += 1
                edges.append(Edge(
                    id=f"infra_edge_{self._edge_counter}",
                    from_node=df.node_id,
                    to_node=f"{df.node_id}.{stage}",
                    type=EdgeType.DEPENDS_ON,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                    metadata={
                        "relationship": "copy_from_stage",
                        "stage": stage,
                    },
                ))

        return edges
