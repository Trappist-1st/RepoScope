from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

import numpy as np

from app.retrieval.config import EmbeddingConfig


def _stable_hash(token: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
    )


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return matrix / norms


@runtime_checkable
class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized vectors, shape (n, dim). Cosine == dot product."""
        ...


class HashEmbedder:
    """Deterministic bag-of-tokens hasher for tests / CI (no model download).

    Uses blake2b rather than the builtin ``hash``: CPython salts string hashing
    per process, which made every vector — and therefore every Recall@k in the
    benchmark harness — differ from run to run.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in _tokenize(text):
                h = _stable_hash(token)
                idx = h % self._dim
                sign = 1.0 if (h // self._dim) % 2 == 0 else -1.0
                vectors[i, idx] += sign
        return l2_normalize(vectors)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for embedding.provider=sentence-transformers. "
                "Install with: pip install -e \".[retrieval]\""
            ) from exc
        except OSError as exc:
            raise ImportError(
                "Failed to load torch / sentence-transformers (often a Windows DLL issue). "
                "Try: pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu "
                "or set embedding.provider=hash"
            ) from exc

        self._model = SentenceTransformer(model_name)
        self._dim = int(self._model.get_sentence_embedding_dimension())

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        vectors = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


def create_embedder(config: EmbeddingConfig) -> Embedder:
    if config.provider == "hash":
        return HashEmbedder()
    if config.provider == "sentence-transformers":
        return SentenceTransformerEmbedder(config.model)
    raise ValueError(f"Unknown embedding provider: {config.provider}")


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", text.lower())
