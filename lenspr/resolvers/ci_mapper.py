"""CI/CD workflow mapper — parse GitHub Actions YAML and create graph nodes/edges.

Extracts workflows, jobs, steps, dependencies, secret/env references from
.github/workflows/*.yml files. Uses a minimal indentation-based YAML parser
(same approach as ``_parse_compose_minimal`` in infra_mapper) — no PyYAML needed.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lenspr.models import (
    Edge,
    EdgeConfidence,
    EdgeSource,
    EdgeType,
    Node,
    NodeType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Workflow-level
_WF_NAME_RE = re.compile(r"^name:\s*(.+)", re.MULTILINE)

# Trigger block (on:)
_WF_ON_RE = re.compile(r"^on:\s*(.*)", re.MULTILINE)

# Job needs — inline list: needs: [build, test] or single: needs: build
_JOB_NEEDS_INLINE_RE = re.compile(r"needs:\s*\[([^\]]+)\]")
_JOB_NEEDS_SINGLE_RE = re.compile(r"needs:\s+(\w[\w-]*)\s*$")

# Step uses — uses: actions/checkout@v4
_STEP_USES_RE = re.compile(r"uses:\s*([^\s#]+)")

# Step run — run: npm test
_STEP_RUN_RE = re.compile(r"run:\s*(.*)")

# Secret references — ${{ secrets.DEPLOY_KEY }}
_SECRET_REF_RE = re.compile(r"\$\{\{\s*secrets\.(\w+)\s*\}\}")

# Env var references — ${{ env.NODE_ENV }} or ${{ vars.APP_NAME }}
_ENV_REF_RE = re.compile(r"\$\{\{\s*(?:env|vars)\.(\w+)\s*\}\}")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CiStep:
    """A single step in a CI job."""

    name: str = ""
    uses: str = ""  # e.g., "actions/checkout@v4"
    run: str = ""  # e.g., "npm test"


@dataclass
class CiJob:
    """A CI job within a workflow."""

    name: str  # job key (e.g., "build", "test", "deploy")
    node_id: str  # e.g., "ci.github.ci.build"
    needs: list[str] = field(default_factory=list)
    steps: list[CiStep] = field(default_factory=list)
    env_refs: list[str] = field(default_factory=list)
    secret_refs: list[str] = field(default_factory=list)


@dataclass
class CiWorkflow:
    """A parsed CI/CD workflow file."""

    file_path: str
    node_id: str  # e.g., "ci.github.ci"
    name: str  # workflow name from YAML
    triggers: list[str] = field(default_factory=list)
    jobs: dict[str, CiJob] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge_id() -> str:
    return uuid.uuid4().hex[:12]


def _workflow_name_from_path(file_path: Path, root_path: Path) -> str:
    """Derive a workflow name from the file path.

    Example: .github/workflows/ci.yml → "ci"
    """
    try:
        rel = file_path.relative_to(root_path)
    except ValueError:
        rel = file_path
    stem = rel.stem  # "ci" from "ci.yml"
    return stem


# ---------------------------------------------------------------------------
# Minimal YAML parser — GitHub Actions specific
# ---------------------------------------------------------------------------


def _parse_workflow_minimal(text: str) -> dict:
    """Parse a GitHub Actions workflow YAML without PyYAML.

    Returns a dict with keys: name, triggers, jobs.
    Each job has: needs, steps, env_refs, secret_refs.

    Not a full YAML parser — covers the 80% case for GitHub Actions.
    """
    result: dict = {
        "name": "",
        "triggers": [],
        "jobs": {},
    }

    # Extract workflow name
    m = _WF_NAME_RE.search(text)
    if m:
        result["name"] = m.group(1).strip().strip("'\"")

    # Extract triggers
    _parse_triggers(text, result)

    # Extract all secret and env refs (workflow-wide, assigned to jobs later)
    all_secrets = set(_SECRET_REF_RE.findall(text))
    all_env_refs = set(_ENV_REF_RE.findall(text))

    # Parse jobs
    _parse_jobs(text, result, all_secrets, all_env_refs)

    return result


def _parse_triggers(text: str, result: dict) -> None:
    """Extract trigger events from the 'on:' block."""
    lines = text.splitlines()
    in_on = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        leading = len(line) - len(line.lstrip())

        # on: push  (inline)
        if stripped.startswith("on:") and leading == 0:
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                # on: [push, pull_request]
                if rest.startswith("["):
                    triggers = rest.strip("[]").split(",")
                    result["triggers"] = [t.strip().strip("'\"") for t in triggers if t.strip()]
                else:
                    result["triggers"] = [rest.strip("'\"")]
                return
            in_on = True
            _on_indent = leading  # noqa: F841
            continue

        if in_on:
            # End of on: block — another top-level key
            if leading == 0 and stripped.endswith(":"):
                return
            # Trigger event names at indent 2
            if leading == 2 and stripped.endswith(":"):
                result["triggers"].append(stripped.rstrip(":").strip())
            elif leading == 2 and not stripped.startswith("-"):
                # on:\n  push (without colon) — rare but valid
                if stripped.isidentifier():
                    result["triggers"].append(stripped)


def _parse_jobs(text: str, result: dict, all_secrets: set, all_env_refs: set) -> None:
    """Extract jobs, their dependencies, and steps."""
    lines = text.splitlines()
    in_jobs = False
    current_job: str | None = None
    current_step: CiStep | None = None
    in_steps = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        leading = len(line) - len(line.lstrip())

        # Top-level "jobs:" key
        if stripped == "jobs:" and leading == 0:
            in_jobs = True
            continue

        if not in_jobs:
            continue

        # Another top-level key → end of jobs
        if leading == 0 and stripped.endswith(":"):
            in_jobs = False
            continue

        # Job name (indent 2)
        if leading == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            job_name = stripped.rstrip(":").strip()
            current_job = job_name
            current_step = None
            in_steps = False
            result["jobs"][job_name] = {
                "needs": [],
                "steps": [],
                "env_refs": [],
                "secret_refs": [],
            }
            continue

        if current_job is None:
            continue

        job = result["jobs"][current_job]

        # Collect secret/env refs for every line inside a job block
        # (must happen before any continue statements below)
        for secret in _SECRET_REF_RE.findall(line):
            if secret not in job["secret_refs"]:
                job["secret_refs"].append(secret)
        for env_ref in _ENV_REF_RE.findall(line):
            if env_ref not in job["env_refs"]:
                job["env_refs"].append(env_ref)

        # Job properties (indent 4+)
        if leading >= 4:
            # steps: header
            if stripped == "steps:" and leading == 4:
                in_steps = True
                current_step = None
                continue

            # Other section headers at indent 4
            if leading == 4 and stripped.endswith(":") and not stripped.startswith("-"):
                section = stripped.rstrip(":").strip()
                if section != "steps":
                    in_steps = False
                    current_step = None
                continue

            # needs: inline or single
            if stripped.startswith("needs:"):
                m = _JOB_NEEDS_INLINE_RE.match(stripped)
                if m:
                    deps = m.group(1).split(",")
                    job["needs"] = [d.strip().strip("'\"") for d in deps if d.strip()]
                else:
                    m2 = _JOB_NEEDS_SINGLE_RE.match(stripped)
                    if m2:
                        job["needs"] = [m2.group(1)]
                continue

            # needs: list items
            if stripped.startswith("- ") and leading == 6:
                # Could be needs list item or steps list item
                # If in_steps, it's a step
                if not in_steps and not current_step:
                    # Might be a needs list item
                    item = stripped[2:].strip().strip("'\"")
                    if item and item.isidentifier():
                        job["needs"].append(item)
                    continue

            # Step items (under steps:)
            if in_steps:
                if stripped.startswith("- ") and leading == 6:
                    # New step
                    current_step = {"name": "", "uses": "", "run": ""}
                    job["steps"].append(current_step)
                    # The line itself may contain name: or uses:
                    rest = stripped[2:].strip()
                    if rest.startswith("name:"):
                        current_step["name"] = rest.split(":", 1)[1].strip().strip("'\"")
                    elif rest.startswith("uses:"):
                        current_step["uses"] = rest.split(":", 1)[1].strip()
                    elif rest.startswith("run:"):
                        current_step["run"] = rest.split(":", 1)[1].strip()
                    continue

                if current_step is not None and leading >= 8:
                    if stripped.startswith("name:"):
                        current_step["name"] = stripped.split(":", 1)[1].strip().strip("'\"")
                    elif stripped.startswith("uses:"):
                        current_step["uses"] = stripped.split(":", 1)[1].strip()
                    elif stripped.startswith("run:"):
                        current_step["run"] = stripped.split(":", 1)[1].strip()
                    continue



# ---------------------------------------------------------------------------
# CiMapper class
# ---------------------------------------------------------------------------


class CiMapper:
    """Parse CI/CD workflow files and create graph nodes/edges."""

    def __init__(self) -> None:
        self._workflows: list[CiWorkflow] = []

    def parse_github_workflow(self, file_path: Path, root_path: Path) -> CiWorkflow | None:
        """Parse a single GitHub Actions workflow YAML file."""
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("Cannot read workflow file: %s", file_path)
            return None

        wf_name = _workflow_name_from_path(file_path, root_path)
        node_id = f"ci.github.{wf_name}"

        parsed = _parse_workflow_minimal(text)

        try:
            rel_path = str(file_path.relative_to(root_path))
        except ValueError:
            rel_path = str(file_path)

        workflow = CiWorkflow(
            file_path=rel_path,
            node_id=node_id,
            name=parsed["name"] or wf_name,
            triggers=parsed["triggers"],
        )

        for job_key, job_data in parsed["jobs"].items():
            job_node_id = f"{node_id}.{job_key}"
            steps = [
                CiStep(
                    name=s.get("name", ""),
                    uses=s.get("uses", ""),
                    run=s.get("run", ""),
                )
                for s in job_data["steps"]
            ]
            job = CiJob(
                name=job_key,
                node_id=job_node_id,
                needs=job_data["needs"],
                steps=steps,
                env_refs=job_data["env_refs"],
                secret_refs=job_data["secret_refs"],
            )
            workflow.jobs[job_key] = job

        self._workflows.append(workflow)
        return workflow

    def get_ci_nodes(self) -> list[Node]:
        """Create virtual nodes for workflows and jobs."""
        nodes: list[Node] = []

        for wf in self._workflows:
            # Build source summary for workflow node
            triggers_str = ", ".join(wf.triggers) if wf.triggers else "manual"
            jobs_str = ", ".join(wf.jobs.keys()) if wf.jobs else "none"
            wf_source = (
                f"# Workflow: {wf.name}\n"
                f"# File: {wf.file_path}\n"
                f"# Triggers: {triggers_str}\n"
                f"# Jobs: {jobs_str}\n"
            )

            nodes.append(Node(
                id=wf.node_id,
                type=NodeType.MODULE,
                name=wf.name,
                qualified_name=wf.node_id,
                file_path=wf.file_path,
                start_line=1,
                end_line=1,
                source_code=wf_source,
                docstring=f"GitHub Actions workflow: {wf.name} (triggers: {triggers_str})",
            ))

            # Job nodes
            for job_key, job in wf.jobs.items():
                steps_lines = []
                for s in job.steps:
                    if s.uses:
                        steps_lines.append(f"  - uses: {s.uses}")
                    elif s.run:
                        steps_lines.append(f"  - run: {s.run}")
                    if s.name:
                        steps_lines[-1] = (
                            f"  - name: {s.name}\n" + steps_lines[-1]
                            if steps_lines
                            else f"  - name: {s.name}"
                        )

                needs_str = f"needs: [{', '.join(job.needs)}]" if job.needs else ""
                job_source = (
                    f"# Job: {job_key}\n"
                    + (f"# {needs_str}\n" if needs_str else "")
                    + "# Steps:\n"
                    + "\n".join(steps_lines)
                ) if steps_lines else f"# Job: {job_key} (no steps)"

                nodes.append(Node(
                    id=job.node_id,
                    type=NodeType.BLOCK,
                    name=job_key,
                    qualified_name=job.node_id,
                    file_path=wf.file_path,
                    start_line=1,
                    end_line=1,
                    source_code=job_source,
                    docstring=(
                        f"CI job: {job_key}"
                        + (f" (needs: {', '.join(job.needs)})"
                           if job.needs else "")
                    ),
                ))

        return nodes

    def match(self) -> list[Edge]:
        """Create edges for CI relationships.

        Edge types:
        - DEPENDS_ON: job → job (needs)
        - DEPENDS_ON: step → external action (uses)
        - USES_ENV: job → env/secret reference
        """
        edges: list[Edge] = []

        for wf in self._workflows:
            for job_key, job in wf.jobs.items():
                # Job dependencies (needs)
                for dep in job.needs:
                    dep_node_id = f"{wf.node_id}.{dep}"
                    edges.append(Edge(
                        id=_edge_id(),
                        from_node=job.node_id,
                        to_node=dep_node_id,
                        type=EdgeType.DEPENDS_ON,
                        confidence=EdgeConfidence.RESOLVED,
                        source=EdgeSource.STATIC,
                        metadata={"ci_relation": "needs"},
                    ))

                # Step uses → external action
                for step in job.steps:
                    if step.uses:
                        action_node_id = f"ci.action.{step.uses.split('@')[0]}"
                        edges.append(Edge(
                            id=_edge_id(),
                            from_node=job.node_id,
                            to_node=action_node_id,
                            type=EdgeType.DEPENDS_ON,
                            confidence=EdgeConfidence.INFERRED,
                            source=EdgeSource.STATIC,
                            metadata={"ci_relation": "uses_action", "action": step.uses},
                        ))

                # Secret refs
                for secret in job.secret_refs:
                    edges.append(Edge(
                        id=_edge_id(),
                        from_node=job.node_id,
                        to_node=f"env.secret.{secret}",
                        type=EdgeType.USES_ENV,
                        confidence=EdgeConfidence.RESOLVED,
                        source=EdgeSource.STATIC,
                        metadata={"ci_relation": "secret_ref"},
                    ))

                # Env/vars refs
                for env_ref in job.env_refs:
                    edges.append(Edge(
                        id=_edge_id(),
                        from_node=job.node_id,
                        to_node=f"env.var.{env_ref}",
                        type=EdgeType.USES_ENV,
                        confidence=EdgeConfidence.RESOLVED,
                        source=EdgeSource.STATIC,
                        metadata={"ci_relation": "env_ref"},
                    ))

        return edges
