"""LLM-powered function explanation tool handler."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from lenspr import database
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_explain(params: dict, ctx: LensContext) -> ToolResponse:
    """Generate a human-readable explanation of what a node does.

    Provides rich context for Claude (or other LLM) to generate explanations,
    plus rule-based analysis as a starting point.
    """
    node_id = params["node_id"]
    include_examples = params.get("include_examples", True)

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    nx_graph = ctx.get_graph()

    # Gather rich context for explanation
    callers = _get_callers_context(node_id, nx_graph, ctx, limit=5)
    callees = _get_callees_context(node_id, nx_graph, ctx, limit=5)

    # Analyze the code structure
    analysis = _analyze_code_structure(node.source_code, node.type.value)

    # Generate rule-based explanation as starting point
    explanation = _generate_explanation(
        node=node,
        analysis=analysis,
        callers=callers,
        callees=callees,
    )

    # Get usage examples from callers if requested
    usage_examples = []
    if include_examples and callers:
        usage_examples = _extract_usage_examples(node.name, callers)

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "name": node.name,
            "type": node.type.value,
            "file_path": node.file_path,
            "source_code": node.source_code,
            "signature": node.signature,
            "docstring": node.docstring,
            # Rule-based explanation (starting point)
            "explanation": explanation,
            # Structured analysis
            "analysis": {
                "purpose": analysis.get("purpose"),
                "inputs": analysis.get("inputs", []),
                "outputs": analysis.get("outputs", []),
                "side_effects": analysis.get("side_effects", []),
                "error_handling": analysis.get("error_handling", []),
                "complexity": analysis.get("complexity"),
            },
            # Context for richer LLM explanation
            "context": {
                "callers": callers,
                "callees": callees,
                "caller_count": len(callers),
                "callee_count": len(callees),
            },
            "usage_examples": usage_examples,
            # Hint for Claude
            "llm_hint": (
                "Use the source code, analysis, and context to provide "
                "a clear, concise explanation of what this function does. "
                "Focus on the 'why' not just the 'what'."
            ),
        },
    )


def _get_callers_context(
    node_id: str, graph: Any, ctx: LensContext, limit: int = 5
) -> list[dict]:
    """Get caller context with relevant source snippets."""
    callers = []
    if node_id not in graph:
        return callers

    for pred_id in list(graph.predecessors(node_id))[:limit]:
        pred_node = database.get_node(pred_id, ctx.graph_db)
        if not pred_node:
            continue

        callers.append({
            "id": pred_id,
            "name": pred_node.name,
            "type": pred_node.type.value,
            "file_path": pred_node.file_path,
            "signature": pred_node.signature,
            # Include source for context
            "source_code": pred_node.source_code,
        })

    return callers


def _get_callees_context(
    node_id: str, graph: Any, ctx: LensContext, limit: int = 5
) -> list[dict]:
    """Get callee context to understand dependencies."""
    callees = []
    if node_id not in graph:
        return callees

    for succ_id in list(graph.successors(node_id))[:limit]:
        succ_node = database.get_node(succ_id, ctx.graph_db)
        if not succ_node:
            continue

        callees.append({
            "id": succ_id,
            "name": succ_node.name,
            "type": succ_node.type.value,
            "file_path": succ_node.file_path,
            "signature": succ_node.signature,
            # Brief snippet only for callees
            "docstring": succ_node.docstring,
        })

    return callees


def _analyze_code_structure(source_code: str, node_type: str) -> dict:
    """Analyze code structure to extract semantic information."""
    analysis: dict[str, Any] = {
        "purpose": None,
        "inputs": [],
        "outputs": [],
        "side_effects": [],
        "error_handling": [],
        "complexity": "simple",
    }

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return analysis

    # Find the main node (function/class)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _analyze_function(node, analysis)
            break
        elif isinstance(node, ast.ClassDef):
            _analyze_class(node, analysis)
            break

    return analysis


def _analyze_function(node: ast.FunctionDef | ast.AsyncFunctionDef, analysis: dict) -> None:
    """Analyze function structure."""
    # Extract parameters
    for arg in node.args.args:
        if arg.arg != "self":
            annotation = ""
            if arg.annotation:
                annotation = ast.unparse(arg.annotation)
            analysis["inputs"].append({
                "name": arg.arg,
                "type": annotation,
            })

    # Extract return type from annotation
    if node.returns:
        analysis["outputs"].append({
            "type": ast.unparse(node.returns),
        })

    # Analyze body for patterns
    for stmt in ast.walk(node):
        # Return statements
        if isinstance(stmt, ast.Return) and stmt.value:
            if not analysis["outputs"]:
                analysis["outputs"].append({"inferred": True})

        # Raise statements (error handling)
        if isinstance(stmt, ast.Raise):
            if stmt.exc and isinstance(stmt.exc, ast.Call):
                if hasattr(stmt.exc.func, "id"):
                    analysis["error_handling"].append(stmt.exc.func.id)

        # Try/except blocks
        if isinstance(stmt, ast.Try):
            for handler in stmt.handlers:
                if handler.type:
                    exc_name = ast.unparse(handler.type) if handler.type else "Exception"
                    analysis["error_handling"].append(f"catches {exc_name}")

        # Side effects detection
        if isinstance(stmt, ast.Call):
            _detect_side_effects_from_call(stmt, analysis)

        # Attribute assignments (state modification)
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Attribute):
                    if isinstance(target.value, ast.Name) and target.value.id == "self":
                        if "modifies_state" not in analysis["side_effects"]:
                            analysis["side_effects"].append("modifies_state")

    # Determine complexity
    analysis["complexity"] = _assess_complexity(node)

    # Infer purpose from name and structure
    analysis["purpose"] = _infer_purpose(node.name, analysis)


def _analyze_class(node: ast.ClassDef, analysis: dict) -> None:
    """Analyze class structure."""
    methods = []
    attributes = []

    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(item.name)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    attributes.append(target.id)

    analysis["inputs"] = [{"name": "class_attributes", "items": attributes}]
    analysis["outputs"] = [{"name": "methods", "items": methods}]
    analysis["purpose"] = _infer_class_purpose(node.name, methods, node.bases)


def _detect_side_effects_from_call(call: ast.Call, analysis: dict) -> None:
    """Detect side effects from function calls."""
    call_name = ""
    if isinstance(call.func, ast.Name):
        call_name = call.func.id
    elif isinstance(call.func, ast.Attribute):
        call_name = call.func.attr

    side_effect_patterns = {
        "open": "file_io",
        "write": "writes_file",
        "read": "reads_file",
        "print": "console_output",
        "execute": "database_io",
        "commit": "database_io",
        "request": "network_io",
        "get": "network_io",
        "post": "network_io",
    }

    for pattern, effect in side_effect_patterns.items():
        if pattern in call_name.lower():
            if effect not in analysis["side_effects"]:
                analysis["side_effects"].append(effect)


def _assess_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Assess function complexity."""
    branches = 0
    loops = 0
    calls = 0

    for stmt in ast.walk(node):
        if isinstance(stmt, (ast.If, ast.IfExp)):
            branches += 1
        elif isinstance(stmt, (ast.For, ast.While, ast.comprehension)):
            loops += 1
        elif isinstance(stmt, ast.Call):
            calls += 1

    if branches > 5 or loops > 3:
        return "complex"
    elif branches > 2 or loops > 1:
        return "moderate"
    else:
        return "simple"


