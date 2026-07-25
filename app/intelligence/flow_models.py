"""Flow Trace data models (Iteration 2).

Designed for library use, MCP export, and future SSE / Explore sessions.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.intelligence.enrichers.roles import FlowRole
from app.intelligence.models import Confidence, EvidenceSpan

TraceConfidence = Confidence


class TraceQuery(BaseModel):
    question: str
    repo_id: str
    topic: str | None = None
    topic_terms: list[str] = Field(default_factory=list)
    entry_hints: list[str] = Field(default_factory=list)
    max_depth: int = 5
    max_paths: int = 3
    max_branching: int = 8
    language_prefer: list[str] | None = None
    session_id: str | None = None  # reserved for Explore


class FlowStep(BaseModel):
    order: int = Field(ge=1)
    symbol: str
    node_id: str | None = None
    qualified_name: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    role: FlowRole = FlowRole.UNKNOWN
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    confidence: TraceConfidence = "medium"
    edge_from_prev: str | None = None
    note: str | None = None
    is_synthetic: bool = False
    # Explainability (future Explore / MCP / SSE)
    reason: str | None = None
    inference_reason: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class FlowPathSummary(BaseModel):
    entry_node_id: str
    step_symbols: list[str] = Field(default_factory=list)
    score: float = 0.0
    confidence: TraceConfidence = "medium"


class FlowTraceMeta(BaseModel):
    repo_id: str
    commit_hash: str | None = None
    kg_schema_version: str = "1.0"
    took_ms: int = 0
    method: str = "beam_call_graph"


class FlowTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    query: TraceQuery
    entry: FlowStep | None = None
    steps: list[FlowStep] = Field(default_factory=list)
    alternatives: list[FlowPathSummary] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    confidence: TraceConfidence = "low"
    ranking_score: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    meta: FlowTraceMeta
