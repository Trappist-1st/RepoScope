from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from app.config import settings


class EmbeddingConfig(BaseModel):
    provider: Literal["sentence-transformers", "hash"] = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"


class RerankConfig(BaseModel):
    enabled: bool = True
    provider: Literal["cross-encoder", "identity"] = "cross-encoder"
    model: str = "BAAI/bge-reranker-base"


class FusionConfig(BaseModel):
    default: Literal["rrf", "weighted"] = "rrf"
    rrf_k: int = 60
    weight_vector: float = 0.5
    weight_bm25: float = 0.5


class RetrievalDefaults(BaseModel):
    top_k_vector: int = 20
    top_k_bm25: int = 20
    rerank_top_n: int = 8
    final_top_n: int = 5
    graph_expand: bool = True
    graph_expand_limit: int = 4


class RetrievalConfig(BaseModel):
    vector_backend: Literal["inmemory", "qdrant"] = "inmemory"
    qdrant_url: str = "http://localhost:6333"
    distance: Literal["cosine"] = "cosine"
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    defaults: RetrievalDefaults = Field(default_factory=RetrievalDefaults)

    @property
    def backend_label(self) -> str:
        """For eval reports — which path produced the numbers."""
        return f"{self.vector_backend}/{self.distance}"


def _default_config_path() -> Path:
    if settings.retrieval_config_path is not None:
        return Path(settings.retrieval_config_path)
    return Path(__file__).resolve().parents[2] / "config" / "retrieval.yaml"


def load_retrieval_config(path: Path | None = None) -> RetrievalConfig:
    cfg_path = path or _default_config_path()
    data: dict[str, Any] = {}
    if cfg_path.exists():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Invalid retrieval config: {cfg_path}")
        data = loaded

    # Env / settings overrides (phase-6 friendly switches)
    if settings.vector_backend:
        data["vector_backend"] = settings.vector_backend
    if settings.qdrant_url:
        data["qdrant_url"] = settings.qdrant_url
    if settings.rerank_enabled is not None:
        data.setdefault("rerank", {})
        if isinstance(data["rerank"], dict):
            data["rerank"]["enabled"] = settings.rerank_enabled

    return RetrievalConfig.model_validate(data)
