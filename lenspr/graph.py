"""In-memory graph operations using NetworkX."""

from __future__ import annotations

import networkx as nx

from lenspr.models import Edge, EdgeType, Node


def build_graph(nodes: list[Node], edges: list[Edge]) -> nx.DiGraph:
    """
    Build a NetworkX directed graph from nodes and edges.

    Node attributes include all Node fields.
    Edge attributes include all Edge fields.
    """
    G: nx.DiGraph = nx.DiGraph()

    for node in nodes:
        G.add_node(node.id, **node.to_dict())

    for edge in edges:
        # Only add edge if at least one endpoint exists in the graph
        # (external modules won't have nodes)
        G.add_edge(
            edge.from_node,
            edge.to_node,
            **edge.to_dict(),
        )

    return G


def get_impact_zone(G: nx.DiGraph, node_id: str, depth: int = 2) -> dict:
    """
    Find all nodes that could be affected by changing a given node.

    Traverses incoming edges (who depends on this node) up to `depth` levels.

    Returns:
        Dict with direct_callers, indirect_callers, inheritors, total_affected.
    """
    if node_id not in G:
        return {
            "node": node_id,
            "direct_callers": [],
            "indirect_callers": [],
            "inheritors": [],
            "total_affected": 0,
            "untracked_warnings": [],
        }

    direct_callers: list[str] = []
    inheritors: list[str] = []
    untracked: list[str] = []

    # Direct predecessors (nodes that have an edge TO this node)
    for predecessor in G.predecessors(node_id):
        edge_data = G.edges[predecessor, node_id]
        edge_type = edge_data.get("type", "")

        if edge_type == EdgeType.INHERITS.value:
            inheritors.append(predecessor)
        else:
            direct_callers.append(predecessor)

        # Check for untracked edges
        if edge_data.get("confidence") == "unresolved":
            reason = edge_data.get("untracked_reason", "unknown")
            untracked.append(f"{predecessor} → {node_id}: {reason}")

    # Indirect callers (depth > 1)
    indirect_callers: list[str] = []
    if depth > 1:
        visited = {node_id}
        frontier = set(direct_callers + inheritors)
        for _level in range(depth - 1):
            next_frontier: set[str] = set()
            for nid in frontier:
                if nid in visited:
                    continue
                visited.add(nid)
                for pred in G.predecessors(nid):
                    if pred not in visited and pred not in set(direct_callers):
                        indirect_callers.append(pred)
                        next_frontier.add(pred)
            frontier = next_frontier

    # Deduplicate
    indirect_callers = list(dict.fromkeys(indirect_callers))

    all_affected = set(direct_callers + indirect_callers + inheritors)

    return {
        "node": node_id,
        "direct_callers": direct_callers,
        "indirect_callers": indirect_callers,
        "inheritors": inheritors,
        "total_affected": len(all_affected),
        "untracked_warnings": untracked,
    }


def get_dependency_tree(G: nx.DiGraph, node_id: str, max_depth: int = 3) -> dict:
    """
    Get tree of what this node depends on (outgoing edges).

    Returns nested dict showing dependency hierarchy.
    """
    def _build(nid: str, depth: int, visited: set) -> dict:
        if depth <= 0 or nid in visited:
            return {"id": nid, "dependencies": [], "truncated": depth <= 0}

        visited.add(nid)
        deps = []
        for successor in G.successors(nid):
            edge_data = G.edges[nid, successor]
            deps.append({
                "edge_type": edge_data.get("type", "unknown"),
                "target": _build(successor, depth - 1, visited),
            })

        return {"id": nid, "dependencies": deps, "truncated": False}

    return _build(node_id, max_depth, set())


def find_dead_code(G: nx.DiGraph, entry_points: list[str]) -> list[str]:
    """
    Find nodes not reachable from any entry point.

    Args:
        entry_points: Node IDs that are known entry points
                     (main(), API endpoints, CLI commands, test functions).
    """
    reachable: set[str] = set()

    for ep in entry_points:
        if ep in G:
            reachable.update(nx.descendants(G, ep))
            reachable.add(ep)

    all_nodes = set(G.nodes)
    unreachable = all_nodes - reachable

    # Filter: only return nodes that are actual code (not external modules)
    return [
        nid for nid in sorted(unreachable)
        if G.nodes[nid].get("type") in ("function", "method", "class")
    ]


def find_path(G: nx.DiGraph, from_id: str, to_id: str) -> list[str]:
    """Find shortest path between two nodes. Returns empty list if no path."""
    try:
        return list(nx.shortest_path(G, from_id, to_id))
    except (nx.NodeNotFound, nx.NetworkXNoPath):
        return []


def get_subgraph(G: nx.DiGraph, node_ids: set[str]) -> nx.DiGraph:
    """Extract subgraph containing only specified nodes and edges between them."""
    subgraph: nx.DiGraph = G.subgraph(node_ids).copy()  # type: ignore[assignment]
    return subgraph


def detect_circular_imports(G: nx.DiGraph) -> list[list[str]]:
    """Find circular import chains in the graph."""
    # Build subgraph with only import edges
    import_edges = [
        (u, v) for u, v, d in G.edges(data=True)
        if d.get("type") == EdgeType.IMPORTS.value
    ]
    import_graph = nx.DiGraph(import_edges)

    cycles = []
    for cycle in nx.simple_cycles(import_graph):
        if len(cycle) > 1:  # Skip self-loops
            cycles.append(cycle)

    return cycles


def get_structure(G: nx.DiGraph, max_depth: int = 2) -> dict:
    """
    Get compact project structure overview.

    Returns a tree-like dict organized by file → class → functions.
    """
    structure: dict = {}

    for nid, data in G.nodes(data=True):
        node_type = data.get("type", "")
        file_path = data.get("file_path", "")

        if node_type == "module":
            if file_path not in structure:
                structure[file_path] = {
                    "module": nid,
                    "classes": [],
                    "functions": [],
                    "blocks": [],
                }
        elif node_type == "class":
            if file_path in structure:
                methods = [
                    {
                        "id": mid,
                        "name": mdata.get("name", ""),
                        "signature": mdata.get("signature", ""),
                    }
                    for mid, mdata in G.nodes(data=True)
                    if mdata.get("type") == "method" and mid.startswith(nid + ".")
                ]
                default = {"module": "", "classes": [], "functions": [], "blocks": []}
                structure.setdefault(file_path, default)
                structure[file_path]["classes"].append({
                    "id": nid,
                    "name": data.get("name", ""),
                    "methods": methods if max_depth > 1 else [],
                })
        elif node_type == "function":
            default = {"module": "", "classes": [], "functions": [], "blocks": []}
            structure.setdefault(file_path, default)
            structure[file_path]["functions"].append({
                "id": nid,
                "name": data.get("name", ""),
                "signature": data.get("signature", ""),
            })
        elif node_type == "block":
            default = {"module": "", "classes": [], "functions": [], "blocks": []}
            structure.setdefault(file_path, default)
            structure[file_path]["blocks"].append({
                "id": nid,
                "name": data.get("name", ""),
            })

    return structure
