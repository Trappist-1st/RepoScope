from __future__ import annotations

from pathlib import Path

from app.models.schemas import DependencyGraph
from app.retrieval.schemas import RetrievalHit


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


def is_entry_file(file_path: str, entry_files: list[str]) -> bool:
    name = Path(file_path.replace("\\", "/")).name.lower()
    allowed = {e.lower() for e in entry_files}
    if name in allowed:
        return True
    # common Spring Boot style path
    posix = file_path.replace("\\", "/")
    if name.endswith("application.java") and "/src/main/java/" in posix.lower():
        return True
    return False


def file_ref_count(file_path: str, graph: DependencyGraph | None) -> int:
    if graph is None:
        return 0
    return sum(1 for e in graph.file_edges if e.target == file_path)


def symbol_ref_count(hit: RetrievalHit, graph: DependencyGraph | None) -> int:
    if graph is None or not hit.symbol_name:
        return 0
    refs = {
        f"{hit.citation.file_path}::{hit.symbol_name}",
        f"{hit.citation.file_path}::{hit.symbol_name.split('.')[-1]}",
    }
    return sum(1 for e in graph.call_edges if e.callee in refs)


def raw_relevance(hit: RetrievalHit) -> float:
    if hit.source == "graph_expand":
        return 0.0
    if "rerank" in hit.scores:
        return float(hit.scores["rerank"])
    return float(hit.score)


def tier_score(hit: RetrievalHit) -> float:
    if hit.source == "graph_expand":
        reason = (hit.expansion_reason or "").lower()
        if "called_by:" in reason or reason.startswith("calls:"):
            return 0.55
        return 0.35
    return 1.0


def min_max_norm(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi - lo < 1e-12:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}
