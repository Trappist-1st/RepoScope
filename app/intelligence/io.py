"""Load / save knowledge_graph.json artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.intelligence.models import KnowledgeGraph

ARTIFACT_NAME = "knowledge_graph.json"


def knowledge_graph_path(repo_id: str, artifact_dir: Path | None = None) -> Path:
    base = Path(artifact_dir or settings.artifact_dir)
    return base / repo_id / ARTIFACT_NAME


def save_knowledge_graph(
    graph: KnowledgeGraph,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
) -> Path:
    out = path or knowledge_graph_path(graph.repo_id, artifact_dir)
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
) -> KnowledgeGraph:
    target = path or knowledge_graph_path(repo_id, artifact_dir)
    if not target.exists():
        raise FileNotFoundError(f"knowledge_graph not found: {target}")
    raw = json.loads(target.read_text(encoding="utf-8"))
    return KnowledgeGraph.model_validate(raw)


def try_load_knowledge_graph(
    repo_id: str,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
) -> KnowledgeGraph | None:
    try:
        return load_knowledge_graph(repo_id, artifact_dir=artifact_dir, path=path)
    except FileNotFoundError:
        return None
