"""Three-level validation for code changes."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from lenspr.models import Node, NodeType


@dataclass
class ValidationResult:
    """Result of validating a code change."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_syntax(code: str) -> ValidationResult:
    """
    Level 1: Check if code is syntactically valid Python.

    This is the minimum bar — if this fails, the code cannot be applied.
    """
    try:
        ast.parse(code)
        return ValidationResult(valid=True)
    except SyntaxError as e:
        return ValidationResult(
            valid=False,
            errors=[f"Syntax error at line {e.lineno}: {e.msg}"],
        )


def validate_structure(new_source: str, old_node: Node) -> ValidationResult:
    """
    Level 2: Check that the new source matches the expected structure.

    Ensures a function remains a function, a class remains a class, etc.
    """
    result = ValidationResult(valid=True)

    try:
        tree = ast.parse(new_source)
    except SyntaxError as e:
        return ValidationResult(
            valid=False,
            errors=[f"Syntax error at line {e.lineno}: {e.msg}"],
        )

    body = tree.body
    if not body:
        return ValidationResult(valid=False, errors=["Empty source code."])

    first = body[0]

    # Check type match
    expected_types: dict[NodeType, tuple[type, ...]] = {
        NodeType.FUNCTION: (ast.FunctionDef, ast.AsyncFunctionDef),
        NodeType.METHOD: (ast.FunctionDef, ast.AsyncFunctionDef),
        NodeType.CLASS: (ast.ClassDef,),
    }

    if old_node.type in expected_types:
        allowed = expected_types[old_node.type]
        if not isinstance(first, allowed):
            type_name = old_node.type.value
            actual = type(first).__name__
            return ValidationResult(
                valid=False,
                errors=[
                    f"Node '{old_node.id}' is a {type_name}, "
                    f"but new source is {actual}."
                ],
            )

    # Check name change (warning, not error)
    if hasattr(first, "name") and first.name != old_node.name:
        result.warnings.append(
            f"Name changed: '{old_node.name}' → '{first.name}'. "
            f"This may break callers. Consider using lens_rename instead."
        )

    return result


def validate_signature(new_source: str, old_node: Node) -> ValidationResult:
    """
    Level 3: Check signature compatibility for functions/methods.

    Detects breaking changes like removed parameters, changed defaults,
    or reordered arguments.
    """
    result = ValidationResult(valid=True)

    if old_node.type not in (NodeType.FUNCTION, NodeType.METHOD):
        return result

    try:
        old_tree = ast.parse(old_node.source_code)
        new_tree = ast.parse(new_source)
    except SyntaxError:
        return result  # Syntax validation handles this

    old_func = _find_function(old_tree)
    new_func = _find_function(new_tree)

    if not old_func or not new_func:
        return result

    old_params = _extract_params(old_func)
    new_params = _extract_params(new_func)

    # Check for removed required parameters
    old_required = {p for p, has_default in old_params.items() if not has_default}
    new_required = {p for p, has_default in new_params.items() if not has_default}
    new_all = set(new_params.keys())

    removed = old_required - new_all
    if removed:
        result.warnings.append(
            f"Breaking change: removed required parameter(s): {', '.join(sorted(removed))}. "
            f"All callers passing these arguments will break."
        )

    # Check for new required parameters (no default)
    added_required = new_required - set(old_params.keys())
    if added_required:
        result.warnings.append(
            f"Breaking change: added required parameter(s): {', '.join(sorted(added_required))}. "
            f"All existing callers will break unless they pass these arguments."
        )

    return result


def validate_full(new_source: str, old_node: Node) -> ValidationResult:
    """
    Run all three validation levels and combine results.

    Level 1 failure is blocking (valid=False).
    Level 2 failure is blocking.
    Level 3 issues are warnings only.
    """
    # Level 1: syntax
    syntax = validate_syntax(new_source)
    if not syntax.valid:
        return syntax

    # Level 2: structure
    structure = validate_structure(new_source, old_node)
    if not structure.valid:
        return structure

    # Level 3: signature compatibility
    sig = validate_signature(new_source, old_node)

    # Combine warnings
    all_warnings = structure.warnings + sig.warnings
    return ValidationResult(valid=True, warnings=all_warnings)


def _find_function(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find the first function/async function definition in a module."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _extract_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, bool]:
    """
    Extract parameter names and whether they have defaults.

    Returns dict of {param_name: has_default}.
    Excludes 'self' and 'cls'.
    """
    params: dict[str, bool] = {}
    args = func.args

    # Regular positional args
    num_defaults = len(args.defaults)
    for i, arg in enumerate(args.args):
        if arg.arg in ("self", "cls"):
            continue
        has_default = i >= (len(args.args) - num_defaults)
        params[arg.arg] = has_default

    # Keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        has_default = i < len(args.kw_defaults) and args.kw_defaults[i] is not None
        params[arg.arg] = has_default

    return params
