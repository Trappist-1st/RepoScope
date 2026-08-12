"""Entry Point Discovery for Flow Trace (no LLM by default).

Score fusion:
  0.35 name/topic match
+ 0.25 path match
+ 0.20 role bonus (controller > gateway > service > ...)
+ 0.15 retrieval fallback (optional)
+ 0.05 entry-likeness (low fan-in / handler-ish name)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.intelligence.enrichers.roles import FlowRole, RoleIndex, role_of
from app.intelligence.flow_topics import (
    extract_topic,
    normalize_entry_hint,
)
from app.intelligence.ids import symbol_ref_to_node_id
from app.intelligence.models import EdgeType, KnowledgeGraph, KnowledgeNode, NodeKind
from app.intelligence.query import get_node


class RetrievalHitLike(Protocol):
    symbol_name: str | None

    @property
    def citation(self) -> object: ...


@dataclass
class EntryCandidate:
    node_id: str
    node: KnowledgeNode
    score: float
    role: FlowRole
    reasons: list[str] = field(default_factory=list)
    topic: str | None = None
    topic_terms: list[str] = field(default_factory=list)


# Optional: (query: str) -> sequence of hits with citation.file_path + symbol_name
RetrieveFn = Callable[[str], list]


_ROLE_BONUS: dict[FlowRole, float] = {
    FlowRole.CONTROLLER: 1.0,
    FlowRole.GATEWAY: 0.85,
    FlowRole.SERVICE: 0.55,
    FlowRole.UNKNOWN: 0.25,
    FlowRole.REPOSITORY: 0.1,
    FlowRole.CACHE: 0.1,
    FlowRole.MQ: 0.15,
    FlowRole.DATABASE: 0.0,
    FlowRole.EXTERNAL: 0.0,
}

_HANDLER_NAMES = {
    "login",
    "logout",
    "signin",
    "sign_in",
    "authenticate",
    "register",
    "signup",
    "create",
    "checkout",
    "pay",
    "payment",
    "submit",
    "submit_task",
    "submittask",
    "schedule",
    "dispatch",
    "execute",
    "run",
    "start",
    "trigger",
    "agentroute",
    "executetools",
    "compilecontext",
}

# Names that look like handlers but are rarely business entry points.
_ENTRY_DEMOTE_NAMES = frozenset(
    {
        "health",
        "forbidden",
        "unauthorized",
        "ping",
        "ready",
        "liveness",
        "readiness",
        "tostring",
        "hashcode",
        "equals",
    }
)


def discover_entries(
    graph: KnowledgeGraph,
    question: str,
    *,
    role_index: RoleIndex,
    entry_hint: str | None = None,
    top_k: int = 5,
    retrieve_fn: RetrieveFn | None = None,
    language_prefer: list[str] | None = None,
) -> list[EntryCandidate]:
    """
    Rank candidate entry symbols (function/method preferred).

    Works offline with heuristics only; pass retrieve_fn for RAG boost.
    """
    topic, terms = extract_topic(question)
    hint = normalize_entry_hint(entry_hint)
    fan_in = _call_fan_in(graph)
    retrieval_boost = _retrieval_boost_map(graph, question, terms, retrieve_fn)
    require_topic_affinity = bool(terms) and not hint

    candidates: list[EntryCandidate] = []
    for node in graph.nodes:
        if node.kind not in {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS}:
            continue
        if language_prefer and node.language and node.language not in language_prefer:
            continue
        if _is_constructor(node):
            continue
        if node.name.lower().replace("_", "") in _ENTRY_DEMOTE_NAMES:
            continue

        role = role_of(role_index, node.id)
        name_s, name_reasons = _name_match_score(node, terms)
        path_s, path_reasons = _path_match_score(node, terms)
        role_s = _ROLE_BONUS.get(role, 0.25)
        ret_s = retrieval_boost.get(node.id, 0.0)
        entry_s, entry_reasons = _entry_likeness(node, role, fan_in.get(node.id, 0))

        # When the question has topic terms, ignore pure role/controller fallbacks.
        # Structural path_layer bonuses do NOT count as topic affinity.
        term_path_s = 0.9 if any(r.startswith("path:") for r in path_reasons) else 0.0
        topic_affinity = max(name_s, term_path_s, ret_s)
        if require_topic_affinity and topic_affinity < 0.35:
            continue

        score = (
            0.35 * name_s
            + 0.25 * path_s
            + 0.20 * role_s
            + 0.15 * ret_s
            + 0.05 * entry_s
        )
        reasons = name_reasons + path_reasons + entry_reasons
        if role != FlowRole.UNKNOWN:
            reasons.append(f"role:{role.value}")
        if ret_s > 0:
            reasons.append(f"retrieval:{ret_s:.2f}")
        if topic:
            reasons.append(f"topic:{topic}")

        if hint:
            hint_boost, hint_reason = _hint_boost(node, hint)
            score += hint_boost
            if hint_reason:
                reasons.append(hint_reason)

        # Drop near-zero noise unless hint forced
        if score < 0.08 and not (hint and any(r.startswith("hint:") for r in reasons)):
            continue

        candidates.append(
            EntryCandidate(
                node_id=node.id,
                node=node,
                score=score,
                role=role,
                reasons=reasons,
                topic=topic,
                topic_terms=list(terms),
            )
        )

    candidates.sort(key=lambda c: (-c.score, -_action_rank(c.node.name), c.node_id))

    # Prefer methods/functions over classes when scores are close
    candidates = _prefer_callable_over_class(candidates)

    # Prefer HTTP/controller handlers over same-named service methods
    candidates = _prefer_controller_over_service(candidates)

    # If class won but has a topic-matching method child, promote child
    candidates = _promote_matching_methods(graph, candidates, terms, role_index, topic)

    return candidates[: max(1, top_k)] if candidates else []


def _action_rank(name: str) -> int:
    """Tie-break: prefer submit/schedule/login handlers over CRUD/batch variants."""
    n = (name or "").lower().replace("_", "")
    if n in {"submittask", "login", "logout", "schedule", "dispatch", "authenticate"}:
        return 4
    if n.startswith(("submit", "schedule", "dispatch", "execute", "trigger")):
        return 3
    if any(n.startswith(p) for p in _CRUD_NAME_PREFIXES):
        return 0
    return 1


def _is_constructor(node: KnowledgeNode) -> bool:
    """Java-style Class.Class / ClassName matching parent class name."""
    if node.kind != NodeKind.METHOD:
        return False
    class_name = None
    if node.parent_id and "::" in node.parent_id:
        class_name = node.parent_id.split("::", 1)[1]
    elif "::" in node.qualified_name:
        sym = node.qualified_name.split("::", 1)[1]
        if "." in sym:
            class_name = sym.split(".", 1)[0]
    if class_name and node.name == class_name:
        return True
    # also Class.Class in qualified_name
    if "::" in node.qualified_name:
        sym = node.qualified_name.split("::", 1)[1]
        if "." in sym:
            cls, meth = sym.split(".", 1)
            if cls == meth:
                return True
    return False


def _call_fan_in(graph: KnowledgeGraph) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in graph.edges:
        if e.edge_type != EdgeType.CALL:
            continue
        counts[e.target_id] = counts.get(e.target_id, 0) + 1
    return counts


# Generic lexicon terms that match too many symbols; alone they should not dominate.
_WEAK_TERMS = frozenset(
    {
        "task",
        "job",
        "user",
        "auth",
        "order",
        "pay",
        "api",
        "service",
        "controller",
        "flow",
        "提交",
        "任务",
        "作业",
        "用户",
    }
)

_CRUD_NAME_PREFIXES = (
    "get",
    "list",
    "find",
    "query",
    "count",
    "update",
    "delete",
    "remove",
    "fetch",
)


def _name_match_score(node: KnowledgeNode, terms: list[str]) -> tuple[float, list[str]]:
    if not terms:
        return 0.0, []
    name_l = node.name.lower()
    qn_l = node.qualified_name.lower()
    blob = f"{name_l} {qn_l}"
    reasons: list[str] = []
    hit_strengths: list[float] = []
    hit_terms: list[str] = []
    for term in terms:
        t = term.lower()
        if not t or t not in blob:
            continue
        # exact name hit ranks higher than class path hit
        if name_l == t or name_l.replace("_", "") == t.replace("_", ""):
            strength = 1.0
            reasons.append(f"name_exact:{term}")
        elif t in name_l:
            strength = 0.85
            reasons.append(f"name_substr:{term}")
        else:
            strength = 0.55
            reasons.append(f"qualified_substr:{term}")
        # weak/generic terms alone are softer; still useful with multi-hit
        if t in _WEAK_TERMS and strength < 1.0:
            strength = min(strength, 0.65)
        hit_strengths.append(strength)
        hit_terms.append(t)

    if not hit_strengths:
        return 0.0, []

    best = max(hit_strengths)
    # Distinct term hits (e.g. submit+task) beat single weak hit (task alone)
    unique = list(dict.fromkeys(hit_terms))
    if len(unique) >= 2:
        best = min(1.0, best + 0.10 * (len(unique) - 1))
        reasons.append(f"name_multi:{len(unique)}")
    # Prefer action handlers over CRUD when both match the topic
    if any(name_l.startswith(p) for p in _CRUD_NAME_PREFIXES):
        best = max(0.0, best - 0.12)
        reasons.append("name_crud_penalty")
    return best, reasons[:5]


def _path_match_score(node: KnowledgeNode, terms: list[str]) -> tuple[float, list[str]]:
    path = (node.file_path or "").replace("\\", "/").lower()
    if not path:
        return 0.0, []
    reasons: list[str] = []
    best = 0.0
    for term in terms:
        t = term.lower()
        if not t or t not in path:
            continue
        # Long / specific path terms beat package-root generics (task, job…)
        if t in _WEAK_TERMS:
            strength = 0.45
        elif len(t) >= 8:
            strength = 0.95
        else:
            strength = 0.75
        if strength > best:
            best = strength
            reasons.append(f"path:{term}")
    # structural path bonus for API layers (topic-agnostic mild boost via role path)
    if any(
        seg in path
        for seg in (
            "/controller",
            "/api/",
            "/web/",
            "/routes/",
            "/handlers/",
            "/orchestrator/",
            "/graph/",
            "/runtime/",
        )
    ):
        best = max(best, 0.40)
        reasons.append("path_layer:api")
    return best, reasons[:3]


def _entry_likeness(
    node: KnowledgeNode,
    role: FlowRole,
    fan_in: int,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    if node.kind in {NodeKind.FUNCTION, NodeKind.METHOD}:
        score += 0.4
    if node.name.lower() in _HANDLER_NAMES or node.name.lower().startswith(
        ("get_", "post_", "create_", "login")
    ):
        score += 0.4
        reasons.append("handler_name")
    if role == FlowRole.CONTROLLER:
        score += 0.3
    # low fan-in → more entry-like
    if fan_in == 0:
        score += 0.3
        reasons.append("fan_in:0")
    elif fan_in == 1:
        score += 0.15
    return min(score, 1.0), reasons


def _hint_boost(node: KnowledgeNode, hint: str) -> tuple[float, str | None]:
    h = hint.lower()
    qn = node.qualified_name.lower()
    nid = node.id.lower()
    name = node.name.lower()
    # full symbol_ref or node id
    if h in qn or h in nid or symbol_ref_to_node_id(hint).lower() == nid:
        return 0.5, f"hint:exact:{hint}"
    # Class.method short form
    if "." in hint and hint.split(".")[-1].lower() == name:
        cls = hint.split(".")[0].lower()
        if cls in qn:
            return 0.45, f"hint:class_method:{hint}"
    if h == name or h in name:
        return 0.25, f"hint:name:{hint}"
    return 0.0, None


def _retrieval_boost_map(
    graph: KnowledgeGraph,
    question: str,
    terms: list[str],
    retrieve_fn: RetrieveFn | None,
) -> dict[str, float]:
    if retrieve_fn is None:
        return {}
    query = " ".join(dict.fromkeys([*terms[:6], "controller", "handler", "endpoint", question]))[
        :180
    ]
    try:
        hits = retrieve_fn(query) or []
    except Exception:
        return {}

    boost: dict[str, float] = {}
    for i, hit in enumerate(hits[:12]):
        sym = getattr(hit, "symbol_name", None)
        citation = getattr(hit, "citation", None)
        file_path = getattr(citation, "file_path", None) if citation is not None else None
        if not file_path:
            continue
        file_path = str(file_path).replace("\\", "/")
        # Skip docs/markdown hits — they pollute entry discovery for code flows.
        if _is_doc_path(file_path):
            continue
        # map to nodes
        matched_ids: list[str] = []
        if sym:
            # try file::symbol and file::Class.symbol variants already in KG
            needle = f"{file_path}::{sym}"
            node = get_node(graph, symbol_ref_to_node_id(needle))
            if node:
                matched_ids.append(node.id)
            else:
                for n in graph.nodes:
                    if n.file_path == file_path and (
                        n.name == sym or (n.qualified_name.endswith(f".{sym}"))
                    ):
                        matched_ids.append(n.id)
        else:
            for n in graph.nodes:
                if n.file_path == file_path and n.kind in {
                    NodeKind.FUNCTION,
                    NodeKind.METHOD,
                    NodeKind.CLASS,
                }:
                    matched_ids.append(n.id)
        # decaying boost by rank
        rank_score = max(0.2, 1.0 - i * 0.07)
        for nid in matched_ids:
            boost[nid] = max(boost.get(nid, 0.0), rank_score)
    return boost


def _is_doc_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    if p.startswith("docs/") or "/docs/" in p:
        return True
    return p.endswith((".md", ".markdown", ".rst", ".txt", ".adoc"))


def _prefer_callable_over_class(candidates: list[EntryCandidate]) -> list[EntryCandidate]:
    if len(candidates) < 2:
        return candidates
    top = candidates[0]
    if top.node.kind != NodeKind.CLASS:
        return candidates
    for alt in candidates[1:]:
        if alt.node.kind in {NodeKind.FUNCTION, NodeKind.METHOD} and top.score - alt.score <= 0.12:
            # swap: put callable first
            rest = [c for c in candidates if c.node_id != alt.node_id]
            return [alt, *[c for c in rest if c.node_id != alt.node_id]]
    return candidates


def _prefer_controller_over_service(candidates: list[EntryCandidate]) -> list[EntryCandidate]:
    """When same short name appears as controller/API and service, prefer the HTTP entry."""
    if len(candidates) < 2:
        return candidates
    top = candidates[0]
    if top.role not in {FlowRole.SERVICE, FlowRole.UNKNOWN, FlowRole.REPOSITORY}:
        return candidates
    top_name = (top.node.name or "").lower()
    for alt in candidates[1:6]:
        if (alt.node.name or "").lower() != top_name:
            continue
        if alt.role != FlowRole.CONTROLLER and not _looks_like_api_path(alt.node.file_path):
            continue
        if top.score - alt.score > 0.20:
            continue
        alt.reasons = [*alt.reasons, "promoted_controller_over_service"]
        rest = [c for c in candidates if c.node_id != alt.node_id]
        return [alt, *rest]
    return candidates


def _looks_like_api_path(path: str | None) -> bool:
    p = (path or "").replace("\\", "/").lower()
    return any(seg in p for seg in ("/api/", "/controller", "/routes/", "/handlers/", "/web/"))


def _promote_matching_methods(
    graph: KnowledgeGraph,
    candidates: list[EntryCandidate],
    terms: list[str],
    role_index: RoleIndex,
    topic: str | None,
) -> list[EntryCandidate]:
    if not candidates:
        return candidates
    top = candidates[0]
    if top.node.kind != NodeKind.CLASS:
        return candidates

    children = [
        n
        for n in graph.nodes
        if n.parent_id == top.node_id and n.kind == NodeKind.METHOD
    ]
    best_child: KnowledgeNode | None = None
    best_score = -1.0
    for ch in children:
        s, _ = _name_match_score(ch, terms)
        if s > best_score:
            best_score = s
            best_child = ch
    if best_child is None or best_score < 0.5:
        return candidates

    role = role_of(role_index, best_child.id)
    promoted = EntryCandidate(
        node_id=best_child.id,
        node=best_child,
        score=top.score + 0.05,
        role=role if role != FlowRole.UNKNOWN else top.role,
        reasons=[*top.reasons, "promoted_from_class", f"method_topic:{best_score:.2f}"],
        topic=topic,
        topic_terms=list(terms),
    )
    rest = [c for c in candidates if c.node_id != promoted.node_id]
    return [promoted, *rest]
