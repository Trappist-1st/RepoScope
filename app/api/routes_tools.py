"""HTTP wrappers for MCP tools not covered by analyze/trace/architecture routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class RepoBody(BaseModel):
    repo_source: str
    force_reindex: bool = False


class SummaryRequest(RepoBody):
    question: str | None = None


class DependenciesRequest(RepoBody):
    symbol_name: str
    direction: Literal["both", "callers", "callees", "imports"] = "both"
    limit: int = Field(default=20, ge=1, le=50)


class ImpactRequest(RepoBody):
    symbol_name: str
    depth: int = Field(default=2, ge=1, le=8)
    direction: Literal["affected", "depends_on", "both"] = "both"
    limit: int = Field(default=50, ge=1, le=200)


class RefactorRequest(RepoBody):
    file_path: str
    focus: str | None = None
    max_suggestions: int = Field(default=5, ge=1, le=20)


class SearchRequest(RepoBody):
    query: str
    top_k: int = Field(default=10, ge=1, le=50)
    graph_expand: bool = False


class ViewSourceRequest(RepoBody):
    file_path: str
    symbol_name: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class InitialContextRequest(RepoBody):
    top_k_modules: int = Field(default=8, ge=1, le=30)
    top_k_core_files: int = Field(default=5, ge=0, le=15)


class ExploreRequest(RepoBody):
    query: str
    top_k: int = Field(default=8, ge=1, le=20)
    blast_depth: int = Field(default=2, ge=1, le=4)
    include_flow: bool | None = None


@router.post("/summary")
def get_repo_summary(body: SummaryRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.get_repo_summary(
        repo_url=body.repo_source,
        question=body.question,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/dependencies")
def query_dependencies(body: DependenciesRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.query_dependencies(
        repo_url=body.repo_source,
        symbol_name=body.symbol_name,
        direction=body.direction,
        limit=body.limit,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/impact")
def analyze_impact(body: ImpactRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.analyze_impact(
        repo_url=body.repo_source,
        symbol_name=body.symbol_name,
        depth=body.depth,
        direction=body.direction,
        limit=body.limit,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/refactor")
def suggest_refactor(body: RefactorRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.suggest_refactor(
        repo_url=body.repo_source,
        file_path=body.file_path,
        focus=body.focus,
        max_suggestions=body.max_suggestions,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/search")
def search_code(body: SearchRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.search_code(
        repo_url=body.repo_source,
        query=body.query,
        top_k=body.top_k,
        graph_expand=body.graph_expand,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/source")
def view_source(body: ViewSourceRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.view_source(
        repo_url=body.repo_source,
        file_path=body.file_path,
        symbol_name=body.symbol_name,
        start_line=body.start_line,
        end_line=body.end_line,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/initial-context")
def get_initial_context(body: InitialContextRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.get_initial_context(
        repo_url=body.repo_source,
        top_k_modules=body.top_k_modules,
        top_k_core_files=body.top_k_core_files,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()


@router.post("/explore")
def context_explore(body: ExploreRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.context_explore(
        repo_url=body.repo_source,
        query=body.query,
        top_k=body.top_k,
        blast_depth=body.blast_depth,
        include_flow=body.include_flow,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()
