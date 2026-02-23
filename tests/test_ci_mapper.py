"""Tests for CI/CD workflow mapper."""

from __future__ import annotations

from pathlib import Path

from lenspr.models import NodeType
from lenspr.resolvers.ci_mapper import (
    CiMapper,
    _parse_workflow_minimal,
    _workflow_name_from_path,
)

# ---------------------------------------------------------------------------
# _workflow_name_from_path
# ---------------------------------------------------------------------------


class TestWorkflowNameFromPath:
    def test_simple_yml(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows" / "ci.yml"
        assert _workflow_name_from_path(wf, tmp_path) == "ci"

    def test_yaml_extension(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows" / "deploy.yaml"
        assert _workflow_name_from_path(wf, tmp_path) == "deploy"

    def test_hyphenated_name(self, tmp_path: Path) -> None:
        wf = tmp_path / ".github" / "workflows" / "build-and-test.yml"
        assert _workflow_name_from_path(wf, tmp_path) == "build-and-test"


# ---------------------------------------------------------------------------
# _parse_workflow_minimal — workflow name
# ---------------------------------------------------------------------------


class TestWorkflowNameParsing:
    def test_simple_name(self) -> None:
        result = _parse_workflow_minimal("name: CI\n")
        assert result["name"] == "CI"

    def test_quoted_name(self) -> None:
        result = _parse_workflow_minimal("name: 'Build & Deploy'\n")
        assert result["name"] == "Build & Deploy"

    def test_no_name(self) -> None:
        result = _parse_workflow_minimal("on: push\njobs:\n  build:\n    steps: []\n")
        assert result["name"] == ""


# ---------------------------------------------------------------------------
# _parse_workflow_minimal — triggers
# ---------------------------------------------------------------------------


class TestTriggerParsing:
    def test_inline_single(self) -> None:
        result = _parse_workflow_minimal("on: push\n")
        assert result["triggers"] == ["push"]

    def test_inline_list(self) -> None:
        result = _parse_workflow_minimal("on: [push, pull_request]\n")
        assert result["triggers"] == ["push", "pull_request"]

    def test_block_triggers(self) -> None:
        text = """\
name: CI
on:
  push:
  pull_request:
jobs:
  build:
    steps: []
"""
        result = _parse_workflow_minimal(text)
        assert "push" in result["triggers"]
        assert "pull_request" in result["triggers"]

    def test_no_triggers(self) -> None:
        result = _parse_workflow_minimal("name: CI\njobs:\n  build:\n    steps: []\n")
        assert result["triggers"] == []


# ---------------------------------------------------------------------------
# _parse_workflow_minimal — jobs
# ---------------------------------------------------------------------------


class TestJobParsing:
    def test_single_job(self) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm test
"""
        result = _parse_workflow_minimal(text)
        assert "build" in result["jobs"]
        job = result["jobs"]["build"]
        assert len(job["steps"]) == 2
        assert job["steps"][0]["uses"] == "actions/checkout@v4"
        assert job["steps"][1]["run"] == "npm test"

    def test_job_needs_single(self) -> None:
        text = """\
jobs:
  build:
    steps:
      - run: npm build
  test:
    needs: build
    steps:
      - run: npm test
"""
        result = _parse_workflow_minimal(text)
        assert result["jobs"]["test"]["needs"] == ["build"]

    def test_job_needs_inline_list(self) -> None:
        text = """\
jobs:
  deploy:
    needs: [build, test]
    steps:
      - run: npm deploy
"""
        result = _parse_workflow_minimal(text)
        assert set(result["jobs"]["deploy"]["needs"]) == {"build", "test"}

    def test_multiple_jobs(self) -> None:
        text = """\
jobs:
  lint:
    steps:
      - run: npm run lint
  test:
    steps:
      - run: npm test
  deploy:
    needs: [lint, test]
    steps:
      - run: npm deploy
"""
        result = _parse_workflow_minimal(text)
        assert set(result["jobs"].keys()) == {"lint", "test", "deploy"}

    def test_step_with_name(self) -> None:
        text = """\
jobs:
  build:
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
      - name: Run tests
        run: npm test
"""
        result = _parse_workflow_minimal(text)
        steps = result["jobs"]["build"]["steps"]
        assert steps[0]["name"] == "Checkout code"
        assert steps[0]["uses"] == "actions/checkout@v4"
        assert steps[1]["name"] == "Run tests"
        assert steps[1]["run"] == "npm test"


# ---------------------------------------------------------------------------
# _parse_workflow_minimal — secrets and env refs
# ---------------------------------------------------------------------------


class TestSecretEnvRefs:
    def test_secret_reference(self) -> None:
        text = """\
jobs:
  deploy:
    steps:
      - run: echo ${{ secrets.DEPLOY_KEY }}
"""
        result = _parse_workflow_minimal(text)
        assert "DEPLOY_KEY" in result["jobs"]["deploy"]["secret_refs"]

    def test_env_reference(self) -> None:
        text = """\
jobs:
  build:
    steps:
      - run: echo ${{ env.NODE_ENV }}
"""
        result = _parse_workflow_minimal(text)
        assert "NODE_ENV" in result["jobs"]["build"]["env_refs"]

    def test_vars_reference(self) -> None:
        text = """\
jobs:
  build:
    steps:
      - run: echo ${{ vars.APP_NAME }}
"""
        result = _parse_workflow_minimal(text)
        assert "APP_NAME" in result["jobs"]["build"]["env_refs"]

    def test_multiple_refs_in_job(self) -> None:
        text = """\
jobs:
  deploy:
    env:
      TOKEN: ${{ secrets.GH_TOKEN }}
    steps:
      - run: curl -H "Authorization: ${{ secrets.API_KEY }}" ${{ env.API_URL }}
"""
        result = _parse_workflow_minimal(text)
        job = result["jobs"]["deploy"]
        assert "GH_TOKEN" in job["secret_refs"]
        assert "API_KEY" in job["secret_refs"]
        assert "API_URL" in job["env_refs"]


# ---------------------------------------------------------------------------
# CiMapper.parse_github_workflow
# ---------------------------------------------------------------------------


class TestCiMapperParsing:
    def _write_workflow(self, tmp_path: Path, filename: str, content: str) -> Path:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        wf_file = wf_dir / filename
        wf_file.write_text(content)
        return wf_file

    def test_parse_creates_workflow(self, tmp_path: Path) -> None:
        wf_file = self._write_workflow(
            tmp_path, "ci.yml",
            "name: CI\non: push\njobs:\n  build:\n"
            "    steps:\n      - run: npm build\n",
        )
        mapper = CiMapper()
        wf = mapper.parse_github_workflow(wf_file, tmp_path)
        assert wf is not None
        assert wf.name == "CI"
        assert wf.node_id == "ci.github.ci"
        assert "push" in wf.triggers

    def test_parse_creates_jobs(self, tmp_path: Path) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
  test:
    needs: build
    steps:
      - run: npm test
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        wf = mapper.parse_github_workflow(wf_file, tmp_path)
        assert "build" in wf.jobs
        assert "test" in wf.jobs
        assert wf.jobs["test"].needs == ["build"]

    def test_nonexistent_file_returns_none(self, tmp_path: Path) -> None:
        mapper = CiMapper()
        result = mapper.parse_github_workflow(tmp_path / "nope.yml", tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# CiMapper.get_ci_nodes
# ---------------------------------------------------------------------------


class TestCiNodes:
    def _write_workflow(self, tmp_path: Path, filename: str, content: str) -> Path:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        wf_file = wf_dir / filename
        wf_file.write_text(content)
        return wf_file

    def test_workflow_node_created(self, tmp_path: Path) -> None:
        text = "name: CI\non: push\njobs:\n  build:\n    steps:\n      - run: echo hi\n"
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        nodes = mapper.get_ci_nodes()

        wf_nodes = [n for n in nodes if n.id == "ci.github.ci"]
        assert len(wf_nodes) == 1
        assert wf_nodes[0].type == NodeType.MODULE
        assert wf_nodes[0].name == "CI"

    def test_job_nodes_created(self, tmp_path: Path) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - run: npm build
  test:
    steps:
      - run: npm test
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        nodes = mapper.get_ci_nodes()

        job_nodes = [n for n in nodes if n.type == NodeType.BLOCK]
        assert len(job_nodes) == 2
        node_ids = {n.id for n in job_nodes}
        assert "ci.github.ci.build" in node_ids
        assert "ci.github.ci.test" in node_ids

    def test_multiple_workflows(self, tmp_path: Path) -> None:
        ci = "name: CI\non: push\njobs:\n  build:\n    steps:\n      - run: echo ci\n"
        deploy = "name: Deploy\non: push\njobs:\n  deploy:\n    steps:\n      - run: echo deploy\n"
        self._write_workflow(tmp_path, "ci.yml", ci)
        self._write_workflow(tmp_path, "deploy.yml", deploy)

        mapper = CiMapper()
        wf_dir = tmp_path / ".github" / "workflows"
        for wf in sorted(wf_dir.glob("*.yml")):
            mapper.parse_github_workflow(wf, tmp_path)

        nodes = mapper.get_ci_nodes()
        wf_nodes = [n for n in nodes if n.type == NodeType.MODULE]
        assert len(wf_nodes) == 2


# ---------------------------------------------------------------------------
# CiMapper.match — edge creation
# ---------------------------------------------------------------------------


class TestCiEdges:
    def _write_workflow(self, tmp_path: Path, filename: str, content: str) -> Path:
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        wf_file = wf_dir / filename
        wf_file.write_text(content)
        return wf_file

    def test_needs_creates_depends_on_edge(self, tmp_path: Path) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - run: npm build
  test:
    needs: build
    steps:
      - run: npm test
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        edges = mapper.match()

        needs_edges = [e for e in edges if e.metadata.get("ci_relation") == "needs"]
        assert len(needs_edges) == 1
        assert needs_edges[0].from_node == "ci.github.ci.test"
        assert needs_edges[0].to_node == "ci.github.ci.build"
        assert needs_edges[0].type.value == "depends_on"

    def test_uses_action_creates_edge(self, tmp_path: Path) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v3
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        edges = mapper.match()

        action_edges = [e for e in edges if e.metadata.get("ci_relation") == "uses_action"]
        assert len(action_edges) == 2
        targets = {e.to_node for e in action_edges}
        assert "ci.action.actions/checkout" in targets
        assert "ci.action.actions/setup-node" in targets

    def test_secret_ref_creates_uses_env_edge(self, tmp_path: Path) -> None:
        text = """\
name: Deploy
on: push
jobs:
  deploy:
    steps:
      - run: echo ${{ secrets.DEPLOY_KEY }}
"""
        wf_file = self._write_workflow(tmp_path, "deploy.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        edges = mapper.match()

        secret_edges = [e for e in edges if e.metadata.get("ci_relation") == "secret_ref"]
        assert len(secret_edges) == 1
        assert secret_edges[0].to_node == "env.secret.DEPLOY_KEY"
        assert secret_edges[0].type.value == "uses_env"

    def test_env_ref_creates_uses_env_edge(self, tmp_path: Path) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - run: echo ${{ env.NODE_ENV }}
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        edges = mapper.match()

        env_edges = [e for e in edges if e.metadata.get("ci_relation") == "env_ref"]
        assert len(env_edges) == 1
        assert env_edges[0].to_node == "env.var.NODE_ENV"

    def test_no_edges_when_no_refs(self, tmp_path: Path) -> None:
        text = """\
name: CI
on: push
jobs:
  build:
    steps:
      - run: echo hello
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)
        edges = mapper.match()

        # No needs, no uses, no secrets, no env refs
        assert len(edges) == 0

    def test_complex_workflow_end_to_end(self, tmp_path: Path) -> None:
        text = """\
name: Build and Deploy
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm run lint
  test:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v3
      - run: npm test
  deploy:
    needs: [lint, test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy
        run: ./deploy.sh
        env:
          TOKEN: ${{ secrets.DEPLOY_TOKEN }}
          API_URL: ${{ env.PROD_API_URL }}
"""
        wf_file = self._write_workflow(tmp_path, "ci.yml", text)
        mapper = CiMapper()
        mapper.parse_github_workflow(wf_file, tmp_path)

        # Nodes
        nodes = mapper.get_ci_nodes()
        assert len(nodes) == 4  # 1 workflow + 3 jobs
        wf_node = [n for n in nodes if n.type == NodeType.MODULE][0]
        assert wf_node.name == "Build and Deploy"

        # Edges
        edges = mapper.match()
        needs_edges = [e for e in edges if e.metadata.get("ci_relation") == "needs"]
        assert len(needs_edges) == 3  # test→lint, deploy→lint, deploy→test

        action_edges = [e for e in edges if e.metadata.get("ci_relation") == "uses_action"]
        assert len(action_edges) == 4  # 3 checkouts + 1 setup-node

        secret_edges = [e for e in edges if e.metadata.get("ci_relation") == "secret_ref"]
        assert len(secret_edges) == 1
        assert secret_edges[0].to_node == "env.secret.DEPLOY_TOKEN"

        env_edges = [e for e in edges if e.metadata.get("ci_relation") == "env_ref"]
        assert len(env_edges) == 1
        assert env_edges[0].to_node == "env.var.PROD_API_URL"
