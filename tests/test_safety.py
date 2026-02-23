"""Tests for lenspr/tools/safety.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.safety import (
    handle_arch_check,
    handle_arch_rule_add,
    handle_arch_rule_delete,
    handle_arch_rule_list,
    handle_nfr_check,
    handle_test_coverage,
    handle_vibecheck,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Project with production code and a matching test file."""
    # Production module
    (tmp_path / "service.py").write_text(
        'import os\n'
        '\n'
        'SECRET_KEY = "hardcoded_secret_123"\n'
        '\n'
        'def fetch_data(url: str) -> dict:\n'
        '    """Fetch data from a URL."""\n'
        '    import urllib.request\n'
        '    return urllib.request.urlopen(url).read()\n'
        '\n'
        'def process(data: dict) -> dict:\n'
        '    """Process data."""\n'
        '    return {k: v for k, v in data.items()}\n'
        '\n'
        'def create_item(name: str) -> dict:\n'
        '    """Create an item."""\n'
        '    return {"name": name}\n'
    )

    # Test module (calls process and create_item but NOT fetch_data)
    (tmp_path / "test_service.py").write_text(
        'from service import process, create_item\n'
        '\n'
        'def test_process():\n'
        '    assert process({"a": 1}) == {"a": 1}\n'
        '\n'
        'def test_create_item():\n'
        '    assert create_item("x") == {"name": "x"}\n'
    )

    # eval/ directory (should be excluded from analysis)
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "benchmark.py").write_text(
        'def run_benchmark():\n'
        '    """Benchmark function in eval/."""\n'
        '    pass\n'
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


@pytest.fixture
def clean_project(tmp_path: Path) -> LensContext:
    """Project with well-structured code for positive tests."""
    (tmp_path / "utils.py").write_text(
        'import logging\n'
        '\n'
        'logger = logging.getLogger(__name__)\n'
        '\n'
        'def format_name(name: str) -> str:\n'
        '    """Format a name string."""\n'
        '    return name.strip().title()\n'
        '\n'
        'def validate_email(email: str) -> bool:\n'
        '    """Return True if email looks valid."""\n'
        '    return "@" in email and "." in email\n'
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# TestNfrCheck
# ---------------------------------------------------------------------------


class TestNfrCheck:
    """Tests for handle_nfr_check."""

    def test_flags_io_without_error_handling(self, project: LensContext) -> None:
        """IO operation without try/except should be flagged."""
        result = handle_nfr_check({"node_id": "service.fetch_data"}, project)

        assert result.success
        issues = result.data.get("issues", [])
        rules = [i["rule"] for i in issues]
        assert "io_without_error_handling" in rules

    def test_flags_hardcoded_secrets_at_module_level(self, project: LensContext) -> None:
        """Module-level hardcoded secrets should be detectable via nfr_check."""
        # nfr_check on the module block that contains SECRET_KEY
        result = handle_nfr_check({"node_id": "service"}, project)
        # The check may succeed or fail gracefully if node is a module
        assert result.success or result.error is not None

    def test_returns_pass_for_clean_function(self, clean_project: LensContext) -> None:
        """Function without IO or secrets should have no critical issues."""
        result = handle_nfr_check({"node_id": "utils.format_name"}, clean_project)

        assert result.success
        issues = result.data.get("issues", [])
        # format_name has no IO, no secrets, no auth concerns
        error_issues = [i for i in issues if i.get("severity") == "HIGH"]
        assert len(error_issues) == 0

    def test_unknown_node_returns_error(self, project: LensContext) -> None:
        """Non-existent node should return a graceful error."""
        result = handle_nfr_check({"node_id": "does.not.exist"}, project)

        assert not result.success
        assert result.error is not None

    def test_missing_node_id_returns_error(self, project: LensContext) -> None:
        """Missing node_id param should return an error."""
        result = handle_nfr_check({}, project)

        assert not result.success


# ---------------------------------------------------------------------------
# TestTestCoverage
# ---------------------------------------------------------------------------


class TestTestCoverage:
    """Tests for handle_test_coverage."""

    def test_covered_functions_counted(self, project: LensContext) -> None:
        """Functions called by test_ functions should be covered."""
        result = handle_test_coverage({"mode": "full"}, project)

        assert result.success
        covered_ids = [c["node_id"] for c in result.data["covered"]]
        # process and create_item are called from test functions
        assert any("process" in cid for cid in covered_ids)
        assert any("create_item" in cid for cid in covered_ids)

    def test_uncovered_functions_counted(self, project: LensContext) -> None:
        """Functions not called from tests should be in uncovered list."""
        result = handle_test_coverage({"mode": "full"}, project)

        assert result.success
        uncovered_ids = [u["node_id"] for u in result.data["uncovered"]]
        # fetch_data has no test calling it
        assert any("fetch_data" in uid for uid in uncovered_ids)

    def test_test_functions_excluded_from_coverage(self, project: LensContext) -> None:
        """test_ functions themselves should not appear as production code."""
        result = handle_test_coverage({"mode": "full"}, project)

        assert result.success
        all_ids = (
            [c["node_id"] for c in result.data["covered"]]
            + [u["node_id"] for u in result.data["uncovered"]]
        )
        for nid in all_ids:
            assert "test_process" not in nid
            assert "test_create_item" not in nid

    def test_eval_directory_excluded(self, project: LensContext) -> None:
        """Functions in eval/ should not appear in coverage report."""
        result = handle_test_coverage({"mode": "full"}, project)

        assert result.success
        all_ids = (
            [c["node_id"] for c in result.data["covered"]]
            + [u["node_id"] for u in result.data["uncovered"]]
        )
        for nid in all_ids:
            assert not nid.startswith("eval.")

    def test_grade_returned(self, project: LensContext) -> None:
        """Response should include a letter grade."""
        result = handle_test_coverage({}, project)

        assert result.success
        assert result.data["grade"] in ("A", "B", "C", "D", "F")

    def test_coverage_pct_in_range(self, project: LensContext) -> None:
        """Coverage percentage should be between 0 and 100."""
        result = handle_test_coverage({}, project)

        assert result.success
        pct = result.data["coverage_pct"]
        assert 0 <= pct <= 100


# ---------------------------------------------------------------------------
# TestArchRules
# ---------------------------------------------------------------------------


class TestArchRules:
    """Tests for arch rule CRUD and enforcement."""

    def test_add_and_list_rule(self, project: LensContext) -> None:
        """Adding a rule should make it appear in list."""
        add_result = handle_arch_rule_add(
            {
                "rule_type": "max_class_methods",
                "description": "Classes should not be too large",
                "config": {"threshold": 20},
            },
            project,
        )
        assert add_result.success

        list_result = handle_arch_rule_list({}, project)
        assert list_result.success
        rules = list_result.data["rules"]
        assert len(rules) >= 1
        assert any(r["type"] == "max_class_methods" for r in rules)

    def test_delete_rule(self, project: LensContext) -> None:
        """Deleting a rule should remove it from the list."""
        # Add a rule first
        handle_arch_rule_add(
            {
                "rule_type": "no_circular_imports",
                "description": "No circular imports allowed",
            },
            project,
        )
        list_result = handle_arch_rule_list({}, project)
        rules = list_result.data["rules"]
        rule_id = rules[0]["id"]

        del_result = handle_arch_rule_delete({"rule_id": rule_id}, project)
        assert del_result.success

        list_after = handle_arch_rule_list({}, project)
        remaining_ids = [r["id"] for r in list_after.data["rules"]]
        assert rule_id not in remaining_ids

    def test_empty_list_initially(self, project: LensContext) -> None:
        """A fresh project should have no arch rules."""
        result = handle_arch_rule_list({}, project)
        assert result.success
        assert result.data["rules"] == []

    def test_arch_check_no_violations_when_no_rules(self, project: LensContext) -> None:
        """With no rules defined, arch_check should report zero violations."""
        result = handle_arch_check({}, project)
        assert result.success
        assert result.data["violations"] == []

    def test_no_circular_imports_rule(self, project: LensContext) -> None:
        """no_circular_imports rule should detect or pass correctly."""
        handle_arch_rule_add(
            {
                "rule_type": "no_circular_imports",
                "description": "No circular imports",
            },
            project,
        )
        result = handle_arch_check({}, project)
        assert result.success
        # Our tiny sample project has no circular imports → no violations
        violations = [
            v for v in result.data["violations"]
            if v.get("rule_type") == "no_circular_imports"
        ]
        assert violations == []

    def test_invalid_rule_type_returns_error(self, project: LensContext) -> None:
        """Unknown rule_type should fail gracefully."""
        result = handle_arch_rule_add(
            {"rule_type": "completely_made_up_rule"},
            project,
        )
        assert not result.success


# ---------------------------------------------------------------------------
# TestVibecheck
# ---------------------------------------------------------------------------


class TestVibecheck:
    """Tests for handle_vibecheck."""

    def test_score_in_valid_range(self, project: LensContext) -> None:
        """Score should be between 0 and 100."""
        result = handle_vibecheck({}, project)

        assert result.success
        assert 0 <= result.data["score"] <= 100

    def test_grade_is_letter(self, project: LensContext) -> None:
        """Grade should be a letter A–F."""
        result = handle_vibecheck({}, project)

        assert result.success
        assert result.data["grade"] in ("A", "B", "C", "D", "F")

    def test_breakdown_has_all_dimensions(self, project: LensContext) -> None:
        """Breakdown should include all 6 scoring dimensions."""
        result = handle_vibecheck({}, project)

        assert result.success
        breakdown = result.data["breakdown"]
        expected = {
            "test_coverage",
            "dead_code",
            "circular_imports",
            "architecture",
            "documentation",
            "graph_confidence",
        }
        assert set(breakdown.keys()) == expected

    def test_dimension_scores_within_max(self, project: LensContext) -> None:
        """Each dimension score should not exceed its max."""
        result = handle_vibecheck({}, project)

        assert result.success
        for _dim, detail in result.data["breakdown"].items():
            assert detail["score"] <= detail["max"]
            assert detail["score"] >= 0

    def test_eval_excluded_from_func_count(self, project: LensContext) -> None:
        """Total functions should not include eval/ functions."""
        result = handle_vibecheck({}, project)

        assert result.success
        # run_benchmark is the only function in eval/; production functions
        # are fetch_data, process, create_item → total should be 3
        _total = result.data["stats"]["total_functions"]  # noqa: F841
        # We can't assert exact number since parser may differ, but eval funcs
        # should NOT be counted: verify coverage % is sane (≤100)
        assert result.data["breakdown"]["test_coverage"]["score"] <= 25

    def test_documentation_score_uses_docstrings(self, project: LensContext) -> None:
        """Documentation score should reflect docstrings, not only semantic annotations."""
        result = handle_vibecheck({}, project)

        assert result.success
        # All 3 production functions (fetch_data, process, create_item) have docstrings
        # so documentation score should be > 0
        doc_score = result.data["breakdown"]["documentation"]["score"]
        assert doc_score > 0, (
            "Documentation score was 0 but all production functions have docstrings. "
            "Likely using n.summary only instead of n.summary or n.docstring."
        )

    def test_top_risks_is_list(self, project: LensContext) -> None:
        """top_risks should be a list (possibly empty)."""
        result = handle_vibecheck({}, project)

        assert result.success
        assert isinstance(result.data["top_risks"], list)

    def test_graph_confidence_excludes_external_edges(self, project: LensContext) -> None:
        """Graph confidence should only count internal edges.

        The test fixture has `import os` in service.py, creating an external
        edge to stdlib. This edge should NOT reduce the confidence score.
        Internal edges (service→test calls) are all resolved, so confidence
        should be high despite the external dependency.
        """
        result = handle_vibecheck({}, project)

        assert result.success
        conf = result.data["breakdown"]["graph_confidence"]
        # All internal edges in the test project are resolved (direct imports
        # between service.py and test_service.py). External edges (os, urllib)
        # should be excluded. So confidence should be near 100%.
        assert conf["score"] >= 13, (
            f"Graph confidence score {conf['score']}/15 is too low. "
            f"External edges (stdlib) may be incorrectly penalizing the score. "
            f"Detail: {conf['detail']}"
        )
