from __future__ import annotations

import pickle
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.models.schemas import Chunk


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\s]")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


class BM25Index:
    def __init__(self, chunks: list[Chunk] | None = None) -> None:
        self._chunks: list[Chunk] = []
        self._bm25: BM25Okapi | None = None
        if chunks:
            self.build(chunks)

    def build(self, chunks: list[Chunk]) -> None:
        self._chunks = list(chunks)
        corpus = [tokenize(c.content) for c in self._chunks]
        # BM25Okapi requires non-empty corpus; handle empty
        if not corpus:
            self._bm25 = None
            return
        self._bm25 = BM25Okapi(corpus)

    @property
    def size(self) -> int:
        return len(self._chunks)

    def search(self, query: str, top_k: int = 20) -> list[tuple[Chunk, float]]:
        if self._bm25 is None or not self._chunks or top_k <= 0:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[tuple[Chunk, float]] = []
        for idx, score in ranked[:top_k]:
            if score <= 0:
                continue
            results.append((self._chunks[idx], float(score)))
        return results

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"chunks": [c.model_dump() for c in self._chunks]}
        path.write_bytes(pickle.dumps(payload))

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        payload = pickle.loads(path.read_bytes())
        chunks = [Chunk.model_validate(item) for item in payload["chunks"]]
        return cls(chunks)
