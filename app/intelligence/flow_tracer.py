"""FlowTracer: orchestrate entry discovery → beam search → verified FlowTrace."""

from __future__ import annotations

import time

from app.intelligence.enrichers.roles import (
    FlowRole,
    RoleIndex,
    build_role_index,
    role_of,
)
from app.intelligence.flow_entry import RetrieveFn, discover_entries
from app.intelligence.flow_models import (
    FlowPathSummary,
    FlowStep,
    FlowTrace,
    FlowTraceMeta,
    TraceQuery,
)
from app.intelligence.flow_search import (
    CandidatePath,
    SearchLimits,
    beam_search_paths,
    build_call_adjacency,
    path_confidence,
)
from app.intelligence.flow_topics import extract_topic
from app.intelligence.models import EvidenceSpan, KnowledgeGraph, KnowledgeNode
from app.intelligence.query import get_node


class FlowTracer:
    """Library entry point for Repository-level flow understanding."""

    def __init__(
        self,
        *,
        retrieve_fn: RetrieveFn | None = None,
        file_texts: dict[str, str] | None = None,
    ) -> None:
        self.retrieve_fn = retrieve_fn
        self.file_texts = file_texts

    def trace(
        self,
        graph: KnowledgeGraph,
        question: str,
        *,
        entry_hint: str | None = None,
        max_depth: int = 5,
        max_paths: int = 3,
        max_branching: int = 8,
        session_id: str | None = None,
        language_prefer: list[str] | None = None,
        role_index: RoleIndex | None = None,
    ) -> FlowTrace:
        t0 = time.perf_counter()
        topic, terms = extract_topic(question)
        query = TraceQuery(
            question=question,
            repo_id=graph.repo_id,
            topic=topic,
            topic_terms=list(terms),
            entry_hints=[entry_hint] if entry_hint else [],
            max_depth=max_depth,
            max_paths=max_paths,
            max_branching=max_branching,
            language_prefer=language_prefer,
            session_id=session_id,
        )
        roles = role_index or build_role_index(graph, self.file_texts)
        unresolved: list[str] = []
        warnings: list[str] = []

        entries = discover_entries(
            graph,
            question,
            role_index=roles,
            entry_hint=entry_hint,
            top_k=5,
            retrieve_fn=self.retrieve_fn,
            language_prefer=language_prefer,
        )
        if not entries:
            unresolved.append("no_entry_candidate")
            return self._empty_trace(query, graph, unresolved, warnings, t0)

        if entries[0].role not in {FlowRole.CONTROLLER, FlowRole.GATEWAY}:
            warnings.append(
                f"entry_role_is_{entries[0].role.value}_not_controller"
            )

        limits = SearchLimits(
            max_depth=max_depth,
            beam_width=max(4, max_paths + 1),
            max_branching=max_branching,
            max_paths=max_paths,
        )
        adjacency = build_call_adjacency(graph)

        best_path: CandidatePath | None = None
        all_summaries: list[FlowPathSummary] = []

        for entry in entries[:3]:
            paths = beam_search_paths(
                graph,
                entry.node_id,
                role_index=roles,
                topic_terms=terms,
                limits=limits,
                adjacency=adjacency,
            )
            if not paths:
                unresolved.append(f"no_call_path_from:{entry.node_id}")
                continue
            for p in paths:
                summary = FlowPathSummary(
                    entry_node_id=entry.node_id,
                    step_symbols=_path_symbols(graph, p),
                    score=p.score,
                    confidence=path_confidence(graph, p, roles),
                )
                all_summaries.append(summary)
                if best_path is None or p.score > best_path.score:
                    best_path = p

        if best_path is None:
            # Fall back: single-step entry only
            entry_node = get_node(graph, entries[0].node_id)
            if entry_node is None:
                unresolved.append("entry_node_missing")
                return self._empty_trace(query, graph, unresolved, warnings, t0)
            step = _node_to_step(
                entry_node,
                order=1,
                role=role_of(roles, entry_node.id),
                reason="entry_only_no_outgoing_calls",
                inference_reason="beam_search_found_no_call_path",
            )
            unresolved.append("partial_trace_entry_only")
            took = int((time.perf_counter() - t0) * 1000)
            return FlowTrace(
                query=query,
                entry=step,
                steps=[step],
                alternatives=[],
                unresolved=unresolved,
                confidence="low",
                ranking_score=entries[0].score,
                warnings=warnings,
                meta=FlowTraceMeta(
                    repo_id=graph.repo_id,
                    commit_hash=graph.commit_hash,
                    kg_schema_version=graph.schema_version,
                    took_ms=took,
                ),
            )

        steps = _path_to_steps(graph, best_path, roles)
        steps = _maybe_append_synthetic_terminal(steps, roles)
        steps, step_unresolved = _verify_steps(steps)
        unresolved.extend(step_unresolved)

        entry_step = steps[0] if steps else None
        conf = path_confidence(graph, best_path, roles)
        if any(s.confidence == "low" for s in steps):
            conf = "low"
        elif any(s.is_synthetic for s in steps) and conf == "high":
            conf = "medium"

        # Dedup alternatives (exclude primary path symbol chain)
        primary_symbols = tuple(_path_symbols(graph, best_path))
        alternatives: list[FlowPathSummary] = []
        alt_seen: set[tuple[str, ...]] = {primary_symbols}
        for alt in sorted(all_summaries, key=lambda s: -s.score):
            key = tuple(alt.step_symbols)
            if key in alt_seen:
                continue
            alt_seen.add(key)
            alternatives.append(alt)
            if len(alternatives) >= max(0, max_paths - 1):
                break

        took = int((time.perf_counter() - t0) * 1000)
        return FlowTrace(
            query=query,
            entry=entry_step,
            steps=steps,
            alternatives=alternatives,
            unresolved=list(dict.fromkeys(unresolved)),
            confidence=conf,
            ranking_score=best_path.score,
            warnings=warnings,
            meta=FlowTraceMeta(
                repo_id=graph.repo_id,
                commit_hash=graph.commit_hash,
                kg_schema_version=graph.schema_version,
                took_ms=took,
            ),
        )

    def _empty_trace(
        self,
        query: TraceQuery,
        graph: KnowledgeGraph,
        unresolved: list[str],
        warnings: list[str],
        t0: float,
    ) -> FlowTrace:
        took = int((time.perf_counter() - t0) * 1000)
        return FlowTrace(
            query=query,
            entry=None,
            steps=[],
            unresolved=unresolved,
            confidence="low",
            warnings=warnings,
            meta=FlowTraceMeta(
                repo_id=graph.repo_id,
                commit_hash=graph.commit_hash,
                kg_schema_version=graph.schema_version,
                took_ms=took,
            ),
        )


