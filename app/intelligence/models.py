"""Code Intelligence Graph v1 models (semantic layer over DependencyGraph)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class NodeKind(str, Enum):
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class EdgeType(str, Enum):
    IMPORT = "import"
    CALL = "call"
    INHERIT = "inherit"


Confidence = Literal["high", "medium", "low"]


class EvidenceSpan(BaseModel):
    """Source location backing an edge or a flow step."""

    file_path: str
    start_line: int
    end_line: int | None = None

    @property
    def citation(self) -> str:
        if self.end_line is not None and self.end_line != self.start_line:
            return f"{self.file_path}:{self.start_line}-{self.end_line}"
        return f"{self.file_path}:{self.start_line}"


class KnowledgeNode(BaseModel):
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    parent_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class KnowledgeEdge(BaseModel):
    id: str
    source_id: str
    target_id: str
    edge_type: EdgeType
    # Bucketed label consumed by FlowTracer beam search; keep the literal type.
    confidence: Confidence = "high"
    # Raw cascade score. None on edges produced by the legacy resolver.
    confidence_score: float | None = None
    resolution_strategy: str | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


def bucket_confidence(score: float) -> Confidence:
    if score >= 0.85:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


class KnowledgeGraphStats(BaseModel):
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    orphan_symbol_refs: list[str] = Field(default_factory=list)


class KnowledgeGraphSource(BaseModel):
    dependency_graph: bool = True
    definitions: bool = False
    inherit_supported: bool = False
    # Self-describing artifact: lets the pipeline detect a mode switch and
    # force a rebuild instead of silently mixing cascade and legacy edges.
    advanced: bool = False


class KnowledgeGraph(BaseModel):
    schema_version: str = "1.0"
    repo_id: str
    commit_hash: str | None = None
    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[KnowledgeEdge] = Field(default_factory=list)
    stats: KnowledgeGraphStats = Field(default_factory=KnowledgeGraphStats)
    source: KnowledgeGraphSource = Field(default_factory=KnowledgeGraphSource)
