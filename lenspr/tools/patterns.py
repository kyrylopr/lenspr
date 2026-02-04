"""Pattern-based detection for semantic roles and side effects.

This module provides deterministic, rule-based detection to avoid LLM hallucinations.
Claude only generates summaries; roles and side_effects are computed from patterns.
"""

from __future__ import annotations

# Valid roles for annotations (canonical list)
VALID_ROLES = [
    "validator",
    "transformer",
    "io",
    "orchestrator",
    "pure",
    "handler",
    "test",
    "utility",
    "factory",
    "accessor",
]

# Role detection patterns (order matters - more specific first)
ROLE_PATTERNS: dict[str, str] = {
    # Test functions
    "test_": "test",
    "_test": "test",
    # Validators
    "validate": "validator",
    "check_": "validator",
    "is_valid": "validator",
    "is_": "validator",
    "has_": "validator",
    "can_": "validator",
    "verify": "validator",
    # Factories
    "__init__": "factory",
    "create_": "factory",
    "build_": "factory",
    "make_": "factory",
    "get_or_create": "factory",
    "new_": "factory",
    # Accessors
    "get_": "accessor",
    "fetch_": "accessor",
    "load_": "accessor",
    "read_": "accessor",
    "find_": "accessor",
    "lookup_": "accessor",
    "query_": "accessor",
    # Transformers
    "transform": "transformer",
    "convert": "transformer",
    "parse": "transformer",
    "format": "transformer",
    "to_": "transformer",
    "from_": "transformer",
    "encode": "transformer",
    "decode": "transformer",
    "serialize": "transformer",
    "deserialize": "transformer",
    "map_": "transformer",
    "extract": "transformer",
    # IO operations
    "save_": "io",
    "write_": "io",
    "store_": "io",
    "upload_": "io",
    "download_": "io",
    "send_": "io",
    "post_": "io",
    "delete_": "io",
    "update_": "io",
    "crawl": "io",
    "insert_": "io",
    "remove_": "io",
    # Handlers
    "handle_": "handler",
    "on_": "handler",
    "_handler": "handler",
    "_callback": "handler",
    "dispatch": "handler",
    # Orchestrators
    "run_": "orchestrator",
    "execute_": "orchestrator",
    "process_": "orchestrator",
    "main": "orchestrator",
    "start_": "orchestrator",
    "stop_": "orchestrator",
    "orchestrate": "orchestrator",
    "coordinate": "orchestrator",
}

# Side effects detection patterns
SIDE_EFFECTS_PATTERNS: dict[str, list[str]] = {
    "write": ["writes_file"],
    "save": ["writes_database"],
    "store": ["writes_database"],
    "delete": ["deletes_data"],
    "remove": ["deletes_data"],
    "send": ["network_io"],
    "post": ["network_io"],
    "fetch": ["network_io"],
    "crawl": ["network_io"],
    "download": ["network_io"],
    "upload": ["network_io"],
    "email": ["sends_email"],
    "notify": ["sends_notification"],
    "log": ["writes_log"],
    "print": ["console_output"],
    "insert": ["writes_database"],
    "update": ["writes_database"],
}

# Class name patterns for role detection
CLASS_ROLE_PATTERNS: dict[str, str] = {
    "service": "orchestrator",
    "manager": "orchestrator",
    "controller": "orchestrator",
    "coordinator": "orchestrator",
    "model": "transformer",
    "schema": "transformer",
    "dto": "transformer",
    "entity": "transformer",
    "validator": "validator",
    "checker": "validator",
    "handler": "handler",
    "listener": "handler",
    "callback": "handler",
    "factory": "factory",
    "builder": "factory",
    "creator": "factory",
    "repository": "io",
    "store": "io",
    "dao": "io",
}


def detect_role(name: str, node_type: str, source_code: str = "") -> str:
    """Detect semantic role based on name patterns.

    Args:
        name: Function/method/class name
        node_type: One of "function", "method", "class"
        source_code: Optional source code for additional analysis

    Returns:
        Detected role (one of VALID_ROLES)
    """
    name_lower = name.lower()

    # Check function/method patterns
    for pattern, role in ROLE_PATTERNS.items():
        if pattern in name_lower:
            return role

    # Special cases for dunder methods
    if name.startswith("__") and name.endswith("__"):
        if name in ("__init__", "__new__"):
            return "factory"
        if name in ("__str__", "__repr__", "__hash__", "__eq__", "__lt__", "__gt__"):
            return "utility"
        if name in ("__enter__", "__exit__"):
            return "handler"
        if name in ("__getitem__", "__setitem__", "__getattr__", "__setattr__"):
            return "accessor"
        if name in ("__iter__", "__next__", "__len__", "__contains__"):
            return "accessor"
        if name == "__call__":
            return "handler"
        return "utility"

    # Class-specific patterns
    if node_type == "class":
        for pattern, role in CLASS_ROLE_PATTERNS.items():
            if pattern in name_lower:
                return role
        return "utility"

    # Private helpers
    if node_type == "method" and name.startswith("_") and not name.startswith("__"):
        return "utility"

    # Default
    return "utility"


def detect_side_effects(name: str, source_code: str = "") -> list[str]:
    """Detect potential side effects based on patterns.

    Args:
        name: Function/method name
        source_code: Optional source code for additional analysis

    Returns:
        List of detected side effects
    """
    effects: set[str] = set()
    name_lower = name.lower()
    source_lower = source_code.lower() if source_code else ""

    for pattern, effect_list in SIDE_EFFECTS_PATTERNS.items():
        if pattern in name_lower or pattern in source_lower:
            effects.update(effect_list)

    return sorted(effects)


def auto_annotate(
    name: str,
    node_type: str,
    source_code: str = "",
    provided_role: str | None = None,
    provided_side_effects: list[str] | None = None,
) -> dict:
    """Auto-fill annotation fields that weren't provided.

    Claude provides summary; this function fills in role and side_effects.

    Args:
        name: Node name
        node_type: One of "function", "method", "class"
        source_code: Source code for analysis
        provided_role: Role provided by caller (if any)
        provided_side_effects: Side effects provided by caller (if any)

    Returns:
        Dict with role and side_effects (auto-detected if not provided)
    """
    role = provided_role if provided_role else detect_role(name, node_type, source_code)
    effects = provided_side_effects if provided_side_effects is not None else detect_side_effects(
        name, source_code
    )
    return {"role": role, "side_effects": effects}
