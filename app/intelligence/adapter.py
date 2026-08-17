"""Project DependencyGraph (+ definitions) into KnowledgeGraph."""

from __future__ import annotations

from app.intelligence.ids import (
    edge_id,
    file_path_to_node_id,
    parse_symbol_ref,
    symbol_ref_to_node_id,
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
    bucket_confidence,
)
from app.models.schemas import Definition, DependencyGraph, SymbolKind
from app.parsing.languages import detect_language


def build_knowledge_graph(
    dependency_graph: DependencyGraph,
    definitions_by_file: dict[str, list[Definition]] | None = None,
    *,
    advanced: bool = False,
) -> KnowledgeGraph:
    """
    Projection of DependencyGraph into KnowledgeGraph.

    - Nodes: file / class / function / method
    - Edges: import / call / inherit (extends|implements in edge.meta.relation)
    - Structure (file→class→method) via parent_id, not extra edge types

    With ``advanced`` the cascade's numeric score is carried through as
    ``confidence_score`` and bucketed into the literal ``confidence`` that
    FlowTracer's beam search consumes, and the originating line becomes an
    :class:`EvidenceSpan` so the edge itself is citable.
    """
    defs = definitions_by_file or {}
    has_definitions = bool(defs)

    nodes: dict[str, KnowledgeNode] = {}
    edges: dict[str, KnowledgeEdge] = {}
    orphan_refs: list[str] = []

    file_paths = _collect_file_paths(dependency_graph, defs)
    for path in sorted(file_paths):
        nodes[file_path_to_node_id(path)] = _make_file_node(path)

    class_ids_by_file: dict[str, dict[str, str]] = {}
    if has_definitions:
        for path, definitions in defs.items():
            file_id = file_path_to_node_id(path)
            class_ids_by_file[path] = {}
            # Pass 1: classes + top-level functions
            for d in definitions:
                if d.kind == SymbolKind.CLASS:
                    node = _make_symbol_node(path, d, parent_id=file_id)
                    nodes[node.id] = node
                    class_ids_by_file[path][d.name] = node.id
                elif d.kind == SymbolKind.FUNCTION:
                    node = _make_symbol_node(path, d, parent_id=file_id)
                    nodes[node.id] = node
            # Pass 2: methods (parent = class when known)
            for d in definitions:
                if d.kind != SymbolKind.METHOD:
                    continue
                parent_id = file_id
                if d.parent_name and d.parent_name in class_ids_by_file[path]:
                    parent_id = class_ids_by_file[path][d.parent_name]
                node = _make_symbol_node(path, d, parent_id=parent_id)
                nodes[node.id] = node

    for e in dependency_graph.file_edges:
        src = file_path_to_node_id(e.source)
        tgt = file_path_to_node_id(e.target)
        _ensure_file_node(nodes, e.source)
        _ensure_file_node(nodes, e.target)
        eid = edge_id(EdgeType.IMPORT.value, src, tgt)
        edges[eid] = KnowledgeEdge(
            id=eid,
            source_id=src,
            target_id=tgt,
            edge_type=EdgeType.IMPORT,
            confidence="high",
            confidence_score=1.0 if advanced else None,
            resolution_strategy="import_map" if advanced else None,
            evidence=_evidence(e.source, e.import_line) if advanced else [],
            meta={"from": "file_dependency_edge"},
        )

    for e in dependency_graph.call_edges:
        caller_id = symbol_ref_to_node_id(e.caller)
        callee_id = symbol_ref_to_node_id(e.callee)
        _ensure_symbol_node_from_ref(nodes, e.caller, orphan_refs)
        _ensure_symbol_node_from_ref(nodes, e.callee, orphan_refs)
        eid = edge_id(EdgeType.CALL.value, caller_id, callee_id)
        edges[eid] = KnowledgeEdge(
            id=eid,
            source_id=caller_id,
            target_id=callee_id,
            edge_type=EdgeType.CALL,
            confidence=(
                bucket_confidence(e.confidence)
                if advanced
                else ("high" if e.same_file else "medium")
            ),
            confidence_score=e.confidence if advanced else None,
            resolution_strategy=e.resolution_strategy if advanced else None,
            evidence=(
                _evidence(e.caller.split("::", 1)[0], e.call_line) if advanced else []
            ),
            meta={"same_file": e.same_file, "from": "call_edge"},
        )

    for e in dependency_graph.inherit_edges:
        child_id = symbol_ref_to_node_id(e.child)
        parent_id = symbol_ref_to_node_id(e.parent)
        _ensure_symbol_node_from_ref(nodes, e.child, orphan_refs)
        _ensure_symbol_node_from_ref(nodes, e.parent, orphan_refs)
        # Force class kind when we know these are type symbols
        for nid in (child_id, parent_id):
            node = nodes.get(nid)
            if node is not None and node.kind == NodeKind.FUNCTION:
                nodes[nid] = node.model_copy(update={"kind": NodeKind.CLASS})
        eid = edge_id(EdgeType.INHERIT.value, child_id, parent_id)
        edges[eid] = KnowledgeEdge(
            id=eid,
            source_id=child_id,
            target_id=parent_id,
            edge_type=EdgeType.INHERIT,
            confidence=(
                bucket_confidence(e.confidence)
                if advanced
                else ("high" if e.same_file else "medium")
            ),
            confidence_score=e.confidence if advanced else None,
            resolution_strategy=e.resolution_strategy if advanced else None,
            evidence=(
                _evidence(e.child.split("::", 1)[0], e.decl_line) if advanced else []
            ),
            meta={
                "relation": e.relation,
                "same_file": e.same_file,
                "from": "inherit_edge",
            },
        )

    stats = _compute_stats(nodes, edges, orphan_refs)
    return KnowledgeGraph(
        schema_version="1.0",
        repo_id=dependency_graph.repo_id,
        commit_hash=dependency_graph.commit_hash,
        nodes=sorted(nodes.values(), key=lambda n: (n.kind.value, n.id)),
        edges=sorted(edges.values(), key=lambda e: e.id),
        stats=stats,
        source=KnowledgeGraphSource(
            dependency_graph=True,
            definitions=has_definitions,
            inherit_supported=True,
            advanced=advanced,
        ),
    )


