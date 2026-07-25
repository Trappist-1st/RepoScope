from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from app.models.schemas import Chunk
from app.retrieval.embedder import l2_normalize


@dataclass
class VectorHit:
    chunk_id: str
    score: float
    payload: dict[str, Any]


@runtime_checkable
class VectorStore(Protocol):
    def upsert(self, collection: str, ids: list[str], vectors: np.ndarray, payloads: list[dict]) -> None: ...

    def search(self, collection: str, query_vector: np.ndarray, top_k: int) -> list[VectorHit]: ...

    def delete_collection(self, collection: str) -> None: ...

    def collection_exists(self, collection: str) -> bool: ...


def collection_name(repo_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in repo_id)
    return f"reposcope_{safe}"


class InMemoryVectorStore:
    """
    Brute-force cosine search via L2-normalized dot product.
    Distance metric MUST match Qdrant (cosine) so eval numbers stay comparable.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def upsert(
        self,
        collection: str,
        ids: list[str],
        vectors: np.ndarray,
        payloads: list[dict],
    ) -> None:
        if collection not in self._data:
            self._data[collection] = {"ids": [], "vectors": None, "payloads": [], "id_to_idx": {}}
        bucket = self._data[collection]
        vectors = np.asarray(vectors, dtype=np.float32)
        vectors = l2_normalize(vectors)

        for i, cid in enumerate(ids):
            if cid in bucket["id_to_idx"]:
                idx = bucket["id_to_idx"][cid]
                bucket["vectors"][idx] = vectors[i]
                bucket["payloads"][idx] = payloads[i]
            else:
                bucket["id_to_idx"][cid] = len(bucket["ids"])
                bucket["ids"].append(cid)
                bucket["payloads"].append(payloads[i])
                if bucket["vectors"] is None:
                    bucket["vectors"] = vectors[i : i + 1].copy()
                else:
                    bucket["vectors"] = np.vstack([bucket["vectors"], vectors[i : i + 1]])

    def search(self, collection: str, query_vector: np.ndarray, top_k: int) -> list[VectorHit]:
        bucket = self._data.get(collection)
        if not bucket or bucket["vectors"] is None or top_k <= 0:
            return []
        q = l2_normalize(np.asarray(query_vector, dtype=np.float32).reshape(1, -1))[0]
        scores = bucket["vectors"] @ q  # cosine similarity
        order = np.argsort(-scores)[:top_k]
        hits: list[VectorHit] = []
        for idx in order:
            hits.append(
                VectorHit(
                    chunk_id=bucket["ids"][idx],
                    score=float(scores[idx]),
                    payload=bucket["payloads"][idx],
                )
            )
        return hits

    def delete_collection(self, collection: str) -> None:
        self._data.pop(collection, None)

    def collection_exists(self, collection: str) -> bool:
        return collection in self._data and bool(self._data[collection]["ids"])


class QdrantVectorStore:
    """Qdrant backend — Distance.COSINE to match InMemoryVectorStore."""

    def __init__(self, url: str) -> None:
        from qdrant_client import QdrantClient

        self._client = QdrantClient(url=url)

    def _ensure_collection(self, collection: str, dim: int) -> None:
        from qdrant_client.http import models as qm

        existing = {c.name for c in self._client.get_collections().collections}
        if collection in existing:
            return
        self._client.create_collection(
            collection_name=collection,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )

    def upsert(
        self,
        collection: str,
        ids: list[str],
        vectors: np.ndarray,
        payloads: list[dict],
    ) -> None:
        from qdrant_client.http import models as qm

        vectors = np.asarray(vectors, dtype=np.float32)
        if len(ids) == 0:
            return
        self._ensure_collection(collection, vectors.shape[1])
        points = [
            qm.PointStruct(
                id=_point_id(cid),
                vector=vectors[i].tolist(),
                payload={**payloads[i], "chunk_id": ids[i]},
            )
            for i, cid in enumerate(ids)
        ]
        self._client.upsert(collection_name=collection, points=points)

    def search(self, collection: str, query_vector: np.ndarray, top_k: int) -> list[VectorHit]:
        if not self.collection_exists(collection) or top_k <= 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32).reshape(-1).tolist()
        # qdrant-client >=1.12 uses query_points; keep search for compatibility
        try:
            results = self._client.search(
                collection_name=collection,
                query_vector=q,
                limit=top_k,
                with_payload=True,
            )
        except AttributeError:
            response = self._client.query_points(
                collection_name=collection,
                query=q,
                limit=top_k,
                with_payload=True,
            )
            results = response.points

        hits: list[VectorHit] = []
        for point in results:
            payload = dict(point.payload or {})
            chunk_id = str(payload.get("chunk_id") or point.id)
            hits.append(
                VectorHit(
                    chunk_id=chunk_id,
                    score=float(point.score),
                    payload=payload,
                )
            )
        return hits

    def delete_collection(self, collection: str) -> None:
        if self.collection_exists(collection):
            self._client.delete_collection(collection)

    def collection_exists(self, collection: str) -> bool:
        names = {c.name for c in self._client.get_collections().collections}
        return collection in names


def create_vector_store(backend: str, qdrant_url: str) -> VectorStore:
    if backend == "inmemory":
        return InMemoryVectorStore()
    if backend == "qdrant":
        return QdrantVectorStore(qdrant_url)
    raise ValueError(f"Unknown vector backend: {backend}")


def chunk_payload(chunk: Chunk, repo_id: str) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "file_path": chunk.file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "chunk_type": chunk.kind,
        "symbol_name": chunk.symbol_name,
        "language": chunk.language,
        "content_hash": chunk.content_hash,
        "content": chunk.content,
        "repo_id": repo_id,
    }


def _point_id(chunk_id: str) -> str:
    # Qdrant accepts UUID or unsigned int; use deterministic UUID5-ish hex via hash
    import hashlib
    import uuid

    digest = hashlib.md5(chunk_id.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest))
