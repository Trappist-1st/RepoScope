from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes_analyze import router as analyze_router
from app.api.routes_architecture import router as architecture_router
from app.api.routes_trace import router as trace_router
from app.mcp.service import RepoScopeFacade


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Analyzer from REPOSCOPE_ANALYZER_PROVIDER (.env): stub | llm
    app.state.facade = RepoScopeFacade(use_hash_embedder=True)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="RepoScope", version="0.1.0", lifespan=lifespan)
    app.include_router(analyze_router)
    app.include_router(trace_router)
    app.include_router(architecture_router)
    return app


app = create_app()
