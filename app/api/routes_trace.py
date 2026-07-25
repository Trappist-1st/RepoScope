"""HTTP API for Flow Trace (Iteration 2) — independent of LangGraph workflow."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class TraceRequest(BaseModel):
    question: str
    repo_source: str
    entry_hint: str | None = None
    max_depth: int = Field(default=5, ge=1, le=10)
    force_reindex: bool = False


@router.post("/trace")
def trace_flow(body: TraceRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.trace_flow(
        repo_url=body.repo_source,
        question=body.question,
        entry_hint=body.entry_hint,
        max_depth=body.max_depth,
        force_reindex=body.force_reindex,
    )
    return result.model_dump()
