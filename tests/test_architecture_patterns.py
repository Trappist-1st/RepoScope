"""Tests for architecture pattern detection."""

from __future__ import annotations

from pathlib import Path

from app.intelligence.architecture.models import ArchitecturePatternKind
from app.intelligence.architecture.modules import discover_modules
from app.intelligence.architecture.patterns import detect_patterns
from app.intelligence.architecture.profile import build_repository_profile
from app.intelligence.enrichers.roles import build_role_index
from app.intelligence.ids import edge_id, symbol_ref_to_node_id
from app.intelligence.models import (
    EdgeType,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    NodeKind,
)
from tests.helpers_flow import SPRING_LOGIN, ingest_fixture, seed_spring_login_calls


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
        start_line=1,
        end_line=10,
        language="java",
        parent_id=parent,
    )


def _call(a: str, b: str) -> KnowledgeEdge:
    s, t = symbol_ref_to_node_id(a), symbol_ref_to_node_id(b)
    return KnowledgeEdge(
        id=edge_id(EdgeType.CALL.value, s, t),
        source_id=s,
        target_id=t,
        edge_type=EdgeType.CALL,
        confidence="high",
    )


def test_layered_pattern_on_csr_chain():
    pc, ps, pr = "auth/AuthController.java", "auth/AuthService.java", "user/UserRepository.java"
    ctrl = _sym(pc, "AuthController", NodeKind.CLASS)
    login_c = _sym(pc, "login", NodeKind.METHOD, parent=ctrl.id)
    svc = _sym(ps, "AuthService", NodeKind.CLASS)
    login_s = _sym(ps, "login", NodeKind.METHOD, parent=svc.id)
    repo = _sym(pr, "UserRepository", NodeKind.CLASS)
    find = _sym(pr, "findByUsername", NodeKind.METHOD, parent=repo.id)
    graph = KnowledgeGraph(
        repo_id="layer",
        nodes=[
            KnowledgeNode(
                id=f"file:{pc}", kind=NodeKind.FILE, name=pc, qualified_name=pc, file_path=pc, language="java"
            ),
            KnowledgeNode(
                id=f"file:{ps}", kind=NodeKind.FILE, name=ps, qualified_name=ps, file_path=ps, language="java"
            ),
            KnowledgeNode(
                id=f"file:{pr}", kind=NodeKind.FILE, name=pr, qualified_name=pr, file_path=pr, language="java"
            ),
            ctrl,
            login_c,
            svc,
            login_s,
            repo,
            find,
        ],
        edges=[
            _call(login_c.qualified_name, login_s.qualified_name),
            _call(login_s.qualified_name, find.qualified_name),
        ],
    )
    roles = build_role_index(graph)
    mmap = discover_modules(graph, role_index=roles)
    matches, primary, findings = detect_patterns(graph, mmap, role_index=roles)
    assert primary in {ArchitecturePatternKind.LAYERED, ArchitecturePatternKind.MVC}
    layered = next(m for m in matches if m.pattern == ArchitecturePatternKind.LAYERED)
    assert layered.score >= 0.45
    assert layered.evidence
    assert findings
    assert findings[0].evidence


def test_event_driven_with_kafka_profile(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "pom.xml").write_text(
        "<project><dependencies><dependency><artifactId>spring-kafka</artifactId></dependency></dependencies></project>",
        encoding="utf-8",
    )
    path = "messaging/OrderEventProducer.java"
    producer = _sym(path, "OrderEventProducer", NodeKind.CLASS)
    graph = KnowledgeGraph(
        repo_id="mq",
        nodes=[
            KnowledgeNode(
                id=f"file:{path}",
                kind=NodeKind.FILE,
                name=path,
                qualified_name=path,
                file_path=path,
                language="java",
            ),
            producer,
        ],
    )
    roles = build_role_index(graph)
    mmap = discover_modules(graph, role_index=roles)
    profile = build_repository_profile(graph, workspace_root=repo, module_map=mmap)
    matches, primary, _ = detect_patterns(
        graph, mmap, role_index=roles, profile=profile
    )
    ev = next(m for m in matches if m.pattern == ArchitecturePatternKind.EVENT_DRIVEN)
    assert ev.score >= 0.45
    assert ev.evidence


def test_hexagonal_low_on_classic_spring_fixture(tmp_path: Path):
    _, kg = ingest_fixture(SPRING_LOGIN, tmp_path)
    kg = seed_spring_login_calls(kg)
    roles = build_role_index(kg)
    mmap = discover_modules(kg, role_index=roles)
    matches, primary, _ = detect_patterns(kg, mmap, role_index=roles)
    hex_m = next(m for m in matches if m.pattern == ArchitecturePatternKind.HEXAGONAL)
    assert hex_m.score < 0.45
    assert primary in {
        ArchitecturePatternKind.LAYERED,
        ArchitecturePatternKind.MVC,
        ArchitecturePatternKind.UNKNOWN,
    }
