"""Minimal KnowledgeGraph queries for Iteration 1."""

from __future__ import annotations

from app.intelligence.models import EdgeType, KnowledgeEdge, KnowledgeGraph, KnowledgeNode, NodeKind


def get_node(graph: KnowledgeGraph, node_id: str) -> KnowledgeNode | None:
    for node in graph.nodes:
        if node.id == node_id:
            return node
    return None


def nodes_by_kind(graph: KnowledgeGraph, kind: NodeKind | str) -> list[KnowledgeNode]:
    kind_value = kind.value if isinstance(kind, NodeKind) else kind
    return [n for n in graph.nodes if n.kind.value == kind_value]


def children_of(graph: KnowledgeGraph, parent_id: str) -> list[KnowledgeNode]:
    return sorted(
        [n for n in graph.nodes if n.parent_id == parent_id],
        key=lambda n: n.id,
    )


def neighbors(
    graph: KnowledgeGraph,
    node_id: str,
    *,
    edge_type: EdgeType | str | None = None,
    direction: str = "out",
) -> list[KnowledgeEdge]:
    """
    Return edges adjacent to node_id.

    direction:
      - "out": edges where source_id == node_id
      - "in":  edges where target_id == node_id
      - "both": union
    """
    et = None
    if edge_type is not None:
        et = edge_type.value if isinstance(edge_type, EdgeType) else edge_type

    if direction not in {"out", "in", "both"}:
        raise ValueError(f"Invalid direction: {direction!r}")

    out: list[KnowledgeEdge] = []
    seen: set[str] = set()
    for edge in graph.edges:
        if et is not None and edge.edge_type.value != et:
            continue
        match = False
        if direction in {"out", "both"} and edge.source_id == node_id:
            match = True
        if direction in {"in", "both"} and edge.target_id == node_id:
            match = True
        if match and edge.id not in seen:
            seen.add(edge.id)
            out.append(edge)
    return out


def find_by_qualified_name(graph: KnowledgeGraph, qualified_name: str) -> KnowledgeNode | None:
    for node in graph.nodes:
        if node.qualified_name == qualified_name:
            return node
    return None
