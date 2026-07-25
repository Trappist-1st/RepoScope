"""Tests for FlowTracer orchestration."""

from __future__ import annotations

from app.intelligence.enrichers.roles import FlowRole
from app.intelligence.flow_format import format_flow_markdown
from app.intelligence.flow_tracer import FlowTracer
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
        id=symbol_ref_to_node_id(qn),
        kind=kind,
        name=name,
        qualified_name=qn,
        file_path=path,
        start_line=10,
        end_line=30,
        language="java",
        parent_id=parent,
    )


def _call(src_qn: str, dst_qn: str) -> KnowledgeEdge:
    sid = symbol_ref_to_node_id(src_qn)
    tid = symbol_ref_to_node_id(dst_qn)
    return KnowledgeEdge(
        id=edge_id(EdgeType.CALL.value, sid, tid),
        source_id=sid,
        target_id=tid,
        edge_type=EdgeType.CALL,
        confidence="high",
    )


def _login_kg() -> KnowledgeGraph:
    pc, ps, pr = (
        "auth/AuthController.java",
        "auth/AuthService.java",
        "user/UserRepository.java",
    )
    ctrl = _sym(pc, "AuthController", NodeKind.CLASS)
    login_c = _sym(pc, "login", NodeKind.METHOD, parent=ctrl.id)
    svc = _sym(ps, "AuthService", NodeKind.CLASS)
    login_s = _sym(ps, "login", NodeKind.METHOD, parent=svc.id)
    repo = _sym(pr, "UserRepository", NodeKind.CLASS)
    find = _sym(pr, "findByUsername", NodeKind.METHOD, parent=repo.id)
    return KnowledgeGraph(
        repo_id="tracer-login",
        commit_hash="abc",
        nodes=[ctrl, login_c, svc, login_s, repo, find],
        edges=[
            _call(login_c.qualified_name, login_s.qualified_name),
            _call(login_s.qualified_name, find.qualified_name),
        ],
    )


def test_tracer_login_flow_with_synthetic_db():
    graph = _login_kg()
    trace = FlowTracer().trace(graph, "用户登录流程是什么？")
    assert trace.steps
    assert trace.entry is not None
    assert trace.entry.role == FlowRole.CONTROLLER
    symbols = [s.symbol for s in trace.steps]
    assert any("AuthController" in s and "login" in s for s in symbols)
    assert any("AuthService" in s for s in symbols)
    assert any("findByUsername" in s or "UserRepository" in s for s in symbols)
    # synthetic database terminal
    assert trace.steps[-1].role == FlowRole.DATABASE
    assert trace.steps[-1].is_synthetic
    assert trace.steps[-1].inference_reason

    for step in trace.steps:
        if step.is_synthetic:
            continue
        assert step.file_path
        assert step.start_line is not None
        assert step.evidence
        assert step.reason
        assert step.inference_reason

    md = format_flow_markdown(trace)
    assert "Flow Trace" in md
    assert "Database" in md or "persistent" in md


def test_tracer_empty_on_unknown_topic_graph():
    n = KnowledgeNode(
        id="sym:x.py::foo",
        kind=NodeKind.FUNCTION,
        name="foo",
        qualified_name="x.py::foo",
        file_path="x.py",
        start_line=1,
        end_line=2,
        language="python",
    )
    graph = KnowledgeGraph(repo_id="empty", nodes=[n], edges=[])
    trace = FlowTracer().trace(graph, "支付流程怎么走？")
    # may find weak entry or none; should not crash
    assert trace.meta.repo_id == "empty"
    assert isinstance(trace.unresolved, list)
