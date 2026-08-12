"""Tests for Flow Entry Discovery."""

from __future__ import annotations

from app.intelligence.enrichers.roles import FlowRole, build_role_index
from app.intelligence.flow_entry import discover_entries
from app.intelligence.flow_topics import extract_topic, is_flow_question
from app.intelligence.models import KnowledgeGraph, KnowledgeNode, NodeKind


def _n(
    nid: str,
    kind: NodeKind,
    name: str,
    path: str,
    parent_id: str | None = None,
    language: str = "java",
) -> KnowledgeNode:
    if kind == NodeKind.FILE:
        qn = path
    elif kind == NodeKind.METHOD and parent_id and "::" in parent_id:
        cls = parent_id.split("::", 1)[1]
        qn = f"{path}::{cls}.{name}"
    else:
        qn = f"{path}::{name}"
    return KnowledgeNode(
        id=nid,
        kind=kind,
        name=name,
        qualified_name=qn,
        file_path=path,
        start_line=1,
        end_line=20,
        language=language,
        parent_id=parent_id,
    )


def _login_graph() -> KnowledgeGraph:
    file_c = "auth/AuthController.java"
    file_s = "auth/AuthService.java"
    file_r = "user/UserRepository.java"
    return KnowledgeGraph(
        repo_id="flow-login",
        nodes=[
            _n("file:" + file_c, NodeKind.FILE, file_c, file_c),
            _n(
                f"sym:{file_c}::AuthController",
                NodeKind.CLASS,
                "AuthController",
                file_c,
                parent_id="file:" + file_c,
            ),
            _n(
                f"sym:{file_c}::AuthController.login",
                NodeKind.METHOD,
                "login",
                file_c,
                parent_id=f"sym:{file_c}::AuthController",
            ),
            _n(
                f"sym:{file_c}::AuthController.health",
                NodeKind.METHOD,
                "health",
                file_c,
                parent_id=f"sym:{file_c}::AuthController",
            ),
            _n(
                f"sym:{file_s}::AuthService",
                NodeKind.CLASS,
                "AuthService",
                file_s,
            ),
            _n(
                f"sym:{file_s}::AuthService.login",
                NodeKind.METHOD,
                "login",
                file_s,
                parent_id=f"sym:{file_s}::AuthService",
            ),
            _n(
                f"sym:{file_r}::UserRepository",
                NodeKind.CLASS,
                "UserRepository",
                file_r,
            ),
            _n(
                f"sym:{file_r}::UserRepository.findByUsername",
                NodeKind.METHOD,
                "findByUsername",
                file_r,
                parent_id=f"sym:{file_r}::UserRepository",
            ),
        ],
    )


def test_extract_topic_login_zh():
    topic, terms = extract_topic("用户登录流程是什么？")
    assert topic == "login"
    assert "login" in terms
    assert "登录" in terms
    assert is_flow_question("用户登录流程是什么？")


def test_discover_prefers_controller_login():
    graph = _login_graph()
    roles = build_role_index(graph)
    hits = discover_entries(graph, "用户登录流程是什么？", role_index=roles, top_k=3)
    assert hits
    top = hits[0]
    assert top.node.name == "login"
    assert top.role == FlowRole.CONTROLLER
    assert "AuthController" in top.node.qualified_name
    # service login should rank below controller login
    ids = [h.node_id for h in hits]
    ctrl = f"sym:auth/AuthController.java::AuthController.login"
    svc = f"sym:auth/AuthService.java::AuthService.login"
    assert ctrl in ids
    if svc in ids:
        assert ids.index(ctrl) < ids.index(svc)


def test_discover_demotes_health_and_prefers_api_path_login():
    """Same-named service login must lose to /api/ login; health is not an entry."""
    api = "app/api/auth.py"
    svc = "app/services/auth_service.py"
    graph = KnowledgeGraph(
        repo_id="py-login",
        nodes=[
            _n(f"sym:{api}::login", NodeKind.FUNCTION, "login", api, language="python"),
            _n(f"sym:{api}::health", NodeKind.FUNCTION, "health", api, language="python"),
            _n(f"sym:{svc}::login", NodeKind.FUNCTION, "login", svc, language="python"),
        ],
        edges=[],
    )
    roles = build_role_index(graph)
    hits = discover_entries(graph, "用户登录流程是什么？", role_index=roles, top_k=5)
    assert hits
    assert hits[0].node.file_path == api
    assert hits[0].node.name == "login"
    assert all(h.node.name != "health" for h in hits)