def _infer_purpose(name: str, analysis: dict) -> str:
    """Infer function purpose from name and analysis."""
    name_lower = name.lower()

    # Common patterns
    if name_lower.startswith("test_"):
        return "Tests functionality"
    if name_lower.startswith("validate") or name_lower.startswith("is_"):
        return "Validates input and returns boolean"
    if name_lower.startswith("get_"):
        return "Retrieves and returns data"
    if name_lower.startswith("set_"):
        return "Sets/updates state"
    if name_lower.startswith("create_") or name_lower.startswith("make_"):
        return "Creates and returns new object"
    if name_lower.startswith("parse"):
        return "Parses input into structured format"
    if name_lower.startswith("handle_"):
        return "Handles event or request"
    if name_lower.startswith("_"):
        return "Internal helper function"

    # Infer from side effects
    if "network_io" in analysis.get("side_effects", []):
        return "Performs network operation"
    if "database_io" in analysis.get("side_effects", []):
        return "Performs database operation"
    if "file_io" in analysis.get("side_effects", []) or "writes_file" in analysis.get("side_effects", []):
        return "Performs file operation"

    return "General purpose function"


def _infer_class_purpose(name: str, methods: list[str], bases: list) -> str:
    """Infer class purpose from name and structure."""
    name_lower = name.lower()

    if "test" in name_lower:
        return "Test class containing test methods"
    if "error" in name_lower or "exception" in name_lower:
        return "Custom exception class"
    if "handler" in name_lower:
        return "Event/request handler class"
    if "factory" in name_lower:
        return "Factory class for creating objects"
    if "response" in name_lower:
        return "Data container for response"
    if "request" in name_lower:
        return "Data container for request"

    # Check for common patterns
    has_init = "__init__" in methods
    has_call = "__call__" in methods
    has_iter = "__iter__" in methods

    if has_call:
        return "Callable class (can be used as function)"
    if has_iter:
        return "Iterable class"
    if has_init and len(methods) <= 3:
        return "Data container class"

    return "General purpose class"


