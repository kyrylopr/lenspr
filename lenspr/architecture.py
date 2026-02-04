"""Architectural pattern detection and component analysis for LensPR.

This module provides:
- Pattern detection (Facade, Strategy, Factory, etc.)
- Component grouping (directories with high cohesion)
- Architectural edge creation (DELEGATES_TO, WRAPS, etc.)

All detection is deterministic and rule-based (no LLM hallucinations).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lenspr.models import (
    ArchPattern,
    Component,
    Edge,
    EdgeConfidence,
    EdgeSource,
    EdgeType,
    Node,
    NodeType,
    PatternMatch,
)


# =============================================================================
# Pattern Detection
# =============================================================================


def detect_facade(
    node: Node,
    outgoing_edges: list[Edge],
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Facade pattern: class that delegates to multiple other classes.

    Criteria:
    - Class with 3+ methods
    - Delegates to 3+ different classes
    - Most methods are thin wrappers (call 1-2 other methods)
    """
    if node.type != NodeType.CLASS:
        return None

    # Count unique classes this node delegates to
    delegated_classes: set[str] = set()
    call_edges = [e for e in outgoing_edges if e.type == EdgeType.CALLS]

    for edge in call_edges:
        target = all_nodes.get(edge.to_node)
        if target and target.type in (NodeType.METHOD, NodeType.FUNCTION):
            # Extract class from method id: "module.Class.method" -> "module.Class"
            parts = edge.to_node.rsplit(".", 1)
            if len(parts) == 2:
                class_id = parts[0]
                if class_id != node.id:  # Don't count self-calls
                    delegated_classes.add(class_id)

    if len(delegated_classes) >= 3:
        return PatternMatch(
            pattern=ArchPattern.FACADE,
            node_id=node.id,
            confidence=min(1.0, len(delegated_classes) / 5),  # 5+ classes = 100%
            related_nodes=list(delegated_classes),
            evidence=[
                f"Delegates to {len(delegated_classes)} classes",
                f"Classes: {', '.join(sorted(delegated_classes)[:5])}",
            ],
        )
    return None


