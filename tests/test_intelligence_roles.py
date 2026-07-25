"""Tests for heuristic FlowRole enrichment."""

from __future__ import annotations

from app.intelligence.enrichers.roles import (
    FlowRole,
    attach_roles_to_meta,
    build_role_index,
    infer_role,
    role_of,
)
from app.intelligence.models import KnowledgeGraph, KnowledgeNode, NodeKind


def _node(
    *,
    id: str,
    kind: NodeKind,
    name: str,
    file_path: str,
    parent_id: str | None = None,
    language: str | None = "java",
) -> KnowledgeNode:
    if kind == NodeKind.FILE:
        qn = file_path
    elif parent_id and kind == NodeKind.METHOD:
        # parent class name from parent_id when present
        parent_name = parent_id.split("::")[-1] if "::" in parent_id else name
        qn = f"{file_path}::{parent_name}.{name}" if "." not in name else f"{file_path}::{name}"
        if "::" in (parent_id or "") and "." not in name:
            cls = parent_id.split("::", 1)[1]
            qn = f"{file_path}::{cls}.{name}"
    else:
        qn = f"{file_path}::{name}"
    return KnowledgeNode(
        id=id,
        kind=kind,
        name=name,
        qualified_name=qn,
        file_path=file_path,
        start_line=1,
        end_line=10,
        language=language,
        parent_id=parent_id,
    )


def test_infer_controller_by_class_name():
    n = _node(
        id="sym:auth/AuthController.java::AuthController",
        kind=NodeKind.CLASS,
        name="AuthController",
        file_path="auth/AuthController.java",
    )
    assert infer_role(n) == FlowRole.CONTROLLER


def test_infer_service_and_repository_by_name():
    svc = _node(
        id="sym:auth/AuthService.java::AuthService",
        kind=NodeKind.CLASS,
        name="AuthService",
        file_path="auth/AuthService.java",
    )
    repo = _node(
        id="sym:user/UserRepository.java::UserRepository",
        kind=NodeKind.CLASS,
        name="UserRepository",
        file_path="user/UserRepository.java",
    )
    assert infer_role(svc) == FlowRole.SERVICE
    assert infer_role(repo) == FlowRole.REPOSITORY


def test_infer_by_path_when_name_generic():
    n = _node(
        id="sym:app/repositories/user.py::UserStoreHelper",
        kind=NodeKind.CLASS,
        name="UserStoreHelper",  # no Repo suffix — path wins via stem? 
        file_path="app/repositories/user.py",
        language="python",
    )
    # name doesn't match; path segment repositories → repository
    assert infer_role(n) == FlowRole.REPOSITORY


def test_method_inherits_parent_class_role():
    graph = KnowledgeGraph(
        repo_id="t",
        nodes=[
            _node(
                id="file:auth/AuthController.java",
                kind=NodeKind.FILE,
                name="auth/AuthController.java",
                file_path="auth/AuthController.java",
            ),
            _node(
                id="sym:auth/AuthController.java::AuthController",
                kind=NodeKind.CLASS,
                name="AuthController",
                file_path="auth/AuthController.java",
                parent_id="file:auth/AuthController.java",
            ),
            _node(
                id="sym:auth/AuthController.java::AuthController.login",
                kind=NodeKind.METHOD,
                name="login",
                file_path="auth/AuthController.java",
                parent_id="sym:auth/AuthController.java::AuthController",
            ),
        ],
    )
    index = build_role_index(graph)
    assert role_of(index, "sym:auth/AuthController.java::AuthController") == FlowRole.CONTROLLER
    assert (
        role_of(index, "sym:auth/AuthController.java::AuthController.login")
        == FlowRole.CONTROLLER
    )


def test_java_annotation_hint():
    n = _node(
        id="sym:web/UserApi.java::UserApi",
        kind=NodeKind.CLASS,
        name="UserApi",  # no Controller suffix
        file_path="web/UserApi.java",
    )
    text = "@RestController\npublic class UserApi { }"
    assert infer_role(n, file_text=text) == FlowRole.CONTROLLER


def test_fastapi_router_function():
    n = _node(
        id="sym:app/api/auth.py::login",
        kind=NodeKind.FUNCTION,
        name="login",
        file_path="app/api/auth.py",
        language="python",
    )
    text = "from fastapi import APIRouter\nrouter = APIRouter()\n@router.post('/login')\ndef login():\n    ..."
    assert infer_role(n, file_text=text) == FlowRole.CONTROLLER


def test_mq_and_cache_by_name():
    mq = _node(
        id="sym:msg/OrderEventProducer.java::OrderEventProducer",
        kind=NodeKind.CLASS,
        name="OrderEventProducer",
        file_path="msg/OrderEventProducer.java",
    )
    cache = _node(
        id="sym:cache/UserCache.java::UserCache",
        kind=NodeKind.CLASS,
        name="UserCache",
        file_path="cache/UserCache.java",
    )
    assert infer_role(mq) == FlowRole.MQ
    assert infer_role(cache) == FlowRole.CACHE


def test_attach_roles_to_meta():
    graph = KnowledgeGraph(
        repo_id="t",
        nodes=[
            _node(
                id="sym:s/FooService.java::FooService",
                kind=NodeKind.CLASS,
                name="FooService",
                file_path="s/FooService.java",
            )
        ],
    )
    index = build_role_index(graph)
    enriched = attach_roles_to_meta(graph, index)
    assert enriched.nodes[0].meta.get("role") == "service"
    # original unchanged
    assert "role" not in graph.nodes[0].meta
