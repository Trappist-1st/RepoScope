"""Beam-search call-path discovery over KnowledgeGraph.

Not longest-path BFS: paths are ranked by topic relevance, architectural
layer progression, and edge confidence, with hard depth/branch caps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.intelligence.enrichers.roles import FlowRole, RoleIndex, role_of
from app.intelligence.models import (
    Confidence,
    EdgeType,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
)
from app.intelligence.query import get_node

# Noise callees to skip / heavily penalize
_NOISE_NAMES = {
    "tostring",
    "hashcode",
    "equals",
    "getclass",
    "clone",
    "finalize",
    "notify",
    "notifyall",
    "wait",
    "logger",
    "log",
    "debug",
    "info",
    "warn",
    "error",
    "print",
    "println",
}

# Preferred layer transitions (bonus when moving "down" the stack)
_LAYER_RANK: dict[FlowRole, int] = {
    FlowRole.CONTROLLER: 0,
    FlowRole.GATEWAY: 0,
    FlowRole.SERVICE: 1,
    FlowRole.MQ: 2,
    FlowRole.CACHE: 2,
    FlowRole.REPOSITORY: 3,
    FlowRole.DATABASE: 4,
    FlowRole.EXTERNAL: 4,
    FlowRole.UNKNOWN: 2,
}


@dataclass
class CandidatePath:
    """Ordered call path from an entry node."""

    node_ids: list[str]
    edge_ids: list[str]  # edge into node_ids[i+1] from node_ids[i]
    score: float
    reasons: list[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.node_ids)

    @property
    def terminal_id(self) -> str:
        return self.node_ids[-1]


@dataclass
class SearchLimits:
    max_depth: int = 5
    beam_width: int = 4
    max_branching: int = 8
    max_paths: int = 3


def build_call_adjacency(graph: KnowledgeGraph) -> dict[str, list[KnowledgeEdge]]:
    """node_id → outgoing call edges."""
    adj: dict[str, list[KnowledgeEdge]] = {}
    for e in graph.edges:
        if e.edge_type != EdgeType.CALL:
            continue
        adj.setdefault(e.source_id, []).append(e)
    return adj


def beam_search_paths(
    graph: KnowledgeGraph,
    entry_node_id: str,
    *,
    role_index: RoleIndex,
    topic_terms: list[str] | None = None,
    limits: SearchLimits | None = None,
    adjacency: dict[str, list[KnowledgeEdge]] | None = None,
) -> list[CandidatePath]:
    """
    Beam search from entry along call edges.

    Returns up to ``limits.max_paths`` ranked CandidatePath objects.
    """
    limits = limits or SearchLimits()
    terms = [t.lower() for t in (topic_terms or []) if t]
    adj = adjacency or build_call_adjacency(graph)

    entry = get_node(graph, entry_node_id)
    if entry is None:
        return []

    seed = CandidatePath(
        node_ids=[entry_node_id],
        edge_ids=[],
        score=0.5 + 0.2 * _topic_node_score(entry, terms),
        reasons=["seed_entry"],
    )
    frontier: list[CandidatePath] = [seed]
    completed: list[CandidatePath] = []

    for _depth in range(limits.max_depth):
        expansions: list[CandidatePath] = []
        for path in frontier:
            node_id = path.terminal_id
            outs = adj.get(node_id, [])
            ranked_edges = _rank_outgoing(
                graph, outs, role_index, terms, path, limits.max_branching
            )
            if not ranked_edges:
                # dead-end — keep as completed if length > 1
                if path.length > 1:
                    completed.append(path)
                continue

            extended_any = False
            for edge, step_score, step_reasons in ranked_edges:
                callee = edge.target_id
                if callee in path.node_ids:
                    continue  # cycle
                new_path = CandidatePath(
                    node_ids=[*path.node_ids, callee],
                    edge_ids=[*path.edge_ids, edge.id],
                    score=path.score + step_score,
                    reasons=[*path.reasons, *step_reasons],
                )
                expansions.append(new_path)
                extended_any = True
                # Early stop signal: reached repository-like sink with enough depth
                if _is_good_sink(graph, callee, role_index) and new_path.length >= 3:
                    completed.append(new_path)

            if not extended_any and path.length > 1:
                completed.append(path)

        if not expansions:
            break

        expansions.sort(key=lambda p: (-p.score, p.length, p.node_ids[-1]))
        frontier = expansions[: limits.beam_width]

        # Also keep frontier tips that already look complete
        for p in frontier:
            if _is_good_sink(graph, p.terminal_id, role_index) and p.length >= 3:
                completed.append(p)

    # Include remaining frontier as candidates
    for p in frontier:
        if p.length > 1:
            completed.append(p)

    return rank_paths(graph, completed, role_index, terms, max_paths=limits.max_paths)


def rank_paths(
    graph: KnowledgeGraph,
    paths: list[CandidatePath],
    role_index: RoleIndex,
    topic_terms: list[str],
    *,
    max_paths: int = 3,
) -> list[CandidatePath]:
    """Dedupe and re-score paths for final ranking."""
    seen: set[tuple[str, ...]] = set()
    scored: list[CandidatePath] = []
    for path in paths:
        key = tuple(path.node_ids)
        if key in seen or path.length < 2:
            continue
        seen.add(key)
        bonus, reasons = _path_quality_bonus(graph, path, role_index, topic_terms)
        scored.append(
            CandidatePath(
                node_ids=list(path.node_ids),
                edge_ids=list(path.edge_ids),
                score=path.score + bonus,
                reasons=[*path.reasons, *reasons],
            )
        )
    scored.sort(key=lambda p: (-p.score, p.length, p.node_ids[0]))
    return scored[: max(1, max_paths)] if scored else []


def path_confidence(
    graph: KnowledgeGraph,
    path: CandidatePath,
    role_index: RoleIndex,
) -> Confidence:
    if path.length < 2:
        return "low"
    edge_by_id = {e.id: e for e in graph.edges}
    confs: list[str] = []
    missing_lines = 0
    for eid in path.edge_ids:
        e = edge_by_id.get(eid)
        if e:
            confs.append(e.confidence)
    for nid in path.node_ids:
        n = get_node(graph, nid)
        if n is None or n.start_line is None:
            missing_lines += 1
    if missing_lines:
        return "low"
    if any(c == "low" for c in confs):
        return "low"
    roles = [role_of(role_index, nid) for nid in path.node_ids]
    layered = _has_downward_layers(roles)
    if all(c == "high" for c in confs) and layered:
        return "high"
    return "medium"


def _rank_outgoing(
    graph: KnowledgeGraph,
    edges: list[KnowledgeEdge],
    role_index: RoleIndex,
    terms: list[str],
    path: CandidatePath,
    max_branching: int,
) -> list[tuple[KnowledgeEdge, float, list[str]]]:
    scored: list[tuple[KnowledgeEdge, float, list[str]]] = []
    prev_role = role_of(role_index, path.terminal_id)
    for edge in edges:
        callee = get_node(graph, edge.target_id)
        if callee is None:
            continue
        if _is_noise_name(callee.name):
            continue
        step, reasons = _step_score(edge, callee, prev_role, role_index, terms)
        if step <= 0:
            continue
        scored.append((edge, step, reasons))
    scored.sort(key=lambda t: (-t[1], t[0].target_id))
    return scored[:max_branching]


def _step_score(
    edge: KnowledgeEdge,
    callee: KnowledgeNode,
    prev_role: FlowRole,
    role_index: RoleIndex,
    terms: list[str],
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.35  # base progress
    # edge confidence
    if edge.confidence == "high":
        score += 0.25
    elif edge.confidence == "medium":
        score += 0.1
        reasons.append("edge:medium")
    else:
        score -= 0.15
        reasons.append("edge:low")

    next_role = role_of(role_index, callee.id)
    # layer progression bonus
    prev_r = _LAYER_RANK.get(prev_role, 2)
    next_r = _LAYER_RANK.get(next_role, 2)
    if next_r > prev_r:
        score += 0.35
        reasons.append(f"layer:{prev_role.value}->{next_role.value}")
    elif next_r == prev_r and next_role != FlowRole.UNKNOWN:
        score += 0.05
    elif next_r < prev_r:
        score -= 0.2
        reasons.append("layer_upward_penalty")

    topic = _topic_node_score(callee, terms)
    score += 0.3 * topic
    if topic >= 0.85:
        reasons.append("topic_hit")

    # same module mild bonus
    if edge.meta.get("same_file"):
        score += 0.05

    return score, reasons


def _topic_node_score(node: KnowledgeNode, terms: list[str]) -> float:
    if not terms:
        return 0.0
    blob = f"{node.name} {node.qualified_name} {node.file_path or ''}".lower()
    best = 0.0
    for t in terms:
        if not t:
            continue
        if node.name.lower() == t:
            best = max(best, 1.0)
        elif t in node.name.lower():
            best = max(best, 0.85)
        elif t in blob:
            best = max(best, 0.5)
    return best


def _is_noise_name(name: str) -> bool:
    return name.lower() in _NOISE_NAMES


def _is_good_sink(graph: KnowledgeGraph, node_id: str, role_index: RoleIndex) -> bool:
    role = role_of(role_index, node_id)
    if role in {FlowRole.REPOSITORY, FlowRole.DATABASE, FlowRole.MQ, FlowRole.CACHE}:
        return True
    node = get_node(graph, node_id)
    if node and _is_noise_name(node.name):
        return False
    return False


def _has_downward_layers(roles: list[FlowRole]) -> bool:
    ranks = [_LAYER_RANK.get(r, 2) for r in roles]
    return any(ranks[i] < ranks[j] for i in range(len(ranks)) for j in range(i + 1, len(ranks)))


def _path_quality_bonus(
    graph: KnowledgeGraph,
    path: CandidatePath,
    role_index: RoleIndex,
    terms: list[str],
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    bonus = 0.0
    roles = [role_of(role_index, nid) for nid in path.node_ids]
    if FlowRole.CONTROLLER in roles and FlowRole.SERVICE in roles:
        bonus += 0.4
        reasons.append("pattern:controller-service")
    if FlowRole.SERVICE in roles and FlowRole.REPOSITORY in roles:
        bonus += 0.4
        reasons.append("pattern:service-repository")
    if FlowRole.CONTROLLER in roles and FlowRole.REPOSITORY in roles:
        bonus += 0.2

    topic_hits = 0
    for nid in path.node_ids:
        n = get_node(graph, nid)
        if n and _topic_node_score(n, terms) >= 0.5:
            topic_hits += 1
    if topic_hits:
        bonus += 0.15 * min(topic_hits, 3)
        reasons.append(f"topic_nodes:{topic_hits}")

    if 3 <= path.length <= 5:
        bonus += 0.15
        reasons.append("length_sweet_spot")
    elif path.length > 6:
        bonus -= 0.2
    return bonus, reasons
