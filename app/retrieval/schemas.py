from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.schemas import Chunk


class Citation(BaseModel):
    file_path: str
    start_line: int
    end_line: int

    def format(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"

    @classmethod
    def from_chunk(cls, chunk: Chunk) -> "Citation":
        return cls(
            file_path=chunk.file_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
        )


class RetrievalHit(BaseModel):
    chunk_id: str
    content: str
    citation: Citation
    score: float
    source: str  # vector | bm25 | hybrid | rerank | graph_expand
    symbol_name: str | None = None
    kind: str | None = None
    language: str | None = None
    scores: dict[str, float] = Field(default_factory=dict)
    # Only for expanded_hits — how this evidence was pulled in
    expansion_reason: str | None = None


class IndexRequest(BaseModel):
    repo_id: str
    chunks: list[Chunk] | None = None
    force_reindex: bool = False


class IndexResult(BaseModel):
    repo_id: str
    indexed_count: int
    collection_name: str
    bm25_docs: int
    backend: str


class RetrieveRequest(BaseModel):
    repo_id: str
    query: str
    top_k_vector: int | None = None
    top_k_bm25: int | None = None
    fusion: Literal["rrf", "weighted"] | None = None
    weight_vector: float | None = None
    weight_bm25: float | None = None
    rrf_k: int | None = None
    rerank_top_n: int | None = None
    final_top_n: int | None = None
    graph_expand: bool | None = None
    graph_expand_limit: int | None = None
    # Phase-6 experiment switches (override config for a single call)
    mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    skip_rerank: bool | None = None


class RetrieveResponse(BaseModel):
    repo_id: str
    query: str
    hits: list[RetrievalHit]
    expanded_hits: list[RetrievalHit] = Field(default_factory=list)
    expansion_depth: int = 1
    diagnostics: dict[str, Any] = Field(default_factory=dict)
