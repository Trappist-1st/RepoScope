"""Assemble a surgical context pack for ``context_explore``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.graph.query import callees_of, callers_of, children_of_type, parents_of
from app.mcp.schemas import (
    BlastRadiusHit,
    CallPathOut,
    CallPathStepOut,
    CitationOut,
    Evidence,
    ExploreSeedOut,
)
from app.models.schemas import Chunk, DependencyGraph

if TYPE_CHECKING:
    from app.intelligence.flow_models import FlowTrace

_FLOW_HINT = re.compile(
    r"(flow|流程|how does|怎么|如何|reach|调用链|login|登录|request|请求)",
    re.I,
)


def looks_like_flow_question(query: str) -> bool:
    return bool(_FLOW_HINT.search(query or ""))


def format_explore_markdown(
    *,
    query: str,
    seeds: list[ExploreSeedOut],
    must_read: list[ExploreSeedOut],
    call_paths: list[CallPathOut],
    blast_radius: list[BlastRadiusHit],
) -> str:
    lines = [
        f"## Context Explore",
        "",
        f"**Query:** {query}",
        "",
        "### Seeds",
    ]
    if not seeds:
        lines.append("_No seed symbols._")
    for s in seeds:
        cite = s.citation.text if s.citation else ""
        lines.append(f"- `{s.symbol_ref}` ({s.reason}, score={s.score:.2f}) {cite}")

    lines.extend(["", "### Must read"])
    if not must_read:
        lines.append("_No source excerpts._")
    for s in must_read:
        cite = s.citation.text if s.citation else s.symbol_ref
        lines.append(f"#### `{s.symbol_ref}` — {cite}")
        if s.snippet:
            lines.append("```")
            lines.append(s.snippet[:1200])
            lines.append("```")

    lines.extend(["", "### Call paths"])
    if not call_paths:
        lines.append("_No call paths._")
    for i, path in enumerate(call_paths, 1):
        chain = " → ".join(st.symbol_ref.split("::")[-1] for st in path.steps) or "(empty)"
        lines.append(
            f"{i}. `{chain}` (source={path.source}, confidence={path.confidence}, score={path.score:.2f})"
        )
        for st in path.steps:
            cite = st.citation.text if st.citation else ""
            role = f" [{st.role}]" if st.role else ""
            lines.append(f"   - {st.order}. `{st.symbol_ref}`{role} {cite}")

    lines.extend(["", "### Blast radius"])
    if not blast_radius:
        lines.append("_No related symbols._")
    for hit in blast_radius[:30]:
        lines.append(f"- `{hit.symbol_ref}` — {hit.relation} (hops={hit.hops})")

    return "\n".join(lines).rstrip() + "\n"


def seeds_from_chunks(
    chunks: list[Chunk],
    *,
    scores: list[float] | None = None,
    reason: str = "search",
    limit: int = 8,
) -> list[ExploreSeedOut]:
    from app.retrieval.source_boost import source_quality_multiplier

    scored: list[tuple[float, ExploreSeedOut]] = []
    seen: set[str] = set()
    for i, ch in enumerate(chunks):
        sym = ch.symbol_name or ""
        if sym:
            ref = f"{ch.file_path}::{sym}"
        else:
            ref = f"{ch.file_path}::__chunk__"
        if ref in seen:
            continue
        seen.add(ref)
        base = scores[i] if scores and i < len(scores) else max(0.0, 1.0 - i * 0.05)
        mult = source_quality_multiplier(
            file_path=ch.file_path, language=ch.language, kind=ch.kind
        )
        score = float(base) * mult
        scored.append(
            (
                score,
                ExploreSeedOut(
                    symbol_ref=ref if sym else ch.file_path,
                    score=score,
                    reason=reason,
                    citation=CitationOut.from_parts(ch.file_path, ch.start_line, ch.end_line),
                    snippet=(ch.content or "")[:800],
                ),
            )
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    return [item for _, item in scored[:limit]]


def blast_radius_for_seeds(
    graph: DependencyGraph,
    seed_refs: list[str],
    *,
    depth: int = 2,
    limit: int = 40,
    min_confidence: float | None = None,
) -> list[BlastRadiusHit]:
    """Callers, callees, and type relatives of the seeds.

    ``min_confidence`` drops call edges the cascade was not sure about. Legacy
    edges score 1.0, so passing a threshold is a no-op on a legacy graph.
    """
    hits: list[BlastRadiusHit] = []
    seen: set[tuple[str, str]] = set()
    evidence_by_pair = _call_evidence(graph)

    def _confident(a: str, b: str) -> bool:
        if min_confidence is None:
            return True
        return _call_confidence(graph, a, b) >= min_confidence

    def _add(
        ref: str, relation: str, hops: int, evidence: Evidence | None = None
    ) -> None:
        key = (ref, relation)
        if key in seen or not ref:
            return
        seen.add(key)
        hits.append(
            BlastRadiusHit(
                symbol_ref=ref,
                relation=relation,
                hops=hops,
                evidence=[evidence] if evidence is not None else [],
            )
        )

    frontier = list(seed_refs)
    for hop in range(1, max(1, depth) + 1):
        nxt: list[str] = []
        for ref in frontier:
            for caller in callers_of(graph, ref):
                if not _confident(caller, ref):
                    continue
                _add(caller, "caller", hop, evidence_by_pair.get((caller, ref)))
                nxt.append(caller)
            for callee in callees_of(graph, ref):
                if not _confident(ref, callee):
                    continue
                _add(callee, "callee", hop, evidence_by_pair.get((ref, callee)))
                nxt.append(callee)
            for parent in parents_of(graph, ref):
                # find relation from edge
                rel = "extends"
                for e in graph.inherit_edges:
                    if e.child == ref and e.parent == parent:
                        rel = e.relation
                        break
                _add(parent, rel, hop)
            for child in children_of_type(graph, ref):
                _add(child, "child", hop)
                nxt.append(child)
            if len(hits) >= limit:
                return hits[:limit]
        frontier = nxt
        if not frontier:
            break
    return hits[:limit]


def _call_evidence(graph: DependencyGraph) -> dict[tuple[str, str], Evidence]:
    """(caller, callee) -> the `file:line` the call was actually observed on.

    Without this the blast radius names symbols but cannot point at the line
    that justifies the relationship, which is the anti-hallucination contract.
    """
    out: dict[tuple[str, str], Evidence] = {}
    for e in graph.call_edges:
        if e.call_line is None:
            continue
        file_path = e.caller.split("::", 1)[0]
        out.setdefault(
            (e.caller, e.callee),
            Evidence(
                citation=CitationOut.from_parts(file_path, e.call_line, e.call_line),
                symbol_name=e.callee.split("::")[-1],
                evidence_tier="direct",
                expansion_reason=e.resolution_strategy,
            ),
        )
    return out


def _call_confidence(graph: DependencyGraph, caller: str, callee: str) -> float:
    best = 0.0
    found = False
    for e in graph.call_edges:
        if e.caller == caller and e.callee == callee:
            found = True
            best = max(best, e.confidence)
    # Inherit/import relatives arrive here without a call edge; do not prune them.
    return best if found else 1.0


def call_paths_from_callees(
    graph: DependencyGraph,
    seed_ref: str,
    *,
    max_depth: int = 4,
) -> CallPathOut | None:
    """Greedy callee chain from a seed (lightweight path without beam search)."""
    steps: list[CallPathStepOut] = [
        CallPathStepOut(order=1, symbol_ref=seed_ref, note="seed")
    ]
    current = seed_ref
    seen = {seed_ref}
    for order in range(2, max_depth + 1):
        callees = callees_of(graph, current)
        if not callees:
            break
        nxt = callees[0]
        if nxt in seen:
            break
        seen.add(nxt)
        steps.append(CallPathStepOut(order=order, symbol_ref=nxt))
        current = nxt
    if len(steps) < 2:
        return None
    return CallPathOut(steps=steps, score=0.5, confidence="medium", source="graph")


def call_path_from_flow(trace: FlowTrace) -> CallPathOut | None:
    if not trace.steps:
        return None
    steps: list[CallPathStepOut] = []
    for st in trace.steps:
        cite = None
        if st.file_path and st.start_line is not None:
            end = st.end_line if st.end_line is not None else st.start_line
            cite = CitationOut.from_parts(st.file_path, st.start_line, end)
        ref = st.qualified_name or st.symbol
        if st.file_path and "::" not in ref:
            ref = f"{st.file_path}::{st.symbol}"
        steps.append(
            CallPathStepOut(
                order=st.order,
                symbol_ref=ref,
                role=st.role.value if hasattr(st.role, "value") else str(st.role),
                citation=cite,
                note=st.note,
            )
        )
    return CallPathOut(
        steps=steps,
        score=float(trace.ranking_score or 0.0),
        confidence=trace.confidence,  # type: ignore[arg-type]
        source="flow_tracer",
    )