def trace_flow(
    graph: KnowledgeGraph,
    question: str,
    **kwargs,
) -> FlowTrace:
    """Functional wrapper around FlowTracer.trace."""
    return FlowTracer(
        retrieve_fn=kwargs.pop("retrieve_fn", None),
        file_texts=kwargs.pop("file_texts", None),
    ).trace(graph, question, **kwargs)


def _path_symbols(graph: KnowledgeGraph, path: CandidatePath) -> list[str]:
    out: list[str] = []
    for nid in path.node_ids:
        n = get_node(graph, nid)
        out.append(_display_symbol(n) if n else nid)
    return out


def _path_to_steps(
    graph: KnowledgeGraph,
    path: CandidatePath,
    roles: RoleIndex,
) -> list[FlowStep]:
    steps: list[FlowStep] = []
    for i, nid in enumerate(path.node_ids):
        node = get_node(graph, nid)
        if node is None:
            steps.append(
                FlowStep(
                    order=i + 1,
                    symbol=nid,
                    node_id=nid,
                    role=FlowRole.UNKNOWN,
                    confidence="low",
                    edge_from_prev=path.edge_ids[i - 1] if i else None,
                    reason="missing_node",
                    inference_reason="node_id_present_in_path_but_not_in_graph",
                )
            )
            continue
        role = role_of(roles, nid)
        reason = "entry" if i == 0 else "call_edge"
        inference = None
        if i == 0:
            inference = "selected_by_entry_discovery"
        else:
            inference = f"via_call:{path.edge_ids[i - 1]}"
        steps.append(
            _node_to_step(
                node,
                order=i + 1,
                role=role,
                edge_from_prev=path.edge_ids[i - 1] if i else None,
                reason=reason,
                inference_reason=inference,
            )
        )
    return steps


