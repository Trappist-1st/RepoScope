from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.retrieval.config import RerankConfig
from app.retrieval.schemas import RetrievalHit


@runtime_checkable
class Reranker(Protocol):
    def rerank(self, query: str, hits: list[RetrievalHit], top_n: int) -> list[RetrievalHit]: ...


class IdentityReranker:
    """Pass-through — preserves fusion order (for skip_rerank / tests)."""

    def rerank(self, query: str, hits: list[RetrievalHit], top_n: int) -> list[RetrievalHit]:
        out = list(hits[:top_n])
        for h in out:
            h.source = "rerank" if h.source != "graph_expand" else h.source
            h.scores = {**h.scores, "rerank": h.score}
        return out


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for rerank.provider=cross-encoder. "
                "Install with: pip install -e \".[retrieval]\" "
                "or set rerank.enabled=false / provider=identity"
            ) from exc
        except OSError as exc:
            # Common on Windows: torch c10.dll WinError 1114 with newer builds.
            raise ImportError(
                "Failed to load torch / sentence-transformers (often a Windows DLL issue). "
                "Try: pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu "
                "or set rerank.enabled=false / provider=identity"
            ) from exc

        self._model = CrossEncoder(model_name)

    def rerank(self, query: str, hits: list[RetrievalHit], top_n: int) -> list[RetrievalHit]:
        if not hits or top_n <= 0:
            return []
        pairs = [(query, h.content) for h in hits]
        scores = self._model.predict(pairs)
        ranked = sorted(
            zip(hits, scores, strict=True),
            key=lambda x: float(x[1]),
            reverse=True,
        )
        out: list[RetrievalHit] = []
        for hit, score in ranked[:top_n]:
            updated = hit.model_copy(deep=True)
            updated.score = float(score)
            updated.source = "rerank"
            updated.scores = {**updated.scores, "rerank": float(score)}
            out.append(updated)
        return out


def create_reranker(config: RerankConfig, force_skip: bool = False) -> Reranker:
    if force_skip or not config.enabled or config.provider == "identity":
        return IdentityReranker()
    if config.provider == "cross-encoder":
        return CrossEncoderReranker(config.model)
    raise ValueError(f"Unknown rerank provider: {config.provider}")
