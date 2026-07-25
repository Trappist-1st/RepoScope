from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.db.postgres import (
    FilesRepository,
    InMemoryFilesRepository,
    InMemoryReposRepository,
    ReposRepository,
)
from app.ingestion.incremental import IngestionPipeline
from app.retrieval.config import RetrievalConfig, load_retrieval_config
from app.retrieval.embedder import HashEmbedder
from app.retrieval.rerank import IdentityReranker
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import InMemoryVectorStore
from app.workflow.analyzers import Analyzer, StubAnalyzer
from app.workflow.nodes.analyze import make_analyze_node
from app.workflow.nodes.finalize import finalize_node
from app.workflow.nodes.repo_parse import make_repo_parse_node
from app.workflow.nodes.retrieve import make_retrieve_node
from app.workflow.nodes.review import review_node
from app.workflow.nodes.route import route_node
from app.workflow.schemas import WorkflowInput, WorkflowResult
from app.workflow.state import WorkflowState


def _after_repo_parse(state: WorkflowState) -> Literal["planner", "finalize"]:
    if state.get("timeouts", {}).get("repo_parse") or not state.get("indexed"):
        return "finalize"
    return "planner"


def _after_analyze(state: WorkflowState) -> Literal["review", "finalize"]:
    if state.get("timeouts", {}).get("analyze"):
        return "finalize"
    return "review"


def _after_review(state: WorkflowState) -> Literal["retrieve", "finalize"]:
    if state.get("review_should_retry"):
        return "retrieve"
    return "finalize"


def build_workflow_graph(
    *,
    ingestion: IngestionPipeline,
    retrieval: RetrievalService,
    analyzer: Analyzer | None = None,
):
    from app.workflow.nodes.planner import planner_node

    graph = StateGraph(WorkflowState)
    graph.add_node("route", route_node)
    graph.add_node("repo_parse", make_repo_parse_node(ingestion=ingestion, retrieval=retrieval))
    graph.add_node("planner", planner_node)
    graph.add_node("retrieve", make_retrieve_node(retrieval))
    graph.add_node("analyze", make_analyze_node(analyzer))
    graph.add_node("review", review_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "route")
    graph.add_edge("route", "repo_parse")
    graph.add_conditional_edges(
        "repo_parse",
        _after_repo_parse,
        {"planner": "planner", "finalize": "finalize"},
    )
    graph.add_edge("planner", "retrieve")
    graph.add_edge("retrieve", "analyze")
    graph.add_conditional_edges(
        "analyze",
        _after_analyze,
        {"review": "review", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "review",
        _after_review,
        {"retrieve": "retrieve", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()


class WorkflowRunner:
    def __init__(
        self,
        *,
        ingestion: IngestionPipeline,
        retrieval: RetrievalService,
        analyzer: Analyzer | None = None,
    ) -> None:
        self.graph = build_workflow_graph(
            ingestion=ingestion,
            retrieval=retrieval,
            analyzer=analyzer or StubAnalyzer(),
        )

    def run(self, inp: WorkflowInput) -> WorkflowResult:
        initial: WorkflowState = {
            "question": inp.question,
            "repo_source": inp.repo_source,
            "intent_hint": inp.intent_hint,
            "token_budget": inp.token_budget,
            "max_review_retries": inp.max_review_retries,
            "retry_count": 0,
            "retry_hints": [],
            "timeouts": {},
            "errors": [],
            "hits": [],
            "expanded_hits": [],
            "findings": [],
            "review_issues": [],
            "plan": [],
            "history_rounds": 0,
            "analysis_history": [],
            "low_confidence": False,
            "review_passed": False,
            "review_should_retry": False,
            "indexed": False,
        }
        final: dict[str, Any] = self.graph.invoke(initial)
        return WorkflowResult(
            status=final.get("status") or "failed",  # type: ignore[arg-type]
            intent=final.get("intent"),
            repo_id=final.get("repo_id"),
            report_markdown=final.get("report_markdown") or "",
            report_json=final.get("report_json") or {},
            findings=final.get("findings") or [],
            review_issues=final.get("review_issues") or [],
            low_confidence=bool(final.get("low_confidence")),
            retry_count=int(final.get("retry_count") or 0),
            timeouts=final.get("timeouts") or {},
            errors=final.get("errors") or [],
        )


def create_default_runner(
    *,
    workspace_root: Path | None = None,
    artifact_dir: Path | None = None,
    files_repo: FilesRepository | None = None,
    repos_repo: ReposRepository | None = None,
    retrieval_config: RetrievalConfig | None = None,
    analyzer: Analyzer | None = None,
    use_hash_embedder: bool = True,
) -> WorkflowRunner:
    """Convenience factory for local/dev (InMemory DB + hash embedder by default)."""
    from app.config import settings

    workspace_root = workspace_root or settings.workspace_root
    artifact_dir = artifact_dir or settings.artifact_dir
    files_repo = files_repo or InMemoryFilesRepository()
    repos_repo = repos_repo or InMemoryReposRepository()

    ingestion = IngestionPipeline(
        workspace_root=workspace_root,
        artifact_dir=artifact_dir,
        files_repo=files_repo,
        repos_repo=repos_repo,
    )

    cfg = retrieval_config or load_retrieval_config()
    if use_hash_embedder:
        retrieval = RetrievalService(
            config=cfg,
            artifact_dir=artifact_dir,
            embedder=HashEmbedder(),
            vector_store=InMemoryVectorStore(),
            reranker=IdentityReranker(),
        )
    else:
        retrieval = RetrievalService(config=cfg, artifact_dir=artifact_dir)

    from app.workflow.resolve_analyzer import resolve_analyzer

    return WorkflowRunner(
        ingestion=ingestion,
        retrieval=retrieval,
        analyzer=resolve_analyzer(analyzer),
    )
