"""Backward-compatible helpers — prefer app.context_engine.assemble_context. """

from __future__ import annotations

from app.context_engine.assembler import assemble_context
from app.context_engine.features import estimate_tokens
from app.retrieval.schemas import RetrievalHit

__all__ = ["estimate_tokens", "trim_hits_to_budget"]


def trim_hits_to_budget(
    hits: list[RetrievalHit],
    expanded_hits: list[RetrievalHit],
    budget: int,
) -> tuple[list[RetrievalHit], list[RetrievalHit], int]:
    assembled = assemble_context(
        question="",
        plan_hint="",
        hits=hits,
        expanded_hits=expanded_hits,
        budget=budget,
    )
    return assembled.code_hits, assembled.expanded_hits, assembled.after_tokens
