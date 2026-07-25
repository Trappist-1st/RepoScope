"""Repository Intelligence layer (Code Intelligence Graph v1)."""

from app.intelligence.adapter import build_knowledge_graph
from app.intelligence.ids import (
    edge_id,
    file_path_to_node_id,
    node_id_to_file_path,
    node_id_to_symbol_ref,
    parse_symbol_ref,
    symbol_ref_to_node_id,
)
from app.intelligence.io import (
    ARTIFACT_NAME,
    knowledge_graph_path,
    load_knowledge_graph,
    save_knowledge_graph,
    try_load_knowledge_graph,
)
from app.intelligence.models import (
    EdgeType,
    EvidenceSpan,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeGraphSource,
    KnowledgeGraphStats,
    KnowledgeNode,
    NodeKind,
)
from app.intelligence.query import (
    children_of,
    find_by_qualified_name,
    get_node,
    neighbors,
    nodes_by_kind,
)

__all__ = [
    "ARTIFACT_NAME",
    "EdgeType",
    "EvidenceSpan",
    "KnowledgeEdge",
    "KnowledgeGraph",
    "KnowledgeGraphSource",
    "KnowledgeGraphStats",
    "KnowledgeNode",
    "NodeKind",
    "build_knowledge_graph",
    "children_of",
    "edge_id",
    "file_path_to_node_id",
    "find_by_qualified_name",
    "get_node",
    "knowledge_graph_path",
    "load_knowledge_graph",
    "neighbors",
    "node_id_to_file_path",
    "node_id_to_symbol_ref",
    "nodes_by_kind",
    "parse_symbol_ref",
    "save_knowledge_graph",
    "symbol_ref_to_node_id",
    "try_load_knowledge_graph",
]
