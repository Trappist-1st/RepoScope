from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IndexingStatus = Literal["cached", "incremental", "full_reindex"]
EvidenceTier = Literal["direct", "expanded", "mixed", "none"]
Confidence = Literal["high", "medium", "low"]


class CitationOut(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    text: str

    @classmethod
    def from_parts(cls, file_path: str, start_line: int, end_line: int) -> "CitationOut":
        return cls(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            text=f"{file_path}:{start_line}-{end_line}",
        )


class Evidence(BaseModel):
    citation: CitationOut
    symbol_name: str | None = None
    snippet: str = ""
    evidence_tier: EvidenceTier = "direct"
    expansion_reason: str | None = None
    confidence: Confidence = "high"


class MCPMeta(BaseModel):
    repo_id: str
    repo_url: str
    commit_hash: str | None = None
    run_id: str | None = None
    took_ms: int = 0
    indexing_status: IndexingStatus = "cached"
    warnings: list[str] = Field(default_factory=list)
    audit_backend: str = "in_memory"
    graph_update_mode: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    sync_took_ms: int | None = None


class ModuleSummary(BaseModel):
    name: str
    role: str
    evidence: list[Evidence] = Field(default_factory=list)


class KeyFlow(BaseModel):
    description: str
    evidence: list[Evidence] = Field(default_factory=list)


class FindingOut(BaseModel):
    claim: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: Confidence = "high"


class RepoSummaryResult(BaseModel):
    meta: MCPMeta
    summary: dict[str, Any]
    report_markdown: str = ""
    review_passed: bool | None = None
    low_confidence: bool = False


class DependencyEdgeOut(BaseModel):
    symbol_ref: str | None = None
    source: str | None = None
    target: str | None = None
    edge_type: str
    same_file: bool | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class DependenciesResult(BaseModel):
    meta: MCPMeta
    query: dict[str, Any]
    callers: list[DependencyEdgeOut] = Field(default_factory=list)
    callees: list[DependencyEdgeOut] = Field(default_factory=list)
    file_imports: list[DependencyEdgeOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RefactorSuggestion(BaseModel):
    title: str
    rationale: str
    severity: Literal["high", "medium", "low"] = "medium"
    category: str = "other"
    evidence: list[Evidence] = Field(default_factory=list)
    related_symbols: list[str] = Field(default_factory=list)
    confidence: Confidence = "high"


class RefactorResult(BaseModel):
    meta: MCPMeta
    file_path: str
    suggestions: list[RefactorSuggestion] = Field(default_factory=list)
    report_markdown: str = ""
    review_passed: bool | None = None
    low_confidence: bool = False


class TraceFlowResult(BaseModel):
    """MCP/API payload for Flow Trace (Iteration 2)."""

    meta: MCPMeta
    query: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    report_markdown: str = ""
    low_confidence: bool = False


class ArchitectureResult(BaseModel):
    """MCP/API payload for Architecture Intelligence (Iteration 3)."""

    meta: MCPMeta
    report: dict[str, Any] = Field(default_factory=dict)
    report_markdown: str = ""
    primary_pattern: str = "unknown"
    finding_count: int = 0
    low_confidence: bool = False


class SearchHitOut(BaseModel):
    citation: CitationOut
    symbol_name: str | None = None
    kind: str | None = None
    language: str | None = None
    score: float = 0.0
    source: str = "hybrid"
    snippet: str = ""


class SearchCodeResult(BaseModel):
    """MCP/API payload for keyword/semantic code search (hybrid BM25 + vector)."""

    meta: MCPMeta
    query: str = ""
    hits: list[SearchHitOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DefinitionOut(BaseModel):
    """One entry in a file outline (used when view_source has no symbol/line target)."""

    name: str
    kind: str
    start_line: int
    end_line: int
    parent_name: str | None = None


class ViewSourceResult(BaseModel):
    """MCP/API payload for Granular Code View: read a file, symbol, or line range."""

    meta: MCPMeta
    file_path: str
    symbol_name: str | None = None
    citation: CitationOut | None = None
    content: str = ""
    outline: list[DefinitionOut] = Field(default_factory=list)
    truncated: bool = False
    # Continuation helpers when truncated (whole-file or oversized line range).
    total_lines: int | None = None
    next_start_line: int | None = None
    notes: list[str] = Field(default_factory=list)


class BootstrapModuleOut(BaseModel):
    id: str
    name: str
    module_type: str
    responsibility: str
    boundary_confidence: str
    file_count: int
    path_roots: list[str] = Field(default_factory=list)
    coupling: float = 0.0


class BootstrapCoreFileOut(BaseModel):
    file_path: str
    content: str
    truncated: bool
    reason: str


class InitialContextResult(BaseModel):
    """MCP/API payload for the four-part repository launchpad context.

    README + repository profile + top-coupling module map + a handful of core
    source excerpts -- meant to be the first thing an agent reads before
    drilling in with search_code / view_source / query_dependencies.
    """

    meta: MCPMeta
    readme_path: str | None = None
    readme_excerpt: str = ""
    readme_truncated: bool = False
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    build_systems: list[str] = Field(default_factory=list)
    infra: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    core_modules: list[BootstrapModuleOut] = Field(default_factory=list)
    core_files: list[BootstrapCoreFileOut] = Field(default_factory=list)
    remaining_modules: list[BootstrapModuleOut] = Field(default_factory=list)


class ExploreSeedOut(BaseModel):
    symbol_ref: str
    score: float = 0.0
    reason: str = "search"
    citation: CitationOut | None = None
    snippet: str = ""


class BlastRadiusHit(BaseModel):
    symbol_ref: str
    relation: str  # caller | callee | extends | implements | child
    hops: int = 1
    evidence: list[Evidence] = Field(default_factory=list)


class CallPathStepOut(BaseModel):
    order: int
    symbol_ref: str
    role: str | None = None
    citation: CitationOut | None = None
    note: str | None = None


class CallPathOut(BaseModel):
    steps: list[CallPathStepOut] = Field(default_factory=list)
    score: float = 0.0
    confidence: Confidence = "medium"
    source: str = "graph"  # graph | flow_tracer


class ContextExploreResult(BaseModel):
    """Primary surgical context pack for coding agents (CodeGraph-style explore)."""

    meta: MCPMeta
    query: str
    seeds: list[ExploreSeedOut] = Field(default_factory=list)
    must_read: list[ExploreSeedOut] = Field(default_factory=list)
    call_paths: list[CallPathOut] = Field(default_factory=list)
    blast_radius: list[BlastRadiusHit] = Field(default_factory=list)
    report_markdown: str = ""
    notes: list[str] = Field(default_factory=list)
    low_confidence: bool = False


class ImpactHitOut(BaseModel):
    symbol_ref: str
    relation: str
    hops: int
    direction: Literal["affected", "depends_on"]
    evidence: list[Evidence] = Field(default_factory=list)


class ImpactResult(BaseModel):
    """Dedicated impact / blast-radius analysis for a symbol."""

    meta: MCPMeta
    query: dict[str, Any] = Field(default_factory=dict)
    seeds: list[str] = Field(default_factory=list)
    affected: list[ImpactHitOut] = Field(default_factory=list)
    depends_on: list[ImpactHitOut] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    depends_on_files: list[str] = Field(default_factory=list)
    report_markdown: str = ""
    notes: list[str] = Field(default_factory=list)
    low_confidence: bool = False
