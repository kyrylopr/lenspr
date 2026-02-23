"""Architecture metrics computation and component analysis for LensPR.

This module provides:
- Class metrics computation (method count, dependencies, etc.)
- Component grouping (directories with high cohesion)

Metrics are computed during init/sync and stored in the database.
Tools read pre-computed data — no computation at query time.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from lenspr.models import (
    Component,
    Edge,
    EdgeType,
    Node,
    NodeType,
)

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

            # Dependencies (unique external classes/modules called by this class or its methods)
            dependencies: set[str] = set()
            # Check edges from the class node itself AND all its methods
            all_class_edges = list(outgoing.get(node.id, []))
            for method in methods:
                all_class_edges.extend(outgoing.get(method.id, []))
            for edge in all_class_edges:
                if edge.type in (EdgeType.CALLS, EdgeType.IMPORTS):
                    target = all_nodes.get(edge.to_node)
                    if target and target.type in (
                        NodeType.METHOD, NodeType.FUNCTION, NodeType.CLASS
                    ):
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
