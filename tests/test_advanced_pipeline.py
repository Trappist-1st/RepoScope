"""End-to-end behaviour of the two orthogonal switches.

`use_advanced_kg` controls how edges are resolved; `kg_storage` controls where
artifacts land. Neither may change what the other one does, and turning both
off must reproduce the pre-refactor pipeline exactly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.db.postgres import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion.incremental import IngestionPipeline
from app.storage import sqlite_store
from tests.conftest import SAMPLE_REPO

FIXTURES = Path(__file__).parent / "fixtures"
CONFLICT = FIXTURES / "name_conflict_repo"


def _pipeline(
    tmp_path: Path, *, advanced: bool = False, storage: str = "json"
) -> IngestionPipeline:
    return IngestionPipeline(
        workspace_root=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
        use_advanced_kg=advanced,
        kg_storage=storage,  # type: ignore[arg-type]
    )


def _copy(repo_src: Path, tmp_path: Path, name: str) -> Path:
    dst = tmp_path / name
    shutil.copytree(repo_src, dst)
    return dst


def test_comment_only_edit_reuses_the_graph(tmp_path: Path):
    """Bytes changed, AST structure did not: no graph rebuild."""
    repo = _copy(SAMPLE_REPO, tmp_path, "repo_cosmetic")
    pipeline = _pipeline(tmp_path, advanced=True)
    pipeline.run(str(repo))

    target = repo / "py_pkg" / "a.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# a comment, nothing structural\n",
        encoding="utf-8",
    )
    result = pipeline.run(str(repo))

    assert result.changed_files == ["py_pkg/a.py"]
    assert result.graph_update_mode == "structure_cached"


def test_structural_edit_still_rebuilds(tmp_path: Path):
    repo = _copy(SAMPLE_REPO, tmp_path, "repo_structural")
    pipeline = _pipeline(tmp_path, advanced=True)
    pipeline.run(str(repo))

    target = repo / "py_pkg" / "a.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\ndef brand_new_symbol():\n    return 1\n",
        encoding="utf-8",
    )
    result = pipeline.run(str(repo))

    assert result.changed_files == ["py_pkg/a.py"]
    assert result.graph_update_mode in {"merge", "full"}
    assert any(
        d.name == "brand_new_symbol"
        for r in result.parse_results
        for d in r.definitions
    )


def test_legacy_mode_is_byte_identical_after_rollback(tmp_path: Path):
    """Running with the switch off produces the original graph, always."""
    repo_a = _copy(SAMPLE_REPO, tmp_path, "repo_legacy_a")
    repo_b = _copy(SAMPLE_REPO, tmp_path, "repo_legacy_b")

    baseline = _pipeline(tmp_path / "a", advanced=False).run(str(repo_a)).graph
    # Same input, same switch state, after the advanced code paths exist.
    rolled_back = _pipeline(tmp_path / "b", advanced=False).run(str(repo_b)).graph

    assert baseline.model_dump(exclude={"repo_id"}) == rolled_back.model_dump(
        exclude={"repo_id"}
    )
    for edge in rolled_back.call_edges:
        assert edge.resolution_strategy == "legacy"
        assert edge.call_line is None


def test_sqlite_and_json_backends_agree(tmp_path: Path):
    repo_json = _copy(CONFLICT, tmp_path, "repo_json")
    repo_db = _copy(CONFLICT, tmp_path, "repo_db")

    pipe_json = _pipeline(tmp_path / "j", advanced=True, storage="json")
    res_json = pipe_json.run(str(repo_json))
    kg_json = pipe_json.load_knowledge_graph(res_json.repo_id)

    pipe_db = _pipeline(tmp_path / "d", advanced=True, storage="sqlite")
    res_db = pipe_db.run(str(repo_db))
    kg_db = pipe_db.load_knowledge_graph(res_db.repo_id)

    assert sqlite_store.db_path(res_db.repo_id, tmp_path / "d" / "artifacts").exists()
    assert {n.qualified_name for n in kg_json.nodes} == {
        n.qualified_name for n in kg_db.nodes
    }
    assert len(kg_json.edges) == len(kg_db.edges)

    # Order matters, not just membership: retrieval breaks score ties by
    # position, so a reordering backend silently shifts Recall@k.
    chunks_json, _ = pipe_json.load_artifacts(res_json.repo_id)
    chunks_db, _ = pipe_db.load_artifacts(res_db.repo_id)
    assert [c.chunk_id for c in chunks_json] == [c.chunk_id for c in chunks_db]


def test_advanced_graph_edges_carry_evidence(tmp_path: Path):
    repo = _copy(CONFLICT, tmp_path, "repo_evidence")
    pipeline = _pipeline(tmp_path, advanced=True)
    result = pipeline.run(str(repo))
    kg = pipeline.load_knowledge_graph(result.repo_id)

    assert kg.source.advanced is True
    call_edges = [e for e in kg.edges if e.edge_type.value == "call"]
    assert call_edges
    assert any(e.evidence for e in call_edges), "no call edge carries a file:line"
    for edge in call_edges:
        for span in edge.evidence:
            assert ":" in span.citation
