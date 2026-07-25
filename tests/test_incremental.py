from pathlib import Path

from app.db.postgres import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion.incremental import IngestionPipeline
from tests.conftest import SAMPLE_REPO


def _pipeline(tmp_path: Path) -> IngestionPipeline:
    return IngestionPipeline(
        workspace_root=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )


def test_full_ingest_then_incremental(tmp_path: Path):
    # Copy sample repo into a writable temp dir so we can mutate a file
    import shutil

    repo_dir = tmp_path / "sample_repo"
    shutil.copytree(SAMPLE_REPO, repo_dir)

    pipeline = _pipeline(tmp_path)
    first = pipeline.run(str(repo_dir))
    assert first.repo_id
    assert len(first.changed_files) >= 3  # py/js/java sources
    assert first.unchanged_count == 0
    assert first.graph.file_edges or first.graph.call_edges

    chunks, graph = pipeline.load_artifacts(first.repo_id)
    assert chunks
    assert graph.repo_id == first.repo_id

    # Second run with no changes → everything unchanged
    second = pipeline.run(str(repo_dir))
    assert second.changed_files == []
    assert second.deleted_files == []
    assert second.unchanged_count == len(first.changed_files)
    assert second.parse_results == []

    # Mutate one file → only that file reprocessed
    target = repo_dir / "py_pkg" / "a.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# touch\n", encoding="utf-8")

    third = pipeline.run(str(repo_dir))
    assert third.changed_files == ["py_pkg/a.py"]
    assert len(third.parse_results) == 1
    assert third.parse_results[0].file_path == "py_pkg/a.py"
    assert third.parse_results[0].parse_ok is True

    hashes = pipeline.files_repo.get_file_hashes(third.repo_id)
    assert "py_pkg/a.py" in hashes
