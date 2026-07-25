from pathlib import Path

from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline
from app.retrieval import IndexRequest, RetrievalService, RetrieveRequest
from app.retrieval.config import (
    EmbeddingConfig,
    RetrievalConfig,
    RerankConfig,
)
from app.retrieval.embedder import HashEmbedder
from app.retrieval.rerank import IdentityReranker
from app.retrieval.vector_store import InMemoryVectorStore
from tests.conftest import SAMPLE_REPO


def _service(tmp_path: Path) -> tuple[RetrievalService, str]:
    artifact_dir = tmp_path / "artifacts"
    pipeline = IngestionPipeline(
        workspace_root=tmp_path / "workspace",
        artifact_dir=artifact_dir,
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    ingest = pipeline.run(str(SAMPLE_REPO))

    config = RetrievalConfig(
        vector_backend="inmemory",
        embedding=EmbeddingConfig(provider="hash", model="hash"),
        rerank=RerankConfig(enabled=False, provider="identity", model="identity"),
    )
    service = RetrievalService(
        config=config,
        artifact_dir=artifact_dir,
        embedder=HashEmbedder(),
        vector_store=InMemoryVectorStore(),
        reranker=IdentityReranker(),
    )
    indexed = service.index(IndexRequest(repo_id=ingest.repo_id, force_reindex=True))
    assert indexed.indexed_count > 0
    assert indexed.backend.startswith("inmemory/cosine")
    return service, ingest.repo_id


def test_index_and_hybrid_retrieve_has_citations(tmp_path: Path):
    service, repo_id = _service(tmp_path)
    response = service.retrieve(
        RetrieveRequest(
            repo_id=repo_id,
            query="greet function hello",
            mode="hybrid",
            final_top_n=5,
            graph_expand=True,
        )
    )
    assert response.hits
    for hit in response.hits:
        assert ":" in hit.citation.format()
        assert "-" in hit.citation.format()
        assert hit.citation.start_line >= 1
        assert hit.citation.end_line >= hit.citation.start_line

    assert response.diagnostics["backend"] == "inmemory/cosine"
    assert response.diagnostics["mode"] == "hybrid"
    assert "fused" in response.diagnostics


def test_mode_switches_vector_bm25_hybrid(tmp_path: Path):
    service, repo_id = _service(tmp_path)
    for mode in ("vector", "bm25", "hybrid"):
        resp = service.retrieve(
            RetrieveRequest(repo_id=repo_id, query="Calculator multiply", mode=mode, final_top_n=3)
        )
        assert resp.diagnostics["mode"] == mode
        assert isinstance(resp.hits, list)


def test_expanded_hits_have_expansion_reason(tmp_path: Path):
    service, repo_id = _service(tmp_path)
    response = service.retrieve(
        RetrieveRequest(
            repo_id=repo_id,
            query="greet",
            mode="bm25",
            final_top_n=5,
            graph_expand=True,
            graph_expand_limit=4,
        )
    )
    assert response.expansion_depth == 1
    # May or may not expand depending on which hits ranked; if expanded, reason required
    for hit in response.expanded_hits:
        assert hit.source == "graph_expand"
        assert hit.expansion_reason
        assert hit.expansion_reason.startswith("called_by:") or hit.expansion_reason.startswith(
            "calls:"
        )


def test_cosine_inmemory_ranks_similar_text_higher(tmp_path: Path):
    from app.models.schemas import Chunk
    from app.retrieval.schemas import IndexRequest as IR

    config = RetrievalConfig(
        vector_backend="inmemory",
        embedding=EmbeddingConfig(provider="hash", model="hash"),
        rerank=RerankConfig(enabled=False, provider="identity"),
    )
    service = RetrievalService(
        config=config,
        artifact_dir=tmp_path,
        embedder=HashEmbedder(dim=64),
        vector_store=InMemoryVectorStore(),
        reranker=IdentityReranker(),
    )
    chunks = [
        Chunk(
            chunk_id="1",
            file_path="a.py",
            start_line=1,
            end_line=2,
            content="def authenticate_user(token): validate token",
            kind="function",
            symbol_name="authenticate_user",
        ),
        Chunk(
            chunk_id="2",
            file_path="b.py",
            start_line=1,
            end_line=2,
            content="def render_template(html): return html",
            kind="function",
            symbol_name="render_template",
        ),
    ]
    # Write minimal artifacts so retrieve can load chunks
    import json

    repo_id = "toy"
    (tmp_path / repo_id).mkdir(parents=True)
    (tmp_path / repo_id / "chunks.json").write_text(
        json.dumps([c.model_dump() for c in chunks]), encoding="utf-8"
    )
    service.index(IR(repo_id=repo_id, chunks=chunks, force_reindex=True))
    resp = service.retrieve(
        RetrieveRequest(repo_id=repo_id, query="authenticate token user", mode="vector", final_top_n=1)
    )
    assert resp.hits[0].chunk_id == "1"
    assert resp.hits[0].citation.format() == "a.py:1-2"


def test_retrieval_cache_survives_concurrent_index_and_retrieve(tmp_path: Path):
    import threading

    service, repo_id = _service(tmp_path)
    errors: list[BaseException] = []

    def _retrieve_loop() -> None:
        try:
            for _ in range(20):
                service.retrieve(
                    RetrieveRequest(repo_id=repo_id, query="greet", mode="hybrid", final_top_n=3)
                )
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            errors.append(exc)

    def _reindex_loop() -> None:
        try:
            for _ in range(5):
                service.index(IndexRequest(repo_id=repo_id, force_reindex=True))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_retrieve_loop),
        threading.Thread(target=_retrieve_loop),
        threading.Thread(target=_reindex_loop),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert repo_id in service._chunks
    assert repo_id in service._bm25
