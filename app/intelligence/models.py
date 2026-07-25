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
    """Reserved for Flow Trace; Iteration 1 adapters leave this empty."""

    file_path: str
    start_line: int
    end_line: int | None = None


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
    confidence: Confidence = "high"
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphStats(BaseModel):
    node_counts: dict[str, int] = Field(default_factory=dict)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    orphan_symbol_refs: list[str] = Field(default_factory=list)


class KnowledgeGraphSource(BaseModel):
    dependency_graph: bool = True
    definitions: bool = False
    inherit_supported: bool = False


class KnowledgeGraph(BaseModel):
    schema_version: str = "1.0"
    repo_id: str
    commit_hash: str | None = None
    nodes: list[KnowledgeNode] = Field(default_factory=list)
    edges: list[KnowledgeEdge] = Field(default_factory=list)
    stats: KnowledgeGraphStats = Field(default_factory=KnowledgeGraphStats)
    source: KnowledgeGraphSource = Field(default_factory=KnowledgeGraphSource)
