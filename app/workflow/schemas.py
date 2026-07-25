from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["summary", "interview", "refactor"]
WorkflowStatus = Literal["ok", "partial", "failed"]


class PlanStep(BaseModel):
    """One executable analysis step produced by the planner."""

    step_id: int = Field(ge=1, description="1-based step index")
    action: str = Field(description="Human-readable analysis action")
    search_query: str = Field(description="Concrete retrieval query for this step")
    reasoning: str = ""
    keywords: list[str] = Field(default_factory=list)


class AnalysisPlan(BaseModel):
    """Structured plan used to drive retrieve + analyze."""

    intent: Intent
    steps: list[PlanStep] = Field(default_factory=list)
    overall_goal: str = ""
    expected_outputs: list[str] = Field(default_factory=list)
    source: Literal["llm", "template"] = "template"

    def action_list(self) -> list[str]:
        return [s.action for s in self.steps]

    def retrieval_query_parts(self, *, max_parts: int = 8, max_len: int = 64) -> list[str]:
        """Compact, BM25-friendly terms (avoid mega concatenated queries)."""
        parts: list[str] = []
        for step in self.steps:
            q = _clip_query(step.search_query, max_len)
            if q:
                parts.append(q)
            for kw in step.keywords[:3]:
                k = _clip_query(kw, 40)
                if k:
                    parts.append(k)
        return list(dict.fromkeys(parts))[:max_parts]


def _clip_query(text: str, max_len: int) -> str:
    q = " ".join((text or "").split())
    if len(q) <= max_len:
        return q
    return q[:max_len].rstrip()


class Finding(BaseModel):
    claim: str
    citations: list[str] = Field(default_factory=list)  # "path:start-end"
    symbols: list[str] = Field(default_factory=list)
    plan_step_idx: int | None = None
    confidence: Literal["high", "medium", "low"] = "high"
    # Evidence provenance for explainability (propagated to finalize)
    evidence_tier: Literal["direct", "expanded", "mixed", "none"] = "none"
    expansion_reasons: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    type: str
    detail: str
    severity: Literal["high", "low"] = "high"
    finding_idx: int | None = None
    citation: str | None = None


class WorkflowInput(BaseModel):
    question: str
    repo_source: str
    intent_hint: Intent | None = None
    token_budget: int = 4000
    max_review_retries: int = 2


class WorkflowResult(BaseModel):
    status: WorkflowStatus
    intent: Intent | None = None
    repo_id: str | None = None
    report_markdown: str = ""
    report_json: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    review_issues: list[ReviewIssue] = Field(default_factory=list)
    low_confidence: bool = False
    retry_count: int = 0
    timeouts: dict[str, bool] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
