from __future__ import annotations

from dataclasses import dataclass, field

from app.context_engine.config import ContextConfig, load_context_config
from app.context_engine.features import (
    file_ref_count,
    is_entry_file,
    min_max_norm,
    raw_relevance,
    symbol_ref_count,
    tier_score,
)
from app.models.schemas import DependencyGraph
from app.retrieval.schemas import RetrievalHit


@dataclass
class PriorityBreakdown:
    chunk_id: str
    citation: str
    priority: float
    entry: float
    graph: float
    relevance: float
    tier: float
    file_ref: int
    symbol_ref: int


@dataclass
class ScoredHit:
    hit: RetrievalHit
    breakdown: PriorityBreakdown


def score_candidates(
    hits: list[RetrievalHit],
    expanded_hits: list[RetrievalHit],
    graph: DependencyGraph | None = None,
    config: ContextConfig | None = None,
) -> list[ScoredHit]:
    """
    priority = w_e*E + w_g*G + w_r*R + w_t*T

    G = mix.file * norm(file_ref) + mix.symbol * norm(symbol_ref)
    (file/symbol normalized separately over the candidate set)
    """
    cfg = config or load_context_config()
    candidates = list(hits) + list(expanded_hits)
    if not candidates:
        return []

    file_raw: dict[str, float] = {}
    symbol_raw: dict[str, float] = {}
    rel_raw: dict[str, float] = {}

    for h in candidates:
        cid = h.chunk_id
        file_raw[cid] = float(file_ref_count(h.citation.file_path, graph))
        symbol_raw[cid] = float(symbol_ref_count(h, graph))
        rel_raw[cid] = raw_relevance(h)

    file_n = min_max_norm(file_raw)
    symbol_n = min_max_norm(symbol_raw)
    rel_n = min_max_norm(rel_raw)

    w = cfg.weights
    mix = cfg.graph_mix
    scored: list[ScoredHit] = []
    for h in candidates:
        cid = h.chunk_id
        e = 1.0 if is_entry_file(h.citation.file_path, cfg.entry_files) else 0.0
        g = mix.file * file_n.get(cid, 0.0) + mix.symbol * symbol_n.get(cid, 0.0)
        r = rel_n.get(cid, 0.0)
        t = tier_score(h)
        priority = w.entry * e + w.graph * g + w.relevance * r + w.tier * t
        scored.append(
            ScoredHit(
                hit=h,
                breakdown=PriorityBreakdown(
                    chunk_id=cid,
                    citation=h.citation.format(),
                    priority=priority,
                    entry=e,
                    graph=g,
                    relevance=r,
                    tier=t,
                    file_ref=int(file_raw[cid]),
                    symbol_ref=int(symbol_raw[cid]),
                ),
            )
        )
    return scored
