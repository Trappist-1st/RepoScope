from __future__ import annotations

from dataclasses import dataclass

from app.graph.query import callees_of, callers_of
from app.models.schemas import Chunk, DependencyGraph
from app.retrieval.schemas import Citation, RetrievalHit


@dataclass
class _SymbolIndex:
    by_ref: dict[str, Chunk]
    class_chunks: dict[str, Chunk]

    def resolve(self, symbol_ref: str) -> Chunk | None:
        chunk = self.by_ref.get(symbol_ref)
        if chunk is not None:
            return chunk
        if "::" not in symbol_ref:
            return None
        file_path, _, sym = symbol_ref.partition("::")
        if "." not in sym:
            return None
        class_name = sym.split(".", 1)[0]
        return self.class_chunks.get(f"{file_path}::{class_name}")


def _symbol_ref_from_chunk(chunk: Chunk) -> str | None:
    if not chunk.symbol_name:
        return None
    return f"{chunk.file_path}::{chunk.symbol_name}"


def _build_symbol_index(chunks: list[Chunk]) -> _SymbolIndex:
    by_ref: dict[str, Chunk] = {}
    class_chunks: dict[str, Chunk] = {}
    for chunk in chunks:
        ref = _symbol_ref_from_chunk(chunk)
        if ref:
            by_ref[ref] = chunk
        if chunk.symbol_name:
            by_ref[f"{chunk.file_path}::{chunk.symbol_name.split('.')[-1]}"] = chunk
        if chunk.kind == "class" and chunk.symbol_name:
            class_chunks[f"{chunk.file_path}::{chunk.symbol_name}"] = chunk
    return _SymbolIndex(by_ref=by_ref, class_chunks=class_chunks)


def expand_one_hop(
    hits: list[RetrievalHit],
    graph: DependencyGraph,
    chunks: list[Chunk],
    limit: int = 4,
) -> list[RetrievalHit]:
    """
    One-hop callers/callees of main hits.
    Returns expanded_hits with expansion_reason like 'called_by: X' / 'calls: Y'.
    """
    if limit <= 0 or not hits:
        return []

    symbol_index = _build_symbol_index(chunks)
    seen_ids = {h.chunk_id for h in hits}
    expanded: list[RetrievalHit] = []

    for hit in hits:
        if len(expanded) >= limit:
            break
        if not hit.symbol_name:
            continue
        ref = f"{hit.citation.file_path}::{hit.symbol_name}"

        for caller_ref in callers_of(graph, ref):
            if len(expanded) >= limit:
                break
            chunk = symbol_index.resolve(caller_ref)
            if chunk is None or chunk.chunk_id in seen_ids:
                continue
            seen_ids.add(chunk.chunk_id)
            expanded.append(_hit_from_chunk(chunk, reason=f"called_by: {caller_ref}"))

        for callee_ref in callees_of(graph, ref):
            if len(expanded) >= limit:
                break
            chunk = symbol_index.resolve(callee_ref)
            if chunk is None or chunk.chunk_id in seen_ids:
                continue
            seen_ids.add(chunk.chunk_id)
            expanded.append(_hit_from_chunk(chunk, reason=f"calls: {callee_ref}"))

    return expanded[:limit]


def _hit_from_chunk(chunk: Chunk, reason: str) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk.chunk_id,
        content=chunk.content,
        citation=Citation.from_chunk(chunk),
        score=0.0,
        source="graph_expand",
        symbol_name=chunk.symbol_name,
        kind=chunk.kind,
        language=chunk.language,
        expansion_reason=reason,
    )
