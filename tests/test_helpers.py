"""Tests for get_proactive_warnings — the only automatic safety mechanism.

Covers:
1. Empty result when node not in graph
2. High-impact warning (>10 direct callers)
3. Moderate-impact warning (>5 callers)
4. No-tests warning when no test callers exist
5. No warning when test caller detected by caller name / file
6. No warning when test found via naming-convention DB search
7. Hardcoded-secret detection (password, api_key, token, secret)
8. IO without try/except → NO ERROR HANDLING warning
9. IO with try/except → no warning
10. No false-positives on clean code
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from lenspr.tools.helpers import get_proactive_warnings

# ── helpers ──────────────────────────────────────────────────────────────────

NODE_ID = "app.module.my_func"
SIMPLE_SOURCE = "def my_func(): return 42"


def _make_graph(
    node_id: str = NODE_ID,
    callers: list[tuple[str, str, str]] | None = None,
) -> nx.DiGraph:
    """Build a minimal DiGraph.

    callers: list of (caller_id, caller_name, caller_file)
    """
    G = nx.DiGraph()
    G.add_node(node_id, name="my_func", file_path="app/module.py", type="function")
    for caller_id, caller_name, caller_file in callers or []:
        G.add_node(caller_id, name=caller_name, file_path=caller_file, type="function")
        G.add_edge(caller_id, node_id, type="calls")
    return G


def _make_ctx(G: nx.DiGraph) -> MagicMock:
    ctx = MagicMock()
    ctx.get_graph.return_value = G
    ctx.graph_db = ":memory:"
    ctx.project_root = Path("/fake/root")
    return ctx


def _warnings(source: str = SIMPLE_SOURCE, callers=None) -> list[str]:
    """Run get_proactive_warnings with mocked DB and no arch rules."""
    G = _make_graph(callers=callers)
    ctx = _make_ctx(G)
    with (
        patch("lenspr.tools.helpers.database") as mock_db,
        patch("lenspr.tools.helpers.graph") as mock_graph,
        patch("lenspr.tools.safety.check_arch_violations", return_value=[]),
    ):
        mock_db.search_nodes.return_value = []
        mock_graph.detect_circular_imports.return_value = []
        return get_proactive_warnings(NODE_ID, source, ctx)


# ── tests ─────────────────────────────────────────────────────────────────────


class TestNodeNotInGraph:
    def test_returns_empty_list_when_node_absent(self):
        G = nx.DiGraph()  # empty — NODE_ID not present
        ctx = _make_ctx(G)
        result = get_proactive_warnings(NODE_ID, SIMPLE_SOURCE, ctx)
        assert result == []


class TestImpactWarnings:
    def test_no_impact_warning_for_few_callers(self):
        callers = [(f"app.c{i}", f"c{i}", "app/other.py") for i in range(3)]
        ws = _warnings(callers=callers)
        assert not any("IMPACT" in w for w in ws)

    def test_moderate_impact_at_6_callers(self):
        callers = [(f"app.c{i}", f"c{i}", "app/other.py") for i in range(6)]
        ws = _warnings(callers=callers)
        assert any("MODERATE IMPACT" in w for w in ws)
        assert not any("HIGH IMPACT" in w for w in ws)

    def test_high_impact_at_11_callers(self):
        callers = [(f"app.c{i}", f"c{i}", "app/other.py") for i in range(11)]
        ws = _warnings(callers=callers)
        assert any("HIGH IMPACT" in w for w in ws)

    def test_high_impact_not_moderate_at_11_callers(self):
        callers = [(f"app.c{i}", f"c{i}", "app/other.py") for i in range(11)]
        ws = _warnings(callers=callers)
        # Only HIGH, not MODERATE
        assert not any("MODERATE IMPACT" in w for w in ws)


class TestNoTestsWarning:
    def test_warns_when_no_test_callers(self):
        ws = _warnings()
        assert any("NO TESTS" in w for w in ws)

    def test_no_warning_when_caller_name_starts_with_test(self):
        callers = [("tests.test_mod.test_my_func", "test_my_func", "tests/test_mod.py")]
        ws = _warnings(callers=callers)
        assert not any("NO TESTS" in w for w in ws)

    def test_no_warning_when_caller_file_contains_test_underscore(self):
        # Condition: "test_" in pred_file — file must contain "test_" substring
        callers = [("tests.test_utils.helper", "helper", "tests/test_utils.py")]
        ws = _warnings(callers=callers)
        assert not any("NO TESTS" in w for w in ws)

    def test_no_warning_when_test_found_by_naming_convention(self):
        """DB search finds test_my_func even without a call edge."""
        G = _make_graph()
        ctx = _make_ctx(G)
        with (
            patch("lenspr.tools.helpers.database") as mock_db,
            patch("lenspr.tools.helpers.graph") as mock_graph,
        ):
            mock_db.search_nodes.return_value = [MagicMock()]  # one matching test
            mock_graph.detect_circular_imports.return_value = []
            ws = get_proactive_warnings(NODE_ID, SIMPLE_SOURCE, ctx)
        assert not any("NO TESTS" in w for w in ws)


class TestHardcodedSecrets:
    @pytest.mark.parametrize(
        "source",
        [
            "def f():\n    password = 'supersecret'\n",
            "def f():\n    passwd = 'hunter2'\n",
            "def f():\n    api_key = 'sk-abc123456'\n",
            "def f():\n    apikey = 'sk-abc123456'\n",
            "def f():\n    secret_key = 'my_secret_key'\n",
            "def f():\n    token = 'eyJhbGciOiJIUzI1NiJ9.payload'\n",
            "def f():\n    secret = 'topsecret'\n",
        ],
    )
    def test_detects_hardcoded_secret(self, source: str):
        ws = _warnings(source=source)
        assert any("HARDCODED SECRET" in w for w in ws), (
            f"Expected HARDCODED SECRET warning for source: {source!r}"
        )

    def test_no_false_positive_on_param_name(self):
        """password as a *parameter* name (no assignment to literal) is not a secret."""
        source = "def login(password: str) -> bool:\n    return check(password)"
        ws = _warnings(source=source)
        assert not any("HARDCODED SECRET" in w for w in ws)

    def test_no_false_positive_on_env_var(self):
        source = "def f():\n    api_key = os.environ['API_KEY']\n"
        ws = _warnings(source=source)
        assert not any("HARDCODED SECRET" in w for w in ws)


class TestIOWithoutErrorHandling:
    @pytest.mark.parametrize(
        "io_snippet",
        [
            "open('file.txt')",
            "requests.get(url)",
            "httpx.post(url)",
            "conn.execute(sql)",
            "cursor.fetchall()",
            "subprocess.run(cmd)",
            "socket.connect(addr)",
        ],
    )
    def test_warns_for_io_without_try(self, io_snippet: str):
        source = f"def f():\n    result = {io_snippet}\n    return result"
        ws = _warnings(source=source)
        assert any("NO ERROR HANDLING" in w for w in ws), (
            f"Expected NO ERROR HANDLING warning for {io_snippet!r}"
        )

    def test_no_warning_when_try_present_with_io(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        with open('file.txt') as fh:\n"
            "            return fh.read()\n"
            "    except IOError:\n"
            "        return None\n"
        )
        ws = _warnings(source=source)
        assert not any("NO ERROR HANDLING" in w for w in ws)

    def test_no_warning_for_pure_computation(self):
        source = "def f(x: int) -> int:\n    return x * 2 + 1"
        ws = _warnings(source=source)
        assert not any("NO ERROR HANDLING" in w for w in ws)


class TestCircularDependency:
    def test_warns_when_node_in_cycle(self):
        G = _make_graph()
        ctx = _make_ctx(G)
        # node_id = "app.module.my_func", so module = "app.module"
        cycle = ["app.module", "app.other"]
        with (
            patch("lenspr.tools.helpers.database") as mock_db,
            patch("lenspr.tools.helpers.graph") as mock_graph,
        ):
            mock_db.search_nodes.return_value = []
            mock_graph.detect_circular_imports.return_value = [cycle]
            ws = get_proactive_warnings(NODE_ID, SIMPLE_SOURCE, ctx)
        assert any("CIRCULAR" in w for w in ws)

    def test_no_warning_when_cycle_does_not_contain_node_module(self):
        G = _make_graph()
        ctx = _make_ctx(G)
        # cycle is in unrelated modules
        cycle = ["other.pkg", "another.pkg"]
        with (
            patch("lenspr.tools.helpers.database") as mock_db,
            patch("lenspr.tools.helpers.graph") as mock_graph,
        ):
            mock_db.search_nodes.return_value = []
            mock_graph.detect_circular_imports.return_value = [cycle]
            ws = get_proactive_warnings(NODE_ID, SIMPLE_SOURCE, ctx)
        assert not any("CIRCULAR" in w for w in ws)
