from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.retrieval.schemas import RetrievalHit
from app.workflow.schemas import AnalysisPlan, Finding, Intent, ReviewIssue


def _merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(a or {})
    out.update(b or {})
    return out


class WorkflowState(TypedDict, total=False):
    # input
    question: str
    repo_source: str
    intent_hint: Intent | None
    token_budget: int
    max_review_retries: int

    # route
    intent: Intent
    route_notes: str

    # repo
    repo_id: str
    commit_hash: str
    local_path: str
    indexed: bool

    # planner
    analysis_plan: AnalysisPlan
    plan_source: str  # llm | template

    # retrieve
    hits: list[RetrievalHit]
    expanded_hits: list[RetrievalHit]
    retrieve_query: str
    # Explicit retry signals — NOT just a boolean retry flag
    retry_hints: list[str]
    primary_citations: list[str]
    expanded_citations: list[str]

    # analyze
    plan: list[str]
    findings: list[Finding]
    analysis_markdown: str
    tokens_used: int
    history_rounds: int
    # Prior analyze-round summaries for HistoryWindow (per invoke; not shared across runs)
    analysis_history: list[str]
    dependency_graph: Any  # DependencyGraph | None — avoided circular import in TypedDict

    # review
    review_passed: bool
    review_issues: list[ReviewIssue]
    review_should_retry: bool
    low_confidence: bool
    retry_count: int

    # control / output
    timeouts: Annotated[dict[str, bool], _merge_dicts]
    errors: Annotated[list[str], operator.add]
    report_markdown: str
    report_json: dict[str, Any]
    status: str
