from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.postgres import FilesRepository, ReposRepository
from app.ingestion.incremental import IngestionPipeline
from app.retrieval.schemas import IndexRequest
from app.retrieval.service import RetrievalService
from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout


def make_repo_parse_node(
    *,
    ingestion: IngestionPipeline,
    retrieval: RetrievalService,
) -> Any:
    def repo_parse_node(state: WorkflowState) -> WorkflowState:
        def _run() -> WorkflowState:
            result = ingestion.run(state["repo_source"])
            chunks, graph = ingestion.load_artifacts(result.repo_id)
            retrieval.index(
                IndexRequest(repo_id=result.repo_id, chunks=chunks, force_reindex=False)
            )
            return {
                "repo_id": result.repo_id,
                "commit_hash": result.commit_hash,
                "local_path": result.local_path,
                "indexed": True,
                "dependency_graph": graph,
            }

        try:
            return run_with_timeout("repo_parse", _run)
        except NodeTimeoutError:
            return {
                "indexed": False,
                "timeouts": {"repo_parse": True},
                "errors": ["repo_parse timed out"],
                "status": "partial",
            }
        except Exception as exc:  # noqa: BLE001 — surface into workflow
            return {
                "indexed": False,
                "errors": [f"repo_parse failed: {exc}"],
                "status": "partial",
            }

    return repo_parse_node


def default_ingestion(
    *,
    workspace_root: Path,
    artifact_dir: Path,
    files_repo: FilesRepository,
    repos_repo: ReposRepository,
) -> IngestionPipeline:
    return IngestionPipeline(
        workspace_root=workspace_root,
        artifact_dir=artifact_dir,
        files_repo=files_repo,
        repos_repo=repos_repo,
    )
