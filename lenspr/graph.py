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


def get_structure(
    G: nx.DiGraph,
    max_depth: int = 2,
    mode: str = "full",
    limit: int = 100,
    offset: int = 0,
    path_prefix: str | None = None,
) -> dict:
    """
    Get compact project structure overview.

    Args:
        max_depth: 0=file names only, 1=with classes/functions, 2=with methods
        mode: "full" (names+signatures) or "summary" (counts only)
        limit: Max files to return (default 100)
        offset: Skip first N files (default 0)
        path_prefix: Filter to files starting with this path

    Returns a tree-like dict organized by file → class → functions.
    """
    from collections import defaultdict

    # Single pass: group all nodes by file and type (O(n))
    files: dict[str, dict] = {}
    methods_by_class: dict[str, list] = defaultdict(list)

    for nid, data in G.nodes(data=True):
        node_type = data.get("type", "")
        file_path = data.get("file_path", "")

        if not file_path:
            continue

        # Apply path prefix filter
        if path_prefix and not file_path.startswith(path_prefix):
            continue

        # Pre-group methods by their parent class (O(1) lookup later)
        if node_type == "method":
            # Method ID format: "module.Class.method" → parent is "module.Class"
            class_id = nid.rsplit(".", 1)[0]
            methods_by_class[class_id].append({
                "id": nid,
                "name": data.get("name", ""),
                "signature": data.get("signature", ""),
            })
            continue

        # Initialize file structure if needed
        if file_path not in files:
            files[file_path] = {
                "module": "",
                "classes": [],
                "functions": [],
                "blocks": [],
                "_class_count": 0,
                "_function_count": 0,
                "_method_count": 0,
                "_block_count": 0,
            }

        if node_type == "module":
            files[file_path]["module"] = nid
        elif node_type == "class":
            files[file_path]["classes"].append({
                "id": nid,
                "name": data.get("name", ""),
                "methods": [],  # Will be populated below
            })
            files[file_path]["_class_count"] += 1
        elif node_type == "function":
            files[file_path]["functions"].append({
                "id": nid,
                "name": data.get("name", ""),
                "signature": data.get("signature", ""),
            })
            files[file_path]["_function_count"] += 1
        elif node_type == "block":
            files[file_path]["blocks"].append({
                "id": nid,
                "name": data.get("name", ""),
            })
            files[file_path]["_block_count"] += 1

    # Attach methods to their classes (O(1) per class)
    for file_path, file_data in files.items():
        for cls in file_data["classes"]:
            cls_methods = methods_by_class.get(cls["id"], [])
            if max_depth > 1:
                cls["methods"] = cls_methods
            file_data["_method_count"] += len(cls_methods)

    # Sort files and apply pagination
    sorted_files = sorted(files.keys())
    total_files = len(sorted_files)
    paginated_files = sorted_files[offset : offset + limit]

    # Build result based on mode
    structure: list | dict
    if mode == "summary":
        # Summary mode: counts only, no details
        structure = [
            {
                "file": fp,
                "module": files[fp]["module"],
                "classes": files[fp]["_class_count"],
                "functions": files[fp]["_function_count"],
                "methods": files[fp]["_method_count"],
                "blocks": files[fp]["_block_count"],
            }
            for fp in paginated_files
        ]
    else:
        # Full mode: all details
        full_structure: dict = {}
        for fp in paginated_files:
            file_data = files[fp]
            # Remove internal count fields
            full_structure[fp] = {
                "module": file_data["module"],
                "classes": file_data["classes"],
                "functions": file_data["functions"],
                "blocks": file_data["blocks"],
            }
        structure = full_structure

    return {
        "structure": structure,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total_files": total_files,
            "has_more": offset + limit < total_files,
        },
    }