def _node_to_step(
    node: KnowledgeNode,
    *,
    order: int,
    role: FlowRole,
    edge_from_prev: str | None = None,
    reason: str | None = None,
    inference_reason: str | None = None,
) -> FlowStep:
    evidence: list[EvidenceSpan] = []
    conf: str = "high"
    if node.file_path and node.start_line is not None:
        evidence.append(
            EvidenceSpan(
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
            )
        )
    else:
        conf = "low"
    if node.meta.get("source") == "call_edge_inferred":
        conf = "low"
    return FlowStep(
        order=order,
        symbol=_display_symbol(node),
        node_id=node.id,
        qualified_name=node.qualified_name,
        file_path=node.file_path,
        start_line=node.start_line,
        end_line=node.end_line,
        role=role,
        evidence=evidence,
        confidence=conf,  # type: ignore[arg-type]
        edge_from_prev=edge_from_prev,
        reason=reason,
        inference_reason=inference_reason,
    )


def _display_symbol(node: KnowledgeNode) -> str:
    if "::" in node.qualified_name:
        return node.qualified_name.split("::", 1)[1]
    return node.name


def _maybe_append_synthetic_terminal(
    steps: list[FlowStep],
    roles: RoleIndex,
) -> list[FlowStep]:
    if not steps:
        return steps
    last = steps[-1]
    if last.is_synthetic:
        return steps

    # Database after repository
    if last.role == FlowRole.REPOSITORY or (
        last.node_id and role_of(roles, last.node_id) == FlowRole.REPOSITORY
    ):
        evidence = list(last.evidence)
        steps.append(
            FlowStep(
                order=last.order + 1,
                symbol="Database / persistent store",
                node_id=None,
                role=FlowRole.DATABASE,
                evidence=evidence,
                confidence="medium",
                is_synthetic=True,
                note="inferred storage access from repository method",
                reason="synthetic_terminal",
                inference_reason="repository_method_implies_datastore_access",
            )
        )
        return steps

    # MQ terminal
    if last.role == FlowRole.MQ:
        evidence = list(last.evidence)
        steps.append(
            FlowStep(
                order=last.order + 1,
                symbol="Message queue",
                node_id=None,
                role=FlowRole.MQ,
                evidence=evidence,
                confidence="medium",
                is_synthetic=True,
                note="inferred message publish/consume boundary",
                reason="synthetic_terminal",
                inference_reason="mq_role_implies_broker_boundary",
            )
        )
    return steps


def _verify_steps(steps: list[FlowStep]) -> tuple[list[FlowStep], list[str]]:
    unresolved: list[str] = []
    out: list[FlowStep] = []
    for step in steps:
        if step.is_synthetic:
            out.append(step)
            continue
        if not step.file_path or step.start_line is None:
            unresolved.append(f"missing_location:{step.symbol}")
            out.append(step.model_copy(update={"confidence": "low"}))
        elif not step.evidence:
            unresolved.append(f"missing_evidence:{step.symbol}")
            ev = [
                EvidenceSpan(
                    file_path=step.file_path,
                    start_line=step.start_line,
                    end_line=step.end_line,
                )
            ]
            out.append(step.model_copy(update={"evidence": ev}))
        else:
            out.append(step)
    return out, unresolved
