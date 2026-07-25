"""Tests for beam-search flow path discovery."""

from __future__ import annotations

from app.intelligence.enrichers.roles import build_role_index
from app.intelligence.flow_search import (
    SearchLimits,
    beam_search_paths,
    path_confidence,
)
from app.intelligence.ids import edge_id, symbol_ref_to_node_id
from app.intelligence.models import (
    EdgeType,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    NodeKind,
)


def _sym(path: str, name: str, kind: NodeKind, parent: str | None = None) -> KnowledgeNode:
    if kind == NodeKind.METHOD and parent and "::" in parent:
        cls = parent.split("::", 1)[1]
        qn = f"{path}::{cls}.{name}"
    else:
        qn = f"{path}::{name}"
    return KnowledgeNode(
        id=symbol_ref_to_node_id(qn) if kind != NodeKind.FILE else f"file:{path}",
        kind=kind,
        name=name if kind != NodeKind.FILE else path,
        qualified_name=qn if kind != NodeKind.FILE else path,
        file_path=path,
        start_line=1,
        end_line=10,
        language="java",
        parent_id=parent,
    )


def _call(src: str, dst: str, same_file: bool = False) -> KnowledgeEdge:
    sid = symbol_ref_to_node_id(src) if not src.startswith("sym:") else src
    tid = symbol_ref_to_node_id(dst) if not dst.startswith("sym:") else dst
    # normalize to sym ids from qualified names
    if "::" in src and not src.startswith("sym:"):
        sid = symbol_ref_to_node_id(src)
    if "::" in dst and not dst.startswith("sym:"):
        tid = symbol_ref_to_node_id(dst)
    eid = edge_id(EdgeType.CALL.value, sid, tid)
    return KnowledgeEdge(
        id=eid,
        source_id=sid,
        target_id=tid,
        edge_type=EdgeType.CALL,
        confidence="high",
        meta={"same_file": same_file},
    )


def _layered_login_graph() -> tuple[KnowledgeGraph, str]:
    """Controller.login → Service.login → Repo.findByUsername (+ noise toString)."""
    pc = "auth/AuthController.java"
    ps = "auth/AuthService.java"
    pr = "user/UserRepository.java"
    util = "util/Strings.java"

    ctrl = _sym(pc, "AuthController", NodeKind.CLASS)
    ctrl_login = _sym(pc, "login", NodeKind.METHOD, parent=ctrl.id)
    svc = _sym(ps, "AuthService", NodeKind.CLASS)
    svc_login = _sym(ps, "login", NodeKind.METHOD, parent=svc.id)
    repo = _sym(pr, "UserRepository", NodeKind.CLASS)
    find = _sym(pr, "findByUsername", NodeKind.METHOD, parent=repo.id)
    noise_cls = _sym(util, "Strings", NodeKind.CLASS)
    tostring = _sym(util, "toString", NodeKind.METHOD, parent=noise_cls.id)

    nodes = [ctrl, ctrl_login, svc, svc_login, repo, find, noise_cls, tostring]
    edges = [
        _call(ctrl_login.qualified_name, svc_login.qualified_name),
        _call(svc_login.qualified_name, find.qualified_name),
        # tempting noise branch from controller
        _call(ctrl_login.qualified_name, tostring.qualified_name),
    ]
    graph = KnowledgeGraph(repo_id="beam", nodes=nodes, edges=edges)
    return graph, ctrl_login.id


def test_beam_finds_controller_service_repo_path():
    graph, entry = _layered_login_graph()
    roles = build_role_index(graph)
    paths = beam_search_paths(
        graph,
        entry,
        role_index=roles,
        topic_terms=["login", "auth", "user"],
        limits=SearchLimits(max_depth=5, beam_width=4, max_paths=3),
    )
    assert paths
    best = paths[0]
    assert entry in best.node_ids
    assert any("AuthService" in nid for nid in best.node_ids)
    assert any("findByUsername" in nid or "UserRepository" in nid for nid in best.node_ids)
    # noise toString should not win
    assert not any(nid.endswith("::toString") or nid.endswith(".toString") for nid in best.node_ids)
    assert path_confidence(graph, best, roles) in {"high", "medium"}


def test_beam_avoids_noise_only_path():
    graph, entry = _layered_login_graph()
    roles = build_role_index(graph)
    paths = beam_search_paths(
        graph,
        entry,
        role_index=roles,
        topic_terms=["login"],
        limits=SearchLimits(max_depth=3, beam_width=2, max_paths=3),
    )
    assert paths
    # top path should be longer layered path, not login→toString
    top_names = " ".join(paths[0].node_ids)
    assert "toString" not in top_names


def test_empty_when_entry_missing():
    graph, _ = _layered_login_graph()
    roles = build_role_index(graph)
    assert beam_search_paths(graph, "sym:missing::x", role_index=roles) == []


def test_no_outgoing_returns_empty():
    """Single node with no calls → no path of length>=2."""
    n = _sym("a/A.java", "alone", NodeKind.FUNCTION)
    graph = KnowledgeGraph(repo_id="x", nodes=[n], edges=[])
    roles = build_role_index(graph)
    assert beam_search_paths(graph, n.id, role_index=roles) == []
