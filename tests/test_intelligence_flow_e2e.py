"""End-to-end Flow Trace tests on Spring / FastAPI fixtures."""

from __future__ import annotations

from pathlib import Path

from app.intelligence.enrichers.roles import FlowRole, build_role_index, role_of
from app.intelligence.flow_format import format_flow_markdown
from app.intelligence.flow_tracer import FlowTracer
from app.intelligence.ids import symbol_ref_to_node_id
from app.intelligence.query import find_by_qualified_name
from tests.helpers_flow import (
    FASTAPI_LOGIN,
    SPRING_LOGIN,
    ingest_fixture,
)


def _assert_step_locations(trace) -> None:
    for step in trace.steps:
        if step.is_synthetic:
            assert step.role in {FlowRole.DATABASE, FlowRole.MQ, FlowRole.CACHE}
            assert step.inference_reason
            continue
        assert step.symbol
        assert step.node_id
        assert step.file_path, step
        assert step.start_line is not None, step
        assert step.evidence, step
        assert step.reason
        assert step.inference_reason


def test_fastapi_login_flow_e2e(tmp_path: Path):
    _, kg = ingest_fixture(FASTAPI_LOGIN, tmp_path)
    # Real call edges expected via `from ... import login as auth_login`
    assert kg.stats.edge_counts.get("call", 0) >= 1

    roles = build_role_index(kg)
    api_login = find_by_qualified_name(kg, "app/api/auth.py::login")
    assert api_login is not None
    assert role_of(roles, api_login.id) in {
        FlowRole.CONTROLLER,
        FlowRole.UNKNOWN,
        FlowRole.SERVICE,
    }

    trace = FlowTracer().trace(kg, "用户登录流程是什么？")
    assert trace.steps, format_flow_markdown(trace)
    _assert_step_locations(trace)

    chain = " → ".join(s.symbol for s in trace.steps)
    assert "login" in chain.lower()
    # should reach repository layer or synthetic DB
    roles_in_trace = {s.role for s in trace.steps}
    assert FlowRole.REPOSITORY in roles_in_trace or FlowRole.DATABASE in roles_in_trace or any(
        "find_by_username" in s.symbol for s in trace.steps
    ), chain


def test_spring_login_flow_e2e(tmp_path: Path):
    _, kg = ingest_fixture(SPRING_LOGIN, tmp_path)

    # Nodes + line numbers from real AST ingest
    ctrl_login = find_by_qualified_name(
        kg, "auth/AuthController.java::AuthController.login"
    )
    svc_login = find_by_qualified_name(kg, "auth/AuthService.java::AuthService.login")
    find_user = find_by_qualified_name(
        kg, "user/UserRepository.java::UserRepository.findByUsername"
    )
    assert ctrl_login and svc_login and find_user
    assert ctrl_login.start_line and svc_login.start_line and find_user.start_line

    roles = build_role_index(kg)
    assert role_of(roles, ctrl_login.id) == FlowRole.CONTROLLER
    assert role_of(roles, svc_login.id) == FlowRole.SERVICE
    assert role_of(roles, find_user.id) == FlowRole.REPOSITORY

    # Cross-file Java field.method() edges now come from DependencyGraphBuilder
    from app.intelligence.models import EdgeType

    call_pairs = {
        (e.source_id, e.target_id) for e in kg.edges if e.edge_type == EdgeType.CALL
    }
    assert (ctrl_login.id, svc_login.id) in call_pairs
    assert (svc_login.id, find_user.id) in call_pairs

    trace = FlowTracer().trace(kg, "用户登录流程是什么？")
    assert trace.steps, format_flow_markdown(trace)
    _assert_step_locations(trace)

    assert trace.entry is not None
    assert trace.entry.role == FlowRole.CONTROLLER
    assert "AuthController" in (trace.entry.symbol or "")

    symbols = [s.symbol for s in trace.steps]
    assert any("AuthService" in s for s in symbols)
    assert any("findByUsername" in s for s in symbols)
    assert trace.steps[-1].role == FlowRole.DATABASE
    assert trace.steps[-1].is_synthetic

    md = format_flow_markdown(trace)
    assert "AuthController" in md
    assert ctrl_login.file_path in md
