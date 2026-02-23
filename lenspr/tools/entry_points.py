"""Declarative entry point pattern registry for dead code detection.

Entry points are nodes that serve as roots for reachability analysis.
Any node not reachable from an entry point is considered dead code.

Patterns are declarative data descriptions — each EntryPointPattern
specifies which node attribute to check, how to match, and what values
to look for.  The evaluation loop applies all patterns in a single pass
over the graph nodes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class MatchOp(Enum):
    """How to match a string value against pattern values."""
    EXACT = "exact"        # value in values
    PREFIX = "prefix"      # value.startswith(any value)
    SUFFIX = "suffix"      # value.endswith(any value)
    CONTAINS = "contains"  # any value in string


class CheckField(Enum):
    """Which node attribute to check."""
    NAME = "name"
    FILE_PATH = "file_path"
    SOURCE = "source_code"
    TYPE = "type"


@dataclass(frozen=True, slots=True)
class EntryPointPattern:
    """A single declarative rule for recognizing entry point nodes.

    Each pattern checks one field of a graph node using one match operation.
    Multiple values are OR-ed: if ANY value matches, the pattern fires.

    type_filter restricts which node types this pattern applies to.
    None means "apply to all types".
    """
    category: str
    field: CheckField
    op: MatchOp
    values: tuple[str, ...]
    type_filter: tuple[str, ...] | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

ENTRY_POINT_PATTERNS: tuple[EntryPointPattern, ...] = (
    # === ALWAYS entry points (unconditional) ===

    EntryPointPattern(
        "main", CheckField.NAME, MatchOp.EXACT,
        ("main", "__main__"),
        description="Main entry points",
    ),
    EntryPointPattern(
        "test", CheckField.NAME, MatchOp.PREFIX,
        ("test_",),
        description="Test functions by name prefix",
    ),
    EntryPointPattern(
        "test", CheckField.FILE_PATH, MatchOp.PREFIX,
        ("tests/",),
        description="All nodes in test files",
    ),
    EntryPointPattern(
        "structural", CheckField.TYPE, MatchOp.EXACT,
        ("block",),
        description="Module-level blocks (if __name__ == '__main__', etc.)",
    ),
    EntryPointPattern(
        "structural", CheckField.TYPE, MatchOp.EXACT,
        ("class",),
        description="All classes (methods reached through them)",
    ),

    # === CLI entry points ===

    EntryPointPattern(
        "cli", CheckField.NAME, MatchOp.EXACT,
        ("cli", "app", "run", "main_cli"),
        description="CLI module and app names",
    ),
    EntryPointPattern(
        "cli", CheckField.NAME, MatchOp.PREFIX,
        ("cmd_",),
        description="CLI commands (cmd_init, cmd_sync, etc.)",
    ),

    # === MCP and tool handlers ===

    EntryPointPattern(
        "mcp", CheckField.FILE_PATH, MatchOp.CONTAINS,
        ("mcp_server",),
        description="MCP server module (all nodes are entry points)",
    ),
    EntryPointPattern(
        "handler", CheckField.NAME, MatchOp.PREFIX,
        ("handle_",),
        description="Tool handlers (dynamically dispatched)",
    ),
    EntryPointPattern(
        "handler", CheckField.NAME, MatchOp.EXACT,
        ("handle_tool_call",),
        description="Tool call dispatcher",
    ),

    # === Web/API handlers ===

    EntryPointPattern(
        "web", CheckField.NAME, MatchOp.CONTAINS,
        ("_handler", "_endpoint", "_view", "_route"),
        type_filter=("function",),
        description="Web handler name patterns",
    ),
    EntryPointPattern(
        "web", CheckField.FILE_PATH, MatchOp.CONTAINS,
        (
            "/router", "/routes", "/views", "/api/",
            "router.py", "routes.py", "views.py", "endpoints.py",
        ),
        type_filter=("function",),
        description="Web framework route files",
    ),
    EntryPointPattern(
        "web", CheckField.SOURCE, MatchOp.CONTAINS,
        (
            "@app.", "@router.", "@bp.", "@api.",
            "@route", "@get", "@post", "@put", "@delete", "@patch",
            "@websocket",
            "Depends(",
        ),
        type_filter=("function",),
        description="Web framework decorated endpoints",
    ),
    EntryPointPattern(
        "web", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@st.cache", "@st.experimental", "st.button(", "st.form("),
        type_filter=("function",),
        description="Streamlit decorators and callbacks",
    ),
    EntryPointPattern(
        "web", CheckField.FILE_PATH, MatchOp.CONTAINS,
        ("frontend",),
        type_filter=("function",),
        description="Streamlit/frontend app files",
    ),

    # === Database migrations ===

    EntryPointPattern(
        "migration", CheckField.NAME, MatchOp.EXACT,
        ("upgrade", "downgrade", "run_migrations_online", "run_migrations_offline"),
        description="Alembic migration functions",
    ),
    EntryPointPattern(
        "migration", CheckField.FILE_PATH, MatchOp.CONTAINS,
        ("alembic",),
        type_filter=("function",),
        description="Alembic env.py functions",
    ),
    EntryPointPattern(
        "migration", CheckField.FILE_PATH, MatchOp.SUFFIX,
        ("env.py",),
        type_filter=("function",),
        description="env.py functions (Alembic)",
    ),
    EntryPointPattern(
        "migration", CheckField.FILE_PATH, MatchOp.CONTAINS,
        ("/versions/", "/migrations/"),
        type_filter=("function",),
        description="Migration version files",
    ),

    # === Task queues ===

    EntryPointPattern(
        "task_queue", CheckField.SOURCE, MatchOp.CONTAINS,
        (
            "@celery.task", "@app.task", "@shared_task",
            "@celery_app.task", "celery.Task",
        ),
        type_filter=("function",),
        description="Celery tasks",
    ),
    EntryPointPattern(
        "task_queue", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@job",),
        type_filter=("function",),
        description="RQ tasks",
    ),

    # === Pytest ===

    EntryPointPattern(
        "pytest", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@pytest.fixture",),
        type_filter=("function",),
        description="Pytest fixtures (dynamically called by pytest)",
    ),
    EntryPointPattern(
        "pytest", CheckField.FILE_PATH, MatchOp.SUFFIX,
        ("conftest.py",),
        type_filter=("function",),
        description="conftest.py functions (usually fixtures)",
    ),

    # === Django patterns ===

    EntryPointPattern(
        "django", CheckField.FILE_PATH, MatchOp.CONTAINS,
        ("/management/commands/",),
        description="Django management commands",
    ),
    EntryPointPattern(
        "django", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@receiver", "pre_save", "post_save", "pre_delete", "post_delete"),
        type_filter=("function",),
        description="Django signals",
    ),
    EntryPointPattern(
        "django", CheckField.FILE_PATH, MatchOp.CONTAINS,
        ("admin.py",),
        type_filter=("class", "function"),
        description="Django admin classes and functions",
    ),

    # === SQLAlchemy events ===

    EntryPointPattern(
        "sqlalchemy", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@event.listens_for", "event.listen"),
        type_filter=("function",),
        description="SQLAlchemy event listeners",
    ),

    # === Methods called via protocols/conventions ===

    EntryPointPattern(
        "dunder", CheckField.NAME, MatchOp.EXACT,
        (
            "__init__", "__post_init__", "__new__", "__del__",
            "__repr__", "__str__", "__hash__", "__eq__", "__ne__",
            "__lt__", "__le__", "__gt__", "__ge__",
            "__len__", "__iter__", "__next__", "__getitem__", "__setitem__",
            "__contains__", "__call__", "__enter__", "__exit__",
            "__get__", "__set__", "__delete__",
            "from_dict", "to_dict",
        ),
        type_filter=("method",),
        description="Dataclass/class special methods and serialization",
    ),
    EntryPointPattern(
        "property", CheckField.NAME, MatchOp.PREFIX,
        ("is_", "has_", "get_", "set_"),
        type_filter=("method",),
        description="Property-style methods (is_*, has_*, get_*, set_*)",
    ),

    # === Parser/visitor patterns ===

    EntryPointPattern(
        "visitor", CheckField.NAME, MatchOp.PREFIX,
        ("visit_",),
        description="AST visitor methods (visit_*)",
    ),
    EntryPointPattern(
        "visitor", CheckField.NAME, MatchOp.EXACT,
        ("generic_visit",),
        description="AST generic_visit method",
    ),

    # === Helper functions ===

    EntryPointPattern(
        "helper", CheckField.NAME, MatchOp.PREFIX,
        ("_detect_", "_compute_"),
        description="Detection/analysis helpers (often called via dict/getattr)",
    ),

    # === Enum classes ===

    EntryPointPattern(
        "enum", CheckField.NAME, MatchOp.SUFFIX,
        ("Enum", "Role", "Type", "Confidence", "Source"),
        type_filter=("class",),
        description="Enum classes (values accessed by name)",
    ),

    # === Pydantic/dataclass validators ===

    EntryPointPattern(
        "pydantic", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@validator", "@field_validator", "@root_validator", "@model_validator"),
        type_filter=("method",),
        description="Pydantic validators",
    ),

    # === Click/Typer CLI ===

    EntryPointPattern(
        "click", CheckField.SOURCE, MatchOp.CONTAINS,
        ("@click.command", "@click.group", "@app.command", "@typer.command"),
        type_filter=("function",),
        description="Click/Typer CLI commands",
    ),
)


# ---------------------------------------------------------------------------
# Custom predicates — patterns that don't fit the declarative model
# ---------------------------------------------------------------------------

CustomPredicate = Callable[[str, dict], bool]

_CUSTOM_PREDICATES: dict[str, CustomPredicate] = {
    "init_top_level_function": lambda nid, data: (
        data.get("file_path", "").endswith("__init__.py")
        and data.get("type") == "function"
        and nid.count(".") == 1  # "package.function" format
    ),
    "private_method": lambda nid, data: (
        data.get("type") == "method"
        and data.get("name", "").startswith("_")
        and not data.get("name", "").startswith("__")
    ),
}


# ---------------------------------------------------------------------------
# Evaluation functions
# ---------------------------------------------------------------------------

def _get_field_value(field: CheckField, data: dict) -> str:
    """Extract the value of a field from node data."""
    return data.get(field.value, "")


def _check_op(op: MatchOp, value: str, patterns: tuple[str, ...]) -> bool:
    """Check if value matches any of the patterns using the given operation."""
    if op is MatchOp.EXACT:
        return value in patterns
    if op is MatchOp.PREFIX:
        return any(value.startswith(p) for p in patterns)
    if op is MatchOp.SUFFIX:
        return any(value.endswith(p) for p in patterns)
    if op is MatchOp.CONTAINS:
        return any(p in value for p in patterns)
    return False


def matches_pattern(
    pattern: EntryPointPattern,
    data: dict,
) -> bool:
    """Check if a single node matches a single entry point pattern.

    Args:
        pattern: The pattern to test.
        data: Node attribute dict from ``nx_graph.nodes[nid]``.
    """
    if pattern.type_filter and data.get("type", "") not in pattern.type_filter:
        return False

    value = _get_field_value(pattern.field, data)
    return _check_op(pattern.op, value, pattern.values)


def collect_public_api(nx_graph: nx.DiGraph) -> set[str]:
    """Collect node IDs exported via ``__all__`` in any module.

    Scans all module nodes for ``__all__`` in their source, then marks
    their direct children (one level deep) as public API entry points.
    """
    public_api: set[str] = set()
    for nid, data in nx_graph.nodes(data=True):
        if data.get("type") != "module":
            continue
        source = data.get("source_code", "")
        if "__all__" not in source:
            continue
        module_prefix = nid + "."
        for other_nid in nx_graph.nodes():
            if other_nid.startswith(module_prefix):
                remainder = other_nid[len(module_prefix):]
                if "." not in remainder:
                    public_api.add(other_nid)
    return public_api


def collect_entry_points(
    nx_graph: nx.DiGraph,
    patterns: tuple[EntryPointPattern, ...] = ENTRY_POINT_PATTERNS,
    custom_predicates: dict[str, CustomPredicate] | None = None,
) -> set[str]:
    """Collect entry point node IDs by applying all patterns in one pass.

    Iterates every node once.  For each node, tests patterns in order and
    short-circuits (``break``) as soon as the first pattern matches.

    Args:
        nx_graph: The NetworkX directed graph of code nodes.
        patterns: Tuple of declarative patterns to apply.
        custom_predicates: Extra predicate functions for patterns that
            don't fit the declarative model.  Defaults to the built-in
            ``_CUSTOM_PREDICATES``.
    """
    if custom_predicates is None:
        custom_predicates = _CUSTOM_PREDICATES

    entry_set: set[str] = set()

    for nid, data in nx_graph.nodes(data=True):
        # Declarative patterns
        for pattern in patterns:
            if matches_pattern(pattern, data):
                entry_set.add(nid)
                break
        else:
            # Custom predicates (only if no declarative pattern matched)
            for pred_fn in custom_predicates.values():
                if pred_fn(nid, data):
                    entry_set.add(nid)
                    break

    return entry_set


def expand_entry_points(nx_graph: nx.DiGraph, entry_set: set[str]) -> set[str]:
    """Graph-based post-processing: expand entry set with related nodes.

    Three expansions:
    1. **Decorated functions** — functions with incoming ``decorates`` edges
    2. **Class method expansion** — all methods of entry-point classes
    3. **Nested function expansion** — nested functions/classes of entry-point functions
    """
    expanded = set(entry_set)

    # 1. Decorated functions
    for nid, data in nx_graph.nodes(data=True):
        if data.get("type") == "function":
            for pred in nx_graph.predecessors(nid):
                edge_data = nx_graph.edges.get((pred, nid), {})
                if edge_data.get("type") == "decorates":
                    expanded.add(nid)
                    break

    # 2. Methods of entry-point classes
    class_entry_points = {
        nid for nid in expanded
        if nx_graph.nodes.get(nid, {}).get("type") == "class"
    }
    for nid, data in nx_graph.nodes(data=True):
        if data.get("type") == "method":
            class_id = nid.rsplit(".", 1)[0]
            if class_id in class_entry_points:
                expanded.add(nid)

    # 3. Nested functions of entry-point functions
    function_entry_points = {
        nid for nid in expanded
        if nx_graph.nodes.get(nid, {}).get("type") == "function"
    }
    for nid, data in nx_graph.nodes(data=True):
        if data.get("type") in ("function", "class"):
            parent_id = nid.rsplit(".", 1)[0] if "." in nid else None
            if parent_id and parent_id in function_entry_points:
                expanded.add(nid)

    return expanded
