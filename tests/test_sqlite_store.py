"""SQLite artifact backend: round-trip fidelity and differential writes."""

from __future__ import annotations

from pathlib import Path

from app.intelligence.models import (
    EdgeType,
    EvidenceSpan,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeGraphSource,
    KnowledgeGraphStats,
    KnowledgeNode,
    NodeKind,
)
from app.models.schemas import Chunk
from app.storage import sqlite_store


def _graph(repo_id: str = "repo1", *, edge_score: float = 0.95) -> KnowledgeGraph:
    nodes = [
        KnowledgeNode(
            id="n1",
            kind=NodeKind.FUNCTION,
            name="login",
            qualified_name="svc.py::login",
            file_path="svc.py",
            start_line=1,
            end_line=9,
            language="python",
        ),
        KnowledgeNode(
            id="n2",
            kind=NodeKind.FUNCTION,
            name="find_by_username",
            qualified_name="repo.py::find_by_username",
            file_path="repo.py",
            start_line=3,
            end_line=7,
            language="python",
        ),
    ]
    edges = [
        KnowledgeEdge(
            id="e1",
            source_id="n1",
            target_id="n2",
            edge_type=EdgeType.CALL,
            confidence="high",
            confidence_score=edge_score,
            resolution_strategy="import_map",
            evidence=[EvidenceSpan(file_path="svc.py", start_line=5)],
        )
    ]
    return KnowledgeGraph(
        repo_id=repo_id,
        commit_hash="abc123",
        nodes=nodes,
        edges=edges,
        stats=KnowledgeGraphStats(node_count=2, edge_count=1),
        source=KnowledgeGraphSource(advanced=True),
    )


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            chunk_id="c1",
            file_path="svc.py",
            start_line=1,
            end_line=9,
            content="def login(): ...",
            kind="function",
            symbol_name="login",
            language="python",
            content_hash="h1",
        ),
        Chunk(
            chunk_id="c2",
            file_path="repo.py",
            start_line=3,
            end_line=7,
            content="def find_by_username(): ...",
            kind="function",
            symbol_name="find_by_username",
            language="python",
            content_hash="h2",
        ),
    ]


def test_knowledge_graph_round_trip(tmp_path: Path):
    graph = _graph()
    sqlite_store.save_knowledge_graph_db(graph, artifact_dir=tmp_path)
    loaded = sqlite_store.load_knowledge_graph_db("repo1", artifact_dir=tmp_path)

    assert loaded.repo_id == graph.repo_id
    assert loaded.commit_hash == graph.commit_hash
    assert {n.id for n in loaded.nodes} == {"n1", "n2"}
    assert loaded.source.advanced is True

    edge = loaded.edges[0]
    assert edge.edge_type == EdgeType.CALL
    assert edge.confidence_score == 0.95
    assert edge.resolution_strategy == "import_map"
    assert edge.evidence[0].citation == "svc.py:5"


def test_second_save_writes_only_changed_rows(tmp_path: Path):
    """A re-save of an unchanged graph must touch nothing."""
    graph = _graph()
    sqlite_store.save_knowledge_graph_db(graph, artifact_dir=tmp_path)

    path = sqlite_store.db_path("repo1", tmp_path)
    with sqlite_store.connect(path, create=False) as conn:
        before = {
            r["id"]: r["row_hash"] for r in conn.execute("SELECT id, row_hash FROM kg_edges")
        }

    sqlite_store.save_knowledge_graph_db(graph, artifact_dir=tmp_path)
    with sqlite_store.connect(path, create=False) as conn:
        after = {
            r["id"]: r["row_hash"] for r in conn.execute("SELECT id, row_hash FROM kg_edges")
        }
    assert before == after

    sqlite_store.save_knowledge_graph_db(_graph(edge_score=0.55), artifact_dir=tmp_path)
    with sqlite_store.connect(path, create=False) as conn:
        changed = {
            r["id"]: r["row_hash"] for r in conn.execute("SELECT id, row_hash FROM kg_edges")
        }
    assert changed["e1"] != before["e1"]


def test_removed_nodes_are_deleted(tmp_path: Path):
    graph = _graph()
    sqlite_store.save_knowledge_graph_db(graph, artifact_dir=tmp_path)

    shrunk = graph.model_copy(update={"nodes": graph.nodes[:1], "edges": []})
    sqlite_store.save_knowledge_graph_db(shrunk, artifact_dir=tmp_path)

    loaded = sqlite_store.load_knowledge_graph_db("repo1", artifact_dir=tmp_path)
    assert {n.id for n in loaded.nodes} == {"n1"}
    assert loaded.edges == []


def test_chunks_round_trip_and_share_one_file(tmp_path: Path):
    sqlite_store.save_knowledge_graph_db(_graph(), artifact_dir=tmp_path)
    sqlite_store.save_chunks("repo1", _chunks(), artifact_dir=tmp_path)

    # Insertion order is preserved: retrieval breaks score ties by position.
    loaded = sqlite_store.load_chunks("repo1", artifact_dir=tmp_path)
    assert [c.chunk_id for c in loaded] == ["c1", "c2"]
    assert loaded[0].content == "def login(): ..."

    # Graph and chunks live in the same database file.
    dbs = list((tmp_path / "repo1").glob("*.db"))
    assert len(dbs) == 1
    assert sqlite_store.load_knowledge_graph_db("repo1", artifact_dir=tmp_path).edges