def _generate_explanation(
    node: Any,
    analysis: dict,
    callers: list[dict],
    callees: list[dict],
) -> str:
    """Generate a human-readable explanation of what the code does."""
    parts = []

    # Start with the purpose
    purpose = analysis.get("purpose", "")
    if purpose:
        parts.append(purpose + ".")

    # Add information about inputs
    inputs = analysis.get("inputs", [])
    if inputs:
        input_names = [i.get("name", "unknown") for i in inputs if isinstance(i, dict)]
        if input_names:
            parts.append(f"Takes {', '.join(input_names)} as input.")

    # Add information about outputs
    outputs = analysis.get("outputs", [])
    if outputs:
        output_types = []
        for o in outputs:
            if isinstance(o, dict):
                if "type" in o:
                    output_types.append(o["type"])
                elif "items" in o:
                    output_types.append(f"{len(o['items'])} items")
        if output_types:
            parts.append(f"Returns {', '.join(output_types)}.")

    # Add side effects if any
    side_effects = analysis.get("side_effects", [])
    if side_effects:
        effect_map = {
            "writes_file": "writes to files",
            "reads_file": "reads from files",
            "file_io": "performs file I/O",
            "network_io": "makes network requests",
            "database_io": "accesses database",
            "console_output": "outputs to console",
            "modifies_state": "modifies internal state",
            "logging": "produces log output",
        }
        effect_descriptions = [effect_map.get(e, e) for e in side_effects]
        parts.append(f"Side effects: {', '.join(effect_descriptions)}.")

    # Add context from callers
    if callers:
        caller_names = [c["name"] for c in callers[:3]]
        parts.append(f"Called by: {', '.join(caller_names)}.")

    # Add context from callees
    if callees:
        callee_names = [c["name"] for c in callees[:3]]
        parts.append(f"Uses: {', '.join(callee_names)}.")

    # Add complexity note
    complexity = analysis.get("complexity", "simple")
    if complexity == "complex":
        parts.append("This is a complex function with multiple branches or loops.")
    elif complexity == "moderate":
        parts.append("This function has moderate complexity.")

    return " ".join(parts) if parts else "No detailed analysis available."


def _extract_usage_examples(func_name: str, callers: list[dict]) -> list[dict]:
    """Extract usage examples from caller source code."""
    examples = []

    for caller in callers[:3]:  # Limit to 3 examples
        source = caller.get("source_code", "")
        if not source:
            continue

        # Find lines that call the function
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if func_name in line and "(" in line:
                # Get context (line before and after if available)
                start = max(0, i - 1)
                end = min(len(lines), i + 2)
                snippet = "\n".join(lines[start:end]).strip()

                examples.append({
                    "from": caller["name"],
                    "file": caller["file_path"],
                    "snippet": snippet,
                })
                break  # One example per caller

    return examples