def test_entry_hint_boosts_service():
    graph = _login_graph()
    roles = build_role_index(graph)
    hits = discover_entries(
        graph,
        "login flow",
        role_index=roles,
        entry_hint="AuthService.login",
        top_k=3,
    )
    assert hits
    assert "AuthService.login" in hits[0].node.qualified_name


def test_retrieval_fallback_boost():
    graph = _login_graph()
    roles = build_role_index(graph)

    class _Cite:
        file_path = "auth/AuthController.java"

    class _Hit:
        symbol_name = "login"
        citation = _Cite()

    hits = discover_entries(
        graph,
        "authenticate user",
        role_index=roles,
        retrieve_fn=lambda q: [_Hit()],
        top_k=3,
    )
    assert hits
    assert hits[0].node.name == "login"
    assert any(r.startswith("retrieval:") for r in hits[0].reasons)


def test_repository_not_preferred_entry_for_login():
    graph = _login_graph()
    roles = build_role_index(graph)
    hits = discover_entries(graph, "登录流程", role_index=roles, top_k=5)
    assert hits[0].role != FlowRole.REPOSITORY


def _schedule_graph() -> KnowledgeGraph:
    file_auth = "auth/AuthController.java"
    file_task = "task/TaskSubmitController.java"
    file_svc = "task/TaskSubmitServiceImpl.java"
    return KnowledgeGraph(
        repo_id="flow-schedule",
        nodes=[
            _n("file:" + file_auth, NodeKind.FILE, file_auth, file_auth),
            _n(
                f"sym:{file_auth}::AuthController",
                NodeKind.CLASS,
                "AuthController",
                file_auth,
                parent_id="file:" + file_auth,
            ),
            _n(
                f"sym:{file_auth}::AuthController.login",
                NodeKind.METHOD,
                "login",
                file_auth,
                parent_id=f"sym:{file_auth}::AuthController",
            ),
            _n(
                f"sym:{file_auth}::AuthController.AuthController",
                NodeKind.METHOD,
                "AuthController",
                file_auth,
                parent_id=f"sym:{file_auth}::AuthController",
            ),
            _n("file:" + file_task, NodeKind.FILE, file_task, file_task),
            _n(
                f"sym:{file_task}::TaskSubmitController",
                NodeKind.CLASS,
                "TaskSubmitController",
                file_task,
                parent_id="file:" + file_task,
            ),
            _n(
                f"sym:{file_task}::TaskSubmitController.submitTask",
                NodeKind.METHOD,
                "submitTask",
                file_task,
                parent_id=f"sym:{file_task}::TaskSubmitController",
            ),
            _n(
                f"sym:{file_svc}::TaskSubmitServiceImpl.submitTask",
                NodeKind.METHOD,
                "submitTask",
                file_svc,
            ),
        ],
    )


def test_extract_topic_schedule_zh():
    topic, terms = extract_topic("任务调度的流程是什么？")
    assert topic == "task_schedule"
    assert "调度" in terms
    assert "task" in terms
    assert is_flow_question("任务调度的流程是什么？")


def test_discover_prefers_task_submit_over_auth():
    graph = _schedule_graph()
    roles = build_role_index(graph)
    hits = discover_entries(graph, "任务调度的流程是什么？", role_index=roles, top_k=5)
    assert hits
    assert hits[0].node.name == "submitTask"
    assert "TaskSubmitController" in hits[0].node.qualified_name
    assert all("AuthController" not in h.node.qualified_name for h in hits)


def test_constructor_not_entry_candidate():
    graph = _schedule_graph()
    roles = build_role_index(graph)
    hits = discover_entries(
        graph,
        "login",
        role_index=roles,
        entry_hint="AuthController.AuthController",
        top_k=5,
    )
    assert all(h.node.name != "AuthController" or h.node.kind != NodeKind.METHOD for h in hits)
