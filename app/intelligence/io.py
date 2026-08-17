"""Load / save knowledge graph artifacts (JSON file or SQLite single file)."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.intelligence.models import KnowledgeGraph

ARTIFACT_NAME = "knowledge_graph.json"


def _sqlite_selected(storage: str | None) -> bool:
    return (storage or settings.kg_storage) == "sqlite"


def knowledge_graph_path(
    repo_id: str,
    artifact_dir: Path | None = None,
    *,
    storage: str | None = None,
) -> Path:
    if _sqlite_selected(storage):
        from app.storage.sqlite_store import db_path

        return db_path(repo_id, artifact_dir)
    base = Path(artifact_dir or settings.artifact_dir)
    return base / repo_id / ARTIFACT_NAME


def _json_path(repo_id: str, artifact_dir: Path | None) -> Path:
    base = Path(artifact_dir or settings.artifact_dir)
    return base / repo_id / ARTIFACT_NAME


def save_knowledge_graph(
    graph: KnowledgeGraph,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
    storage: str | None = None,
) -> Path:
    if _sqlite_selected(storage):
        from app.storage.sqlite_store import save_knowledge_graph_db

        return save_knowledge_graph_db(graph, artifact_dir=artifact_dir, path=path)

    out = path or _json_path(graph.repo_id, artifact_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(graph.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def load_knowledge_graph(
    repo_id: str,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
    storage: str | None = None,
) -> KnowledgeGraph:
    """Read the graph, preferring the configured backend but accepting either.

    Reading both ways keeps the ``kg_storage`` switch cheap: flipping it does
    not invalidate artifacts already on disk, so rollback needs no reindex.
    """
    if path is not None:
        return _load_from(path, repo_id)

    preferred = knowledge_graph_path(repo_id, artifact_dir, storage=storage)
    if preferred.exists():
        return _load_from(preferred, repo_id)

    other = (
        _json_path(repo_id, artifact_dir)
        if _sqlite_selected(storage)
        else _sqlite_fallback_path(repo_id, artifact_dir)
    )
    if other.exists():
        return _load_from(other, repo_id)

    raise FileNotFoundError(f"knowledge_graph not found: {preferred}")


def _sqlite_fallback_path(repo_id: str, artifact_dir: Path | None) -> Path:
    from app.storage.sqlite_store import db_path

    return db_path(repo_id, artifact_dir)


def _load_from(target: Path, repo_id: str) -> KnowledgeGraph:
    if not target.exists():
        raise FileNotFoundError(f"knowledge_graph not found: {target}")
    if target.suffix == ".json":
        raw = json.loads(target.read_text(encoding="utf-8"))
        return KnowledgeGraph.model_validate(raw)

    from app.storage.sqlite_store import load_knowledge_graph_db

    return load_knowledge_graph_db(repo_id, path=target)


def try_load_knowledge_graph(
    repo_id: str,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
    storage: str | None = None,
) -> KnowledgeGraph | None:
    try:
        return load_knowledge_graph(
            repo_id, artifact_dir=artifact_dir, path=path, storage=storage
        )
    except FileNotFoundError:
        return None
