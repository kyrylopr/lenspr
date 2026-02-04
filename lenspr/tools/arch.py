"""Architecture analysis tool handlers for LensPR."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lenspr import database
from lenspr.architecture import (
    ArchitectureReport,
    analyze_architecture,
    detect_all_patterns,
    detect_components,
    format_architecture_report,
)
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_architecture(params: dict, ctx: LensContext) -> ToolResponse:
    """Analyze codebase architecture: patterns, components, and relationships.

    Returns detected patterns (Facade, Strategy, Factory, etc.),
    components with cohesion metrics, and architectural recommendations.
    """
    # Get all nodes and edges
    nodes, edges = database.load_graph(ctx.graph_db)
    project_root = Path(ctx.project_root)

    # Run full analysis
    report = analyze_architecture(nodes, edges, project_root)

    return ToolResponse(
        success=True,
        data={
            "patterns": [
                {
                    "pattern": p.pattern.value,
                    "node_id": p.node_id,
                    "confidence": round(p.confidence, 2),
                    "related_nodes": p.related_nodes[:10],  # Limit for readability
                    "evidence": p.evidence,
                }
                for p in report.patterns
            ],
            "components": [
                {
                    "id": c.id,
                    "name": c.name,
                    "path": c.path,
                    "pattern": c.pattern.value if c.pattern else None,
                    "cohesion": c.cohesion,
                    "classes": len(c.classes),
                    "public_api": len(c.public_api),
                    "internal": len(c.internal_nodes),
                    "delegates_to": c.delegates_to[:5] if c.delegates_to else [],
                }
                for c in report.components
            ],
            "warnings": report.warnings,
            "recommendations": report.recommendations,
            "summary": {
                "total_patterns": len(report.patterns),
                "total_components": len(report.components),
                "high_cohesion": sum(1 for c in report.components if c.cohesion >= 0.7),
                "low_cohesion": sum(1 for c in report.components if c.cohesion < 0.5),
            },
        },
    )


def handle_patterns(params: dict, ctx: LensContext) -> ToolResponse:
    """Detect architectural patterns in the codebase.

    Detects: Facade, Strategy, Factory, Singleton, Decorator, Repository, Service.
    Returns pattern type, primary node, confidence, and evidence.
    """
    pattern_filter = params.get("pattern")  # Optional filter by pattern type
    min_confidence = params.get("min_confidence", 0.5)

    nodes, edges = database.load_graph(ctx.graph_db)

    patterns = detect_all_patterns(nodes, edges)

    # Filter
    if pattern_filter:
        patterns = [p for p in patterns if p.pattern.value == pattern_filter]
    patterns = [p for p in patterns if p.confidence >= min_confidence]

    # Sort by confidence
    patterns = sorted(patterns, key=lambda p: -p.confidence)

    return ToolResponse(
        success=True,
        data={
            "patterns": [
                {
                    "pattern": p.pattern.value,
                    "node_id": p.node_id,
                    "confidence": round(p.confidence, 2),
                    "related_nodes": p.related_nodes,
                    "evidence": p.evidence,
                }
                for p in patterns
            ],
            "count": len(patterns),
            "by_type": {
                pattern_type: sum(1 for p in patterns if p.pattern.value == pattern_type)
                for pattern_type in {p.pattern.value for p in patterns}
            },
        },
    )


def handle_components(params: dict, ctx: LensContext) -> ToolResponse:
    """Analyze components (directory-based modules) with cohesion metrics.

    Components are directories containing related code. Returns:
    - Cohesion score (internal edges / total edges)
    - Public API nodes (called from outside)
    - Internal nodes (only used within component)
    """
    min_cohesion = params.get("min_cohesion", 0.0)
    path_prefix = params.get("path")

    nodes, edges = database.load_graph(ctx.graph_db)
    project_root = Path(ctx.project_root)

    components = detect_components(nodes, edges, project_root)

    # Filter
    if path_prefix:
        components = [c for c in components if c.path.startswith(path_prefix)]
    components = [c for c in components if c.cohesion >= min_cohesion]

    # Sort by cohesion descending
    components = sorted(components, key=lambda c: -c.cohesion)

    return ToolResponse(
        success=True,
        data={
            "components": [
                {
                    "id": c.id,
                    "name": c.name,
                    "path": c.path,
                    "pattern": c.pattern.value if c.pattern else None,
                    "cohesion": c.cohesion,
                    "modules": c.modules,
                    "classes": c.classes,
                    "public_api": c.public_api,
                    "internal_nodes": c.internal_nodes,
                    "internal_edges": c.internal_edges,
                    "external_edges": c.external_edges,
                }
                for c in components
            ],
            "count": len(components),
            "avg_cohesion": (
                round(sum(c.cohesion for c in components) / len(components), 2)
                if components
                else 0
            ),
        },
    )


def handle_explain_architecture(params: dict, ctx: LensContext) -> ToolResponse:
    """Explain why a class/function has its current architecture.

    For classes flagged as "God Objects" or similar, explains whether
    the pattern is intentional (Facade, Service) or needs refactoring.
    """
    node_id = params.get("node_id")
    if not node_id:
        return ToolResponse(success=False, error="node_id is required")

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    nodes, edges = database.load_graph(ctx.graph_db)
    project_root = Path(ctx.project_root)

    # Run pattern detection
    patterns = detect_all_patterns(nodes, edges)
    node_patterns = [p for p in patterns if p.node_id == node_id]

    # Get component info
    components = detect_components(nodes, edges, project_root)
    node_component = None
    for comp in components:
        if node_id in comp.classes or node_id in comp.modules:
            node_component = comp
            break

    # Count methods/functions
    method_count = sum(
        1
        for n in nodes
        if n.type.value == "method" and n.id.startswith(node_id + ".")
    )

    # Analyze
    explanation: dict = {
        "node_id": node_id,
        "type": node.type.value,
        "methods": method_count,
    }

    if node_patterns:
        pattern = node_patterns[0]
        explanation["pattern"] = {
            "type": pattern.pattern.value,
            "confidence": round(pattern.confidence, 2),
            "evidence": pattern.evidence,
            "related_nodes": pattern.related_nodes[:10],
        }

        # Pattern-specific explanation
        if pattern.pattern.value == "facade":
            explanation["analysis"] = (
                f"This is a FACADE pattern. It delegates to {len(pattern.related_nodes)} "
                f"other classes. The high method count ({method_count}) is justified "
                f"because each method is a thin wrapper providing a unified API."
            )
            explanation["recommendation"] = "KEEP - architecture is intentional"
        elif pattern.pattern.value == "strategy":
            explanation["analysis"] = (
                f"This is a STRATEGY interface with {len(pattern.related_nodes)} "
                f"implementations. The base class defines the contract."
            )
            explanation["recommendation"] = "KEEP - implements Strategy pattern"
        elif pattern.pattern.value == "service":
            explanation["analysis"] = (
                f"This is a SERVICE class that orchestrates business logic. "
                f"It coordinates multiple dependencies."
            )
            explanation["recommendation"] = "KEEP - standard service pattern"
        else:
            explanation["recommendation"] = f"Uses {pattern.pattern.value} pattern"
    else:
        # No pattern detected
        if method_count > 20:
            explanation["analysis"] = (
                f"This class has {method_count} methods but no recognized pattern. "
                f"Consider whether it has too many responsibilities."
            )
            explanation["recommendation"] = (
                "REVIEW - possible God Object. Consider splitting into smaller classes."
            )
        else:
            explanation["analysis"] = "Standard class with no special architectural pattern."
            explanation["recommendation"] = "OK - no issues detected"

    if node_component:
        explanation["component"] = {
            "name": node_component.name,
            "path": node_component.path,
            "cohesion": node_component.cohesion,
        }

    return ToolResponse(success=True, data=explanation)
