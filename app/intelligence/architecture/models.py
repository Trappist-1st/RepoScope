"""Architecture Intelligence models (Iteration 3).

Structured, evidence-backed architecture understanding over KnowledgeGraph.
Independent of LangGraph / FlowTracer / Chat / UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.intelligence.models import Confidence

ArchConfidence = Confidence


class ModuleType(str, Enum):
    """What kind of boundary a discovered cluster likely represents."""

    FEATURE = "feature"  # business/domain-ish (auth, order)
    LAYER = "layer"  # technical layer (controller, service, repository)
    TECHNICAL = "technical"  # utils/config/common infra glue
    UNKNOWN = "unknown"


class ArchitectureFindingCategory(str, Enum):
    MODULE_BOUNDARY = "module_boundary"
    PATTERN = "pattern"
    PROFILE = "profile"
    COUPLING = "coupling"
    LAYER_VIOLATION = "layer_violation"
    CIRCULAR_DEPENDENCY = "circular_dependency"
    RISK = "risk"


class ArchitecturePatternKind(str, Enum):
    LAYERED = "layered"
    MVC = "mvc"
    HEXAGONAL = "hexagonal"
    EVENT_DRIVEN = "event_driven"
    UNKNOWN = "unknown"


class InfraKind(str, Enum):
    DATABASE = "database"
    CACHE = "cache"
    MQ = "mq"
    SEARCH = "search"
    CLOUD = "cloud"
    OTHER = "other"


class EvidenceRefKind(str, Enum):
    FILE = "file"
    SYMBOL = "symbol"
    EDGE = "edge"
    MODULE = "module"
    FLOW_STEP = "flow_step"  # optional enhancement only


class EvidenceRef(BaseModel):
    """Pointer to concrete repo evidence. Findings must not be empty of these."""

    kind: EvidenceRefKind
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    node_id: str | None = None
    edge_id: str | None = None
    module_id: str | None = None
    note: str | None = None


class ArchitectureModule(BaseModel):
    """
    Discovered code cluster / architectural region.

    v1 clusters are path-based; module_type + boundary_confidence distinguish
    feature modules from technical layers (avoid calling every package a domain).
    """

    id: str
    name: str
    path_roots: list[str] = Field(default_factory=list)
    module_type: ModuleType = ModuleType.UNKNOWN
    boundary_confidence: ArchConfidence = "low"
    responsibility: str = ""
    role_mix: dict[str, int] = Field(default_factory=dict)
    file_paths: list[str] = Field(default_factory=list)
    symbol_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    cohesion: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ModuleMap(BaseModel):
    modules: list[ArchitectureModule] = Field(default_factory=list)
    unresolved_files: list[str] = Field(default_factory=list)
    method: str = "path_cluster+role+cohesion"


class FrameworkHit(BaseModel):
    name: str
    confidence: ArchConfidence = "medium"
    evidence: list[EvidenceRef] = Field(default_factory=list)


class InfraHit(BaseModel):
    kind: InfraKind
    name: str
    confidence: ArchConfidence = "medium"
    evidence: list[EvidenceRef] = Field(default_factory=list)


class RepositoryProfile(BaseModel):
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[FrameworkHit] = Field(default_factory=list)
    build_systems: list[str] = Field(default_factory=list)
    infra: list[InfraHit] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)  # qualified names / paths
    file_count: int = 0
    symbol_count: int = 0
    module_count: int = 0
    evidence: list[EvidenceRef] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class PatternMatch(BaseModel):
    pattern: ArchitecturePatternKind
    score: float = 0.0
    confidence: ArchConfidence = "low"
    signals: list[str] = Field(default_factory=list)
    counter_signals: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ArchitectureMetrics(BaseModel):
    module_count: int = 0
    cross_module_edges: int = 0
    dependency_density: float = 0.0
    cycle_count: int = 0
    max_fan_in: float = 0.0
    max_fan_out: float = 0.0
    per_module: dict[str, dict[str, float]] = Field(default_factory=dict)
    # per_module[id] -> {fan_in, fan_out, coupling}


class ArchitectureFinding(BaseModel):
    """Evidence-backed architecture insight. evidence must be non-empty."""

    finding_id: str
    category: ArchitectureFindingCategory
    title: str
    detail: str = ""
    evidence: list[EvidenceRef] = Field(min_length=1)
    related_symbols: list[str] = Field(default_factory=list)
    related_modules: list[str] = Field(default_factory=list)
    confidence: ArchConfidence = "medium"
    score: float | None = None
    reason: str | None = None
    inference_reason: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_evidence(self) -> "ArchitectureFinding":
        if not self.evidence:
            raise ValueError("ArchitectureFinding.evidence must be non-empty")
        if self.confidence == "high" and len(self.evidence) < 1:
            raise ValueError("high confidence requires evidence")
        return self


class ArchitectureReportMeta(BaseModel):
    """Evolution-ready identity (compare reports across commits later)."""

    repo_id: str
    commit_hash: str | None = None
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    took_ms: int = 0
    method: str = "heuristic_v1"
    include_flows: bool = False
    kg_schema_version: str = "1.0"


class ArchitectureReport(BaseModel):
    schema_version: str = "1.0"
    meta: ArchitectureReportMeta
    profile: RepositoryProfile = Field(default_factory=RepositoryProfile)
    modules: ModuleMap = Field(default_factory=ModuleMap)
    patterns: list[PatternMatch] = Field(default_factory=list)
    primary_pattern: ArchitecturePatternKind = ArchitecturePatternKind.UNKNOWN
    findings: list[ArchitectureFinding] = Field(default_factory=list)
    metrics: ArchitectureMetrics = Field(default_factory=ArchitectureMetrics)
    warnings: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
