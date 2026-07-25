from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.sse import format_sse, iter_analyze_events

router = APIRouter()


class AnalyzeStreamRequest(BaseModel):
    question: str
    repo_source: str
    intent_hint: Literal["summary", "interview", "refactor"] | None = None


@router.get("/health")
def health(request: Request) -> dict:
    facade = request.app.state.facade
    return {
        "ok": True,
        "audit_backend": facade.audit_store.backend,
        "run_state_cache": facade.state_cache.backend,
        "warnings": facade._audit_warnings(),
    }


@router.post("/analyze/stream")
def analyze_stream(body: AnalyzeStreamRequest, request: Request) -> StreamingResponse:
    facade = request.app.state.facade

    def gen():
        for event in iter_analyze_events(
            facade,
            question=body.question,
            repo_source=body.repo_source,
            intent_hint=body.intent_hint,
        ):
            yield format_sse(event)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/analyze/{run_id}")
def analyze_status(run_id: str, request: Request) -> dict:
    facade = request.app.state.facade
    live = facade.state_cache.get(run_id)
    record = facade.audit_store.get(run_id)
    if live is None and record is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    return {
        "run_id": run_id,
        "live": live,
        "audit": record.model_dump() if record else None,
        "warnings": facade._audit_warnings(),
    }
