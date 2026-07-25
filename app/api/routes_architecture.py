"""HTTP API for Architecture Intelligence (Iteration 3)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class ArchitectureRequest(BaseModel):
    repo_source: str
    force_reindex: bool = False
    include_flows: bool = Field(
        default=False,
        description="Reserved; ArchitectureAnalyzer does not depend on FlowTracer in v1.",
    )


@router.post("/architecture")
def analyze_architecture(body: ArchitectureRequest, request: Request) -> dict:
    facade = request.app.state.facade
    result = facade.analyze_architecture(
        repo_url=body.repo_source,
        force_reindex=body.force_reindex,
        include_flows=body.include_flows,
    )
    return result.model_dump()
