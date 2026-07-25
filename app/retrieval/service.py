from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from app.config import settings
from app.models.schemas import Chunk, DependencyGraph
from app.retrieval.bm25_index import BM25Index
from app.retrieval.config import RetrievalConfig, load_retrieval_config
from app.retrieval.embedder import Embedder, create_embedder
from app.retrieval.graph_expand import expand_one_hop
from app.retrieval.hybrid import reciprocal_rank_fusion, weighted_fusion
from app.retrieval.rerank import Reranker, create_reranker
from app.retrieval.schemas import (
    Citation,
    IndexRequest,
    IndexResult,
    RetrievalHit,
    RetrieveRequest,
    RetrieveResponse,
)
from app.retrieval.vector_store import (
    VectorStore,
    chunk_payload,
    collection_name,
    create_vector_store,
)


class RetrievalService:
    def __init__(
        self,
        config: RetrievalConfig | None = None,
        artifact_dir: Path | None = None,
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.config = config or load_retrieval_config()
        self.artifact_dir = Path(artifact_dir or settings.artifact_dir)
        self.embedder = embedder or create_embedder(self.config.embedding)
        self.vector_store = vector_store or create_vector_store(
            self.config.vector_backend,
            self.config.qdrant_url,
        )
        self.reranker = reranker  # lazy if None — created per retrieve with skip flag
        self._lock = RLock()
        self._bm25: dict[str, BM25Index] = {}
        self._chunks: dict[str, list[Chunk]] = {}
        self._graphs: dict[str, DependencyGraph] = {}

    def index(self, request: IndexRequest) -> IndexResult:
        chunks = request.chunks
        if chunks is None:
            chunks = self._load_chunks(request.repo_id)
        if chunks is None:
            raise FileNotFoundError(
                f"No chunks for repo_id={request.repo_id}; run ingestion first "
                f"or pass IndexRequest.chunks"
            )

        coll = collection_name(request.repo_id)
        if request.force_reindex:
            self.vector_store.delete_collection(coll)

        texts = [c.content for c in chunks]
        vectors = self.embedder.embed(texts)
        payloads = [chunk_payload(c, request.repo_id) for c in chunks]
        ids = [c.chunk_id for c in chunks]
        self.vector_store.upsert(coll, ids, vectors, payloads)

        bm25 = BM25Index(chunks)
        bm25_path = self._bm25_path(request.repo_id)
        bm25.save(bm25_path)

        # Keep graph cache warm for expand
        graph = self._load_graph(request.repo_id)

        with self._lock:
            self._bm25[request.repo_id] = bm25
            self._chunks[request.repo_id] = chunks
            if graph is not None:
                self._graphs[request.repo_id] = graph
            else:
                self._graphs.pop(request.repo_id, None)

        return IndexResult(
            repo_id=request.repo_id,
            indexed_count=len(chunks),
            collection_name=coll,
            bm25_docs=bm25.size,
            backend=self.config.backend_label,
        )

    def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        d = self.config.defaults
        f = self.config.fusion
        top_k_vector = request.top_k_vector if request.top_k_vector is not None else d.top_k_vector
        top_k_bm25 = request.top_k_bm25 if request.top_k_bm25 is not None else d.top_k_bm25
        fusion = request.fusion or f.default
        weight_vector = request.weight_vector if request.weight_vector is not None else f.weight_vector
        weight_bm25 = request.weight_bm25 if request.weight_bm25 is not None else f.weight_bm25
        rrf_k = request.rrf_k if request.rrf_k is not None else f.rrf_k
        rerank_top_n = request.rerank_top_n if request.rerank_top_n is not None else d.rerank_top_n
        final_top_n = request.final_top_n if request.final_top_n is not None else d.final_top_n
        do_expand = request.graph_expand if request.graph_expand is not None else d.graph_expand
        expand_limit = (
            request.graph_expand_limit
            if request.graph_expand_limit is not None
            else d.graph_expand_limit
        )
        skip_rerank = (
            request.skip_rerank
            if request.skip_rerank is not None
            else not self.config.rerank.enabled
        )

        chunks = self._ensure_chunks(request.repo_id)
        chunk_by_id = {c.chunk_id: c for c in chunks}
        bm25 = self._ensure_bm25(request.repo_id, chunks)
        coll = collection_name(request.repo_id)

        vector_hits: list[tuple[str, float, dict]] = []
        bm25_hits: list[tuple[str, float]] = []

        if request.mode in {"hybrid", "vector"}:
            qvec = self.embedder.embed([request.query])[0]
            for vh in self.vector_store.search(coll, qvec, top_k_vector):
                vector_hits.append((vh.chunk_id, vh.score, vh.payload))

        if request.mode in {"hybrid", "bm25"}:
            for chunk, score in bm25.search(request.query, top_k_bm25):
                bm25_hits.append((chunk.chunk_id, score))

        diagnostics: dict = {
            "backend": self.config.backend_label,
            "mode": request.mode,
            "fusion": fusion if request.mode == "hybrid" else None,
            "rerank_skipped": skip_rerank,
            "vector_hits": len(vector_hits),
            "bm25_hits": len(bm25_hits),
            "embedding_model": self.config.embedding.model,
            "rerank_model": self.config.rerank.model,
        }

        # Build payload/score maps
        payloads: dict[str, dict] = {}
        for cid, score, payload in vector_hits:
            payloads[cid] = payload or chunk_payload(chunk_by_id[cid], request.repo_id)
            payloads[cid].setdefault("content", chunk_by_id[cid].content)
        for cid, _score in bm25_hits:
            if cid not in payloads and cid in chunk_by_id:
                payloads[cid] = chunk_payload(chunk_by_id[cid], request.repo_id)

        vector_scores = {cid: score for cid, score, _ in vector_hits}
        bm25_scores = {cid: score for cid, score in bm25_hits}

        if request.mode == "vector":
            ordered = [
                (cid, score, {"vector": score}) for cid, score, _ in vector_hits
            ]
        elif request.mode == "bm25":
            ordered = [(cid, score, {"bm25": score}) for cid, score in bm25_hits]
        elif fusion == "weighted":
            ordered = weighted_fusion(
                {"vector": vector_scores, "bm25": bm25_scores},
                {"vector": weight_vector, "bm25": weight_bm25},
            )
        else:
            ranked_lists = {
                "vector": [cid for cid, _, _ in vector_hits],
                "bm25": [cid for cid, _ in bm25_hits],
            }
            ordered = reciprocal_rank_fusion(ranked_lists, k=rrf_k)
            # attach raw channel scores into detail
            enriched = []
            for cid, score, parts in ordered:
                detail = {
                    **parts,
                    **({"vector": vector_scores[cid]} if cid in vector_scores else {}),
                    **({"bm25": bm25_scores[cid]} if cid in bm25_scores else {}),
                    "rrf": score,
                }
                enriched.append((cid, score, detail))
            ordered = enriched

        diagnostics["fused"] = len(ordered)

        fused_hits: list[RetrievalHit] = []
        for cid, score, detail in ordered[: max(rerank_top_n, final_top_n) * 2]:
            chunk = chunk_by_id.get(cid)
            payload = payloads.get(cid, {})
            if chunk is None and not payload:
                continue
            citation = Citation(
                file_path=payload.get("file_path") or (chunk.file_path if chunk else ""),
                start_line=int(payload.get("start_line") or (chunk.start_line if chunk else 0)),
                end_line=int(payload.get("end_line") or (chunk.end_line if chunk else 0)),
            )
            fused_hits.append(
                RetrievalHit(
                    chunk_id=cid,
                    content=payload.get("content") or (chunk.content if chunk else ""),
                    citation=citation,
                    score=float(score),
                    source="hybrid" if request.mode == "hybrid" else request.mode,
                    symbol_name=payload.get("symbol_name")
                    or (chunk.symbol_name if chunk else None),
                    kind=payload.get("chunk_type") or (chunk.kind if chunk else None),
                    language=payload.get("language") or (chunk.language if chunk else None),
                    scores={k: float(v) for k, v in detail.items()},
                )
            )

        # Cap candidates entering rerank
        fused_hits = fused_hits[:rerank_top_n]
        reranker = self.reranker or create_reranker(self.config.rerank, force_skip=skip_rerank)
        hits = reranker.rerank(request.query, fused_hits, top_n=final_top_n)
        diagnostics["reranked"] = len(hits)

        expanded: list[RetrievalHit] = []
        if do_expand:
            graph = self._ensure_graph(request.repo_id)
            if graph is not None:
                expanded = expand_one_hop(hits, graph, chunks, limit=expand_limit)
        diagnostics["expanded"] = len(expanded)

        return RetrieveResponse(
            repo_id=request.repo_id,
            query=request.query,
            hits=hits,
            expanded_hits=expanded,
            expansion_depth=1 if do_expand else 0,
            diagnostics=diagnostics,
        )

    def explore(self, repo_id: str, limit: int = 5) -> list[RetrievalHit]:
        """
        Diversity sample of indexed chunks when query recall is zero.
        Prefers distinct files so analyze still has grounded citations.
        """
        try:
            chunks = self._ensure_chunks(repo_id)
        except FileNotFoundError:
            return []
        if not chunks or limit <= 0:
            return []

        picked: list[Chunk] = []
        seen_files: set[str] = set()
        # Pass 1: one chunk per file
        for chunk in chunks:
            if chunk.file_path in seen_files:
                continue
            seen_files.add(chunk.file_path)
            picked.append(chunk)
            if len(picked) >= limit:
                break
        # Pass 2: fill remaining
        if len(picked) < limit:
            have = {c.chunk_id for c in picked}
            for chunk in chunks:
                if chunk.chunk_id in have:
                    continue
                picked.append(chunk)
                if len(picked) >= limit:
                    break

        hits: list[RetrievalHit] = []
        for i, chunk in enumerate(picked):
            hits.append(
                RetrievalHit(
                    chunk_id=chunk.chunk_id,
                    content=chunk.content,
                    citation=Citation.from_chunk(chunk),
                    score=max(0.01, 0.2 - i * 0.01),
                    source="explore",
                    symbol_name=chunk.symbol_name,
                    kind=chunk.kind,
                    language=chunk.language,
                    scores={"explore": 1.0},
                    expansion_reason="fallback:explore_chunks",
                )
            )
        return hits

    def delete_repo_index(self, repo_id: str) -> None:
        self.vector_store.delete_collection(collection_name(repo_id))
        with self._lock:
            self._bm25.pop(repo_id, None)
            self._chunks.pop(repo_id, None)
            self._graphs.pop(repo_id, None)
        path = self._bm25_path(repo_id)
        if path.exists():
            path.unlink()

    def _bm25_path(self, repo_id: str) -> Path:
        return self.artifact_dir / repo_id / "bm25.pkl"

    def _load_chunks(self, repo_id: str) -> list[Chunk] | None:
        path = self.artifact_dir / repo_id / "chunks.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [Chunk.model_validate(item) for item in raw]

    def _load_graph(self, repo_id: str) -> DependencyGraph | None:
        path = self.artifact_dir / repo_id / "graph.json"
        if not path.exists():
            return None
        return DependencyGraph.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _ensure_chunks(self, repo_id: str) -> list[Chunk]:
        with self._lock:
            cached = self._chunks.get(repo_id)
        if cached is not None:
            return cached
        chunks = self._load_chunks(repo_id)
        if chunks is None:
            raise FileNotFoundError(f"Chunks not found for repo_id={repo_id}")
        with self._lock:
            existing = self._chunks.get(repo_id)
            if existing is not None:
                return existing
            self._chunks[repo_id] = chunks
            return chunks

    def _ensure_bm25(self, repo_id: str, chunks: list[Chunk]) -> BM25Index:
        with self._lock:
            cached = self._bm25.get(repo_id)
        if cached is not None:
            return cached
        path = self._bm25_path(repo_id)
        if path.exists():
            index = BM25Index.load(path)
        else:
            index = BM25Index(chunks)
            index.save(path)
        with self._lock:
            existing = self._bm25.get(repo_id)
            if existing is not None:
                return existing
            self._bm25[repo_id] = index
            return index

    def _ensure_graph(self, repo_id: str) -> DependencyGraph | None:
        with self._lock:
            if repo_id in self._graphs:
                return self._graphs[repo_id]
        graph = self._load_graph(repo_id)
        if graph is None:
            return None
        with self._lock:
            existing = self._graphs.get(repo_id)
            if existing is not None:
                return existing
            self._graphs[repo_id] = graph
            return graph
