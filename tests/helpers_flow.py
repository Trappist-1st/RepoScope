"""Shared helpers for Flow Trace fixture e2e tests."""

from __future__ import annotations

from pathlib import Path

from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline
from app.intelligence.ids import edge_id, symbol_ref_to_node_id
from app.intelligence.models import EdgeType, KnowledgeEdge, KnowledgeGraph
from app.intelligence.query import get_node

FIXTURES = Path(__file__).parent / "fixtures"
SPRING_LOGIN = FIXTURES / "flow_spring_login"
FASTAPI_LOGIN = FIXTURES / "flow_fastapi_login"


def ingest_fixture(repo_dir: Path, artifact_dir: Path) -> tuple[str, KnowledgeGraph]:
    pipe = IngestionPipeline(
        workspace_root=artifact_dir / "ws",
        artifact_dir=artifact_dir / "artifacts",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    result = pipe.run(str(repo_dir))
    kg = pipe.load_knowledge_graph(result.repo_id)
    return result.repo_id, kg


def ensure_call(
    graph: KnowledgeGraph,
    caller_qn: str,
    callee_qn: str,
    *,
    confidence: str = "high",
) -> KnowledgeGraph:
    """Add a call edge if missing."""
    src = symbol_ref_to_node_id(caller_qn)
    dst = symbol_ref_to_node_id(callee_qn)
    if get_node(graph, src) is None or get_node(graph, dst) is None:
        raise AssertionError(f"missing nodes for {caller_qn} -> {callee_qn}")
    eid = edge_id(EdgeType.CALL.value, src, dst)
    if any(e.id == eid for e in graph.edges):
        return graph
    edge = KnowledgeEdge(
        id=eid,
        source_id=src,
        target_id=dst,
        edge_type=EdgeType.CALL,
        confidence=confidence,  # type: ignore[arg-type]
        meta={"fixture_seeded": True},
    )
    edges = list(graph.edges) + [edge]
    call_count = graph.stats.edge_counts.get("call", 0) + 1
    stats = graph.stats.model_copy(
        update={"edge_counts": {**graph.stats.edge_counts, "call": call_count}}
    )
    return graph.model_copy(update={"edges": edges, "stats": stats})


def seed_spring_login_calls(graph: KnowledgeGraph) -> KnowledgeGraph:
    """
    DependencyGraphBuilder often misses cross-file Java method invocations
    (callee is a simple name; methods are omitted from the global symbol index).
    Seed the expected layered calls so Flow Trace can validate roles/lines.
    """
    pairs = (
        (
            "auth/AuthController.java::AuthController.login",
            "auth/AuthService.java::AuthService.login",
        ),
        (
            "auth/AuthService.java::AuthService.login",
            "user/UserRepository.java::UserRepository.findByUsername",
        ),
    )
    for caller, callee in pairs:
        graph = ensure_call(graph, caller, callee)
    return graph