def detect_strategy(
    node: Node,
    inheritors: list[str],
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Strategy pattern: interface/base class with multiple implementations.

    Criteria:
    - Abstract class or class with abstract methods
    - 2+ concrete implementations
    - Implementations override same methods
    """
    if node.type != NodeType.CLASS:
        return None

    # Check if it's a base class with inheritors
    if len(inheritors) < 2:
        return None

    # Check for abstract patterns in source
    source = node.source_code.lower()
    is_abstract = (
        "abc" in source
        or "abstract" in source
        or "@abstractmethod" in source
        or "raise notimplementederror" in source
        or "pass" in source  # Empty methods often indicate interface
    )

    # Also check naming patterns
    name_lower = node.name.lower()
    is_interface_name = (
        name_lower.startswith("base")
        or name_lower.startswith("abstract")
        or name_lower.endswith("interface")
        or name_lower.endswith("protocol")
        or name_lower.endswith("base")
        or name_lower.endswith("mixin")
    )

    if is_abstract or is_interface_name:
        return PatternMatch(
            pattern=ArchPattern.STRATEGY,
            node_id=node.id,
            confidence=min(1.0, len(inheritors) / 4),  # 4+ implementations = 100%
            related_nodes=inheritors,
            evidence=[
                f"Base class with {len(inheritors)} implementations",
                f"Implementations: {', '.join(inheritors[:5])}",
            ],
        )
    return None


def detect_factory(
    node: Node,
    outgoing_edges: list[Edge],
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Factory pattern: function/method that creates multiple object types.

    Criteria:
    - Function/method that returns different class instances
    - Contains conditional logic (if/match/dict lookup)
    - Name suggests creation (create_, build_, make_, get_*_instance)
    """
    if node.type not in (NodeType.FUNCTION, NodeType.METHOD):
        return None

    name_lower = node.name.lower()
    source_lower = node.source_code.lower()

    # Check naming patterns
    factory_names = (
        "create_",
        "build_",
        "make_",
        "get_instance",
        "new_",
        "factory",
        "from_",
    )
    has_factory_name = any(p in name_lower for p in factory_names)

    # Check for conditional creation patterns
    has_conditional = (
        "if " in source_lower
        or "match " in source_lower
        or "elif " in source_lower
        or ".get(" in source_lower
    )

    # Count different classes instantiated
    instantiated_classes: set[str] = set()
    # Simple heuristic: look for ClassName( patterns
    class_pattern = re.compile(r"([A-Z][a-zA-Z0-9_]*)\s*\(")
    matches = class_pattern.findall(node.source_code)
    instantiated_classes = set(matches) - {"True", "False", "None", "Exception"}

    if has_factory_name and has_conditional and len(instantiated_classes) >= 2:
        return PatternMatch(
            pattern=ArchPattern.FACTORY,
            node_id=node.id,
            confidence=min(1.0, len(instantiated_classes) / 3),
            related_nodes=[],  # Would need to resolve class IDs
            evidence=[
                f"Creates {len(instantiated_classes)} different types",
                f"Types: {', '.join(sorted(instantiated_classes)[:5])}",
            ],
        )
    return None


def detect_singleton(
    node: Node,
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Singleton pattern: class with single instance management.

    Criteria:
    - Class with _instance or __instance attribute
    - get_instance() or instance() method
    - Private __init__ or __new__ override
    """
    if node.type != NodeType.CLASS:
        return None

    source = node.source_code.lower()

    singleton_indicators = [
        "_instance" in source,
        "__instance" in source,
        "get_instance" in source,
        "cls._instance" in source,
        "cls.__instance" in source,
    ]

    if sum(singleton_indicators) >= 2:
        return PatternMatch(
            pattern=ArchPattern.SINGLETON,
            node_id=node.id,
            confidence=0.9,
            related_nodes=[],
            evidence=["Class manages single instance"],
        )
    return None


def detect_decorator_pattern(
    node: Node,
    outgoing_edges: list[Edge],
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Decorator pattern: class/function that wraps another.

    Criteria:
    - Takes wrapped object in __init__
    - Delegates most calls to wrapped object
    - Adds behavior before/after delegation
    """
    if node.type != NodeType.CLASS:
        return None

    source = node.source_code.lower()

    # Check for wrapper patterns
    wrapper_indicators = [
        "self._wrapped" in source or "self.wrapped" in source,
        "self._inner" in source or "self.inner" in source,
        "self._delegate" in source or "self.delegate" in source,
        "self._component" in source or "self.component" in source,
    ]

    name_lower = node.name.lower()
    wrapper_names = ("wrapper", "decorator", "proxy", "adapter")
    has_wrapper_name = any(n in name_lower for n in wrapper_names)

    if any(wrapper_indicators) or has_wrapper_name:
        return PatternMatch(
            pattern=ArchPattern.DECORATOR,
            node_id=node.id,
            confidence=0.8 if any(wrapper_indicators) else 0.6,
            related_nodes=[],
            evidence=["Wraps another object and delegates calls"],
        )
    return None


def detect_repository(
    node: Node,
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Repository pattern: data access abstraction.

    Criteria:
    - Class with CRUD-like methods (get, save, delete, find, list)
    - Name contains Repository, Store, DAO, Repo
    """
    if node.type != NodeType.CLASS:
        return None

    name_lower = node.name.lower()
    source_lower = node.source_code.lower()

    # Check naming
    repo_names = ("repository", "store", "dao", "repo", "storage")
    has_repo_name = any(n in name_lower for n in repo_names)

    # Check for CRUD methods
    crud_methods = ["get", "save", "delete", "find", "list", "create", "update", "remove"]
    crud_count = sum(1 for m in crud_methods if f"def {m}" in source_lower or f"def {m}_" in source_lower)

    if has_repo_name or crud_count >= 3:
        return PatternMatch(
            pattern=ArchPattern.REPOSITORY,
            node_id=node.id,
            confidence=0.9 if has_repo_name else 0.7,
            related_nodes=[],
            evidence=[f"Data access pattern with {crud_count} CRUD methods"],
        )
    return None


def detect_service(
    node: Node,
    outgoing_edges: list[Edge],
    all_nodes: dict[str, Node],
) -> PatternMatch | None:
    """Detect Service pattern: business logic encapsulation.

    Criteria:
    - Class with Service/Manager/Handler in name
    - Orchestrates multiple other classes
    - Contains business logic methods
    """
    if node.type != NodeType.CLASS:
        return None

    name_lower = node.name.lower()

    service_names = ("service", "manager", "handler", "controller", "processor")
    has_service_name = any(n in name_lower for n in service_names)

    if has_service_name:
        # Count dependencies
        call_edges = [e for e in outgoing_edges if e.type == EdgeType.CALLS]
        unique_targets = len({e.to_node.rsplit(".", 1)[0] for e in call_edges if "." in e.to_node})

        return PatternMatch(
            pattern=ArchPattern.SERVICE,
            node_id=node.id,
            confidence=0.8,
            related_nodes=[],
            evidence=[
                f"Business logic service",
                f"Uses {unique_targets} other classes",
            ],
        )
    return None


def detect_all_patterns(
    nodes: list[Node],
    edges: list[Edge],
) -> list[PatternMatch]:
    """Run all pattern detectors on the codebase.

    Args:
        nodes: All nodes in the graph
        edges: All edges in the graph

    Returns:
        List of detected patterns
    """
    all_nodes = {n.id: n for n in nodes}

    # Build edge indexes
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    incoming: dict[str, list[Edge]] = defaultdict(list)
    inheritors: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        outgoing[edge.from_node].append(edge)
        incoming[edge.to_node].append(edge)
        if edge.type == EdgeType.INHERITS:
            inheritors[edge.to_node].append(edge.from_node)

    patterns: list[PatternMatch] = []

    for node in nodes:
        node_outgoing = outgoing.get(node.id, [])
        node_inheritors = inheritors.get(node.id, [])

        # Run all detectors
        detectors = [
            lambda: detect_facade(node, node_outgoing, all_nodes),
            lambda: detect_strategy(node, node_inheritors, all_nodes),
            lambda: detect_factory(node, node_outgoing, all_nodes),
            lambda: detect_singleton(node, all_nodes),
            lambda: detect_decorator_pattern(node, node_outgoing, all_nodes),
            lambda: detect_repository(node, all_nodes),
            lambda: detect_service(node, node_outgoing, all_nodes),
        ]

        for detector in detectors:
            result = detector()
            if result:
                patterns.append(result)

    return patterns


# =============================================================================
# Component Detection
# =============================================================================


def detect_components(
    nodes: list[Node],
    edges: list[Edge],
    project_root: Path,
) -> list[Component]:
    """Detect architectural components from directory structure and cohesion.

    Components are directories with:
    - Multiple related modules/classes
    - High internal cohesion (many internal edges)
    - Clear external interface

    Args:
        nodes: All nodes in the graph
        edges: All edges in the graph
        project_root: Root path for relative calculations

    Returns:
        List of detected components
    """
    # Group nodes by directory
    dir_nodes: dict[str, list[Node]] = defaultdict(list)
    for node in nodes:
        if node.type in (NodeType.MODULE, NodeType.CLASS, NodeType.FUNCTION):
            # Get directory from file path
            dir_path = str(Path(node.file_path).parent)
            if dir_path == ".":
                dir_path = "root"
            dir_nodes[dir_path].append(node)

    # Build node -> directory mapping
    node_to_dir: dict[str, str] = {}
    for dir_path, dir_node_list in dir_nodes.items():
        for node in dir_node_list:
            node_to_dir[node.id] = dir_path

    components: list[Component] = []

    for dir_path, dir_node_list in dir_nodes.items():
        if len(dir_node_list) < 2:  # Skip single-file directories
            continue

        # Count internal vs external edges
        internal_edges = 0
        external_edges = 0
        node_ids = {n.id for n in dir_node_list}

        for edge in edges:
            if edge.from_node in node_ids:
                if edge.to_node in node_ids:
                    internal_edges += 1
                else:
                    external_edges += 1

        # Calculate cohesion
        total_edges = internal_edges + external_edges
        cohesion = internal_edges / total_edges if total_edges > 0 else 0.0

        # Identify public API (nodes called from outside)
        public_api: list[str] = []
        internal_nodes: list[str] = []

        for node in dir_node_list:
            is_public = False
            for edge in edges:
                if edge.to_node == node.id and edge.from_node not in node_ids:
                    is_public = True
                    break
            if is_public:
                public_api.append(node.id)
            else:
                internal_nodes.append(node.id)

        # Create component
        component = Component(
            id=dir_path.replace("/", ".").replace("\\", "."),
            name=Path(dir_path).name,
            path=dir_path,
            modules=[n.id for n in dir_node_list if n.type == NodeType.MODULE],
            classes=[n.id for n in dir_node_list if n.type == NodeType.CLASS],
            public_api=public_api,
            internal_nodes=internal_nodes,
            internal_edges=internal_edges,
            external_edges=external_edges,
            cohesion=round(cohesion, 2),
        )

        components.append(component)

    return components


def assign_patterns_to_components(
    components: list[Component],
    patterns: list[PatternMatch],
) -> list[Component]:
    """Assign detected patterns to their containing components.

    If a component's main class has a Facade pattern, the component
    is marked as a Facade component, etc.
    """
    pattern_by_node = {p.node_id: p for p in patterns}

    for component in components:
        # Check if any class in component has a pattern
        for class_id in component.classes:
            if class_id in pattern_by_node:
                match = pattern_by_node[class_id]
                component.pattern = match.pattern
                component.delegates_to = [
                    n for n in match.related_nodes if n not in component.classes
                ]
                break

    return components


# =============================================================================
# Architectural Edges
# =============================================================================


def create_architectural_edges(
    patterns: list[PatternMatch],
    components: list[Component],
    existing_edges: list[Edge],
) -> list[Edge]:
    """Create architectural edges based on detected patterns.

    These edges represent high-level relationships:
    - DELEGATES_TO: Facade -> implementation classes
    - IMPLEMENTS: Class -> interface/protocol
    - WRAPS: Decorator -> wrapped class
    """
    new_edges: list[Edge] = []
    edge_counter = 0

    for pattern in patterns:
        if pattern.pattern == ArchPattern.FACADE:
            # Create DELEGATES_TO edges
            for related in pattern.related_nodes:
                edge_counter += 1
                new_edges.append(
                    Edge(
                        id=f"arch_{edge_counter}",
                        from_node=pattern.node_id,
                        to_node=related,
                        type=EdgeType.DELEGATES_TO,
                        confidence=EdgeConfidence.INFERRED,
                        source=EdgeSource.STATIC,
                        metadata={"pattern": "facade"},
                    )
                )

        elif pattern.pattern == ArchPattern.STRATEGY:
            # Create IMPLEMENTS edges from implementations to interface
            for impl in pattern.related_nodes:
                edge_counter += 1
                new_edges.append(
                    Edge(
                        id=f"arch_{edge_counter}",
                        from_node=impl,
                        to_node=pattern.node_id,
                        type=EdgeType.IMPLEMENTS,
                        confidence=EdgeConfidence.INFERRED,
                        source=EdgeSource.STATIC,
                        metadata={"pattern": "strategy"},
                    )
                )

        elif pattern.pattern == ArchPattern.DECORATOR:
            # Would need to detect wrapped class to create WRAPS edge
            pass

    return new_edges


# =============================================================================
# Analysis & Reporting
# =============================================================================


@dataclass
class ArchitectureReport:
    """Full architectural analysis report."""

    components: list[Component]
    patterns: list[PatternMatch]
    architectural_edges: list[Edge]
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "components": [c.to_dict() for c in self.components],
            "patterns": [
                {
                    "pattern": p.pattern.value,
                    "node_id": p.node_id,
                    "confidence": p.confidence,
                    "related_nodes": p.related_nodes,
                    "evidence": p.evidence,
                }
                for p in self.patterns
            ],
            "architectural_edges": [e.to_dict() for e in self.architectural_edges],
            "warnings": self.warnings,
            "recommendations": self.recommendations,
        }


def analyze_architecture(
    nodes: list[Node],
    edges: list[Edge],
    project_root: Path,
) -> ArchitectureReport:
    """Perform full architectural analysis of the codebase.

    Args:
        nodes: All nodes in the graph
        edges: All edges in the graph
        project_root: Root path for the project

    Returns:
        Complete architecture report
    """
    # Detect patterns
    patterns = detect_all_patterns(nodes, edges)

    # Detect components
    components = detect_components(nodes, edges, project_root)

    # Assign patterns to components
    components = assign_patterns_to_components(components, patterns)

    # Create architectural edges
    arch_edges = create_architectural_edges(patterns, components, edges)

    # Generate warnings and recommendations
    warnings: list[str] = []
    recommendations: list[str] = []

    # Check for God Objects (classes with too many methods)
    for node in nodes:
        if node.type == NodeType.CLASS:
            method_count = sum(
                1
                for n in nodes
                if n.type == NodeType.METHOD and n.id.startswith(node.id + ".")
            )
            if method_count > 20:
                # Check if it's a Facade (which justifies many methods)
                is_facade = any(
                    p.pattern == ArchPattern.FACADE and p.node_id == node.id
                    for p in patterns
                )
                if is_facade:
                    recommendations.append(
                        f"✓ {node.name}: {method_count} methods (Facade pattern - justified)"
                    )
                else:
                    warnings.append(
                        f"⚠ {node.name}: {method_count} methods - consider splitting"
                    )

    # Check component cohesion
    for comp in components:
        if comp.cohesion < 0.5 and len(comp.classes) > 3:
            warnings.append(
                f"⚠ Component '{comp.name}': low cohesion ({comp.cohesion:.0%}) - "
                f"consider reorganizing"
            )

    return ArchitectureReport(
        components=components,
        patterns=patterns,
        architectural_edges=arch_edges,
        warnings=warnings,
        recommendations=recommendations,
    )


# =============================================================================
# Metrics Computation (stored during sync, read by tools)
# =============================================================================


def compute_all_metrics(
    nodes: list[Node],
    edges: list[Edge],
) -> tuple[dict[str, dict], dict[str, float]]:
    """Compute metrics for all nodes and project-wide aggregates.

    Called during init/sync. Results are saved to database.

    Returns:
        Tuple of (node_metrics, project_metrics):
        - node_metrics: {node_id: {metric_name: value}}
        - project_metrics: {metric_name: value}
    """
    from statistics import mean, median

    all_nodes = {n.id: n for n in nodes}
    node_metrics: dict[str, dict] = {}

    # Build edge indexes
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.from_node].append(edge)

    # Compute metrics for each class
    class_method_counts: list[int] = []

    for node in nodes:
        if node.type == NodeType.CLASS:
            # Count methods
            methods = [
                n for n in nodes
                if n.type == NodeType.METHOD and n.id.startswith(node.id + ".")
            ]
            method_count = len(methods)
            class_method_counts.append(method_count)

            # Count public vs private methods
            public_methods = sum(1 for m in methods if not m.name.startswith("_"))
            private_methods = method_count - public_methods

            # Lines of code
            lines = node.end_line - node.start_line + 1

            # Dependencies (unique classes called)
            node_outgoing = outgoing.get(node.id, [])
            dependencies: set[str] = set()
            for edge in node_outgoing:
                if edge.type == EdgeType.CALLS:
                    target = all_nodes.get(edge.to_node)
                    if target and target.type in (NodeType.METHOD, NodeType.FUNCTION):
                        parts = edge.to_node.rsplit(".", 1)
                        if len(parts) == 2 and parts[0] != node.id:
                            dependencies.add(parts[0])

            # Internal calls (methods calling each other within class)
            internal_calls = 0
            method_ids = {m.id for m in methods}
            for method in methods:
                method_outgoing = outgoing.get(method.id, [])
                for edge in method_outgoing:
                    if edge.type == EdgeType.CALLS and edge.to_node in method_ids:
                        internal_calls += 1

            # Method name prefixes (for pattern detection)
            prefixes: dict[str, int] = defaultdict(int)
            for method in methods:
                if "_" in method.name:
                    prefix = method.name.split("_")[0]
                    if prefix and not prefix.startswith("_"):
                        prefixes[prefix] += 1

            node_metrics[node.id] = {
                "method_count": method_count,
                "lines": lines,
                "public_methods": public_methods,
                "private_methods": private_methods,
                "dependency_count": len(dependencies),
                "dependencies": list(dependencies)[:20],  # Limit for storage
                "internal_calls": internal_calls,
                "method_prefixes": dict(sorted(prefixes.items(), key=lambda x: -x[1])[:10]),
            }

    # Compute project-wide metrics
    project_metrics: dict[str, float] = {}

    if class_method_counts:
        sorted_counts = sorted(class_method_counts)
        n = len(sorted_counts)

        project_metrics = {
            "total_classes": n,
            "avg_methods": round(mean(class_method_counts), 1),
            "median_methods": round(median(class_method_counts), 1),
            "min_methods": min(class_method_counts),
            "max_methods": max(class_method_counts),
            "p90_methods": sorted_counts[int(n * 0.9)] if n > 0 else 0,
            "p95_methods": sorted_counts[int(n * 0.95)] if n > 0 else 0,
        }

        # Add percentile rank calculation helper info
        for node_id, metrics in node_metrics.items():
            if "method_count" in metrics:
                count = metrics["method_count"]
                # Calculate percentile rank
                rank = sum(1 for c in class_method_counts if c < count)
                metrics["percentile_rank"] = round(rank / n * 100, 1)

    return node_metrics, project_metrics


def get_class_analysis_from_stored(node: Node) -> dict:
    """Get class analysis from stored metrics.

    Args:
        node: The class node with pre-computed metrics

    Returns:
        Analysis dict with metrics and computed flags
    """
    metrics = node.metrics or {}

    return {
        "node_id": node.id,
        "name": node.name,
        "type": node.type.value,
        "method_count": metrics.get("method_count", 0),
        "lines": metrics.get("lines", 0),
        "public_methods": metrics.get("public_methods", 0),
        "private_methods": metrics.get("private_methods", 0),
        "dependency_count": metrics.get("dependency_count", 0),
        "dependencies": metrics.get("dependencies", []),
        "internal_calls": metrics.get("internal_calls", 0),
        "method_prefixes": metrics.get("method_prefixes", {}),
        "percentile_rank": metrics.get("percentile_rank", 0),
    }


def format_architecture_report(report: ArchitectureReport) -> str:
    """Format architecture report as human-readable text."""
    lines: list[str] = []

    # Patterns section
    if report.patterns:
        lines.append("═══ Detected Patterns ═══")
        lines.append("")
        for pattern in sorted(report.patterns, key=lambda p: -p.confidence):
            lines.append(f"  {pattern.pattern.value.upper()}: {pattern.node_id}")
            lines.append(f"    Confidence: {pattern.confidence:.0%}")
            for evidence in pattern.evidence:
                lines.append(f"    • {evidence}")
            lines.append("")

    # Components section
    if report.components:
        lines.append("═══ Components ═══")
        lines.append("")
        for comp in sorted(report.components, key=lambda c: -c.cohesion):
            pattern_str = f" [{comp.pattern.value}]" if comp.pattern else ""
            lines.append(f"  {comp.name}{pattern_str}")
            lines.append(f"    Path: {comp.path}")
            lines.append(f"    Cohesion: {comp.cohesion:.0%}")
            lines.append(f"    Classes: {len(comp.classes)}, Public API: {len(comp.public_api)}")
            if comp.delegates_to:
                lines.append(f"    Delegates to: {', '.join(comp.delegates_to)}")
            lines.append("")

    # Warnings section
    if report.warnings:
        lines.append("═══ Warnings ═══")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"  {warning}")
        lines.append("")

    # Recommendations section
    if report.recommendations:
        lines.append("═══ Recommendations ═══")
        lines.append("")
        for rec in report.recommendations:
            lines.append(f"  {rec}")
        lines.append("")

    return "\n".join(lines)