def _evidence(file_path: str, line: int | None) -> list[EvidenceSpan]:
    if not file_path or line is None:
        return []
    return [EvidenceSpan(file_path=file_path.replace("\\", "/"), start_line=line)]


def _collect_file_paths(
    graph: DependencyGraph,
    defs: dict[str, list[Definition]],
) -> set[str]:
    paths: set[str] = set(defs.keys())
    for e in graph.file_edges:
        paths.add(e.source.replace("\\", "/"))
        paths.add(e.target.replace("\\", "/"))
    for e in graph.call_edges:
        for ref in (e.caller, e.callee):
            try:
                path, _ = parse_symbol_ref(ref)
                paths.add(path)
            except ValueError:
                continue
    for e in graph.inherit_edges:
        for ref in (e.child, e.parent):
            try:
                path, _ = parse_symbol_ref(ref)
                paths.add(path)
            except ValueError:
                continue
    return paths


def _make_file_node(path: str) -> KnowledgeNode:
    posix = path.replace("\\", "/")
    return KnowledgeNode(
        id=file_path_to_node_id(posix),
        kind=NodeKind.FILE,
        name=posix,
        qualified_name=posix,
        file_path=posix,
        language=detect_language(posix),
        parent_id=None,
        meta={},
    )


def _make_symbol_node(
    file_path: str,
    definition: Definition,
    *,
    parent_id: str,
) -> KnowledgeNode:
    posix = file_path.replace("\\", "/")
    if definition.kind == SymbolKind.METHOD and definition.parent_name:
        symbol_part = f"{definition.parent_name}.{definition.name}"
        kind = NodeKind.METHOD
    elif definition.kind == SymbolKind.CLASS:
        symbol_part = definition.name
        kind = NodeKind.CLASS
    elif definition.kind == SymbolKind.METHOD:
        symbol_part = definition.name
        kind = NodeKind.METHOD
    else:
        symbol_part = definition.name
        kind = NodeKind.FUNCTION

    qualified = f"{posix}::{symbol_part}"
    return KnowledgeNode(
        id=symbol_ref_to_node_id(qualified),
        kind=kind,
        name=definition.name,
        qualified_name=qualified,
        file_path=posix,
        start_line=definition.start_line,
        end_line=definition.end_line,
        language=definition.language,
        parent_id=parent_id,
        meta={},
    )


def _ensure_file_node(nodes: dict[str, KnowledgeNode], path: str) -> None:
    posix = path.replace("\\", "/")
    nid = file_path_to_node_id(posix)
    if nid not in nodes:
        nodes[nid] = _make_file_node(posix)


def _ensure_symbol_node_from_ref(
    nodes: dict[str, KnowledgeNode],
    symbol_ref: str,
    orphan_refs: list[str],
) -> None:
    nid = symbol_ref_to_node_id(symbol_ref)
    if nid in nodes:
        return
    try:
        file_path, symbol_part = parse_symbol_ref(symbol_ref)
    except ValueError:
        orphan_refs.append(symbol_ref)
        return

    _ensure_file_node(nodes, file_path)
    file_id = file_path_to_node_id(file_path)

    if "." in symbol_part:
        class_name, _, method_name = symbol_part.partition(".")
        class_qualified = f"{file_path}::{class_name}"
        class_id = symbol_ref_to_node_id(class_qualified)
        if class_id not in nodes:
            nodes[class_id] = KnowledgeNode(
                id=class_id,
                kind=NodeKind.CLASS,
                name=class_name,
                qualified_name=class_qualified,
                file_path=file_path,
                parent_id=file_id,
                meta={"source": "call_edge_inferred"},
            )
        nodes[nid] = KnowledgeNode(
            id=nid,
            kind=NodeKind.METHOD,
            name=method_name,
            qualified_name=f"{file_path}::{symbol_part}",
            file_path=file_path,
            parent_id=class_id,
            meta={"source": "call_edge_inferred"},
        )
    else:
        # Ambiguous: could be class or function; treat as function shadow node
        nodes[nid] = KnowledgeNode(
            id=nid,
            kind=NodeKind.FUNCTION,
            name=symbol_part,
            qualified_name=f"{file_path}::{symbol_part}",
            file_path=file_path,
            parent_id=file_id,
            meta={"source": "call_edge_inferred"},
        )
    orphan_refs.append(f"{file_path}::{symbol_part}")


def _compute_stats(
    nodes: dict[str, KnowledgeNode],
    edges: dict[str, KnowledgeEdge],
    orphan_refs: list[str],
) -> KnowledgeGraphStats:
    node_counts: dict[str, int] = {k.value: 0 for k in NodeKind}
    for n in nodes.values():
        node_counts[n.kind.value] = node_counts.get(n.kind.value, 0) + 1

    edge_counts: dict[str, int] = {e.value: 0 for e in EdgeType}
    for e in edges.values():
        edge_counts[e.edge_type.value] = edge_counts.get(e.edge_type.value, 0) + 1

    # unique preserve order
    seen: set[str] = set()
    orphans: list[str] = []
    for ref in orphan_refs:
        if ref not in seen:
            seen.add(ref)
            orphans.append(ref)

    return KnowledgeGraphStats(
        node_counts=node_counts,
        edge_counts=edge_counts,
        orphan_symbol_refs=orphans,
    )
