from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.audit import InMemoryAgentRunStore, InMemoryRunStateCache
from app.mcp.service import RepoScopeFacade
from tests.conftest import SAMPLE_REPO
from tests.helpers_flow import FASTAPI_LOGIN, SPRING_LOGIN


def _facade(tmp_path: Path) -> RepoScopeFacade:
    return RepoScopeFacade(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "art",
        audit_store=InMemoryAgentRunStore(),
        state_cache=InMemoryRunStateCache(),
        use_hash_embedder=True,
    )


def test_mcp_meta_warns_on_in_memory_audit(tmp_path: Path):
    facade = _facade(tmp_path)
    warnings = facade._audit_warnings()
    assert any("audit_backend: in_memory" in w for w in warnings)
    assert any("run_state_cache: in_memory" in w for w in warnings)


def test_query_dependencies_notes_on_ambiguous_symbol(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.query_dependencies(str(SAMPLE_REPO), symbol_name="greet", direction="both")
    assert result.meta.indexing_status in {"cached", "incremental", "full_reindex"}
    assert any("audit_backend: in_memory" in w for w in result.meta.warnings)
    assert result.meta.run_id
    assert "resolved_refs" in result.query
    if len(result.query["resolved_refs"]) > 1:
        assert any("Multiple symbols" in n for n in result.notes)


def test_get_repo_summary_has_indexing_status(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.get_repo_summary(str(SAMPLE_REPO))
    assert result.meta.indexing_status in {"cached", "incremental", "full_reindex"}
    assert result.report_markdown
    assert result.meta.audit_backend == "in_memory"
    assert facade.audit_store.get(result.meta.run_id) is not None


def test_trace_flow_fastapi_fixture(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.trace_flow(
        str(FASTAPI_LOGIN),
        "用户登录流程是什么？",
        use_retrieval=False,
    )
    assert result.meta.run_id
    assert result.meta.indexing_status in {"cached", "incremental", "full_reindex"}
    assert result.report_markdown
    assert "Flow Trace" in result.report_markdown
    steps = result.trace.get("steps") or []
    assert steps, result.report_markdown
    for step in steps:
        if step.get("is_synthetic"):
            continue
        assert step.get("file_path")
        assert step.get("start_line") is not None
    record = facade.audit_store.get(result.meta.run_id)
    assert record is not None
    assert record.intent == "trace"


def test_health_and_sse_stream(tmp_path: Path):
    app = create_app()
    facade = _facade(tmp_path)
    with TestClient(app) as client:
        app.state.facade = facade
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["ok"] is True
        assert any("audit_backend: in_memory" in w for w in body["warnings"])

        with client.stream(
            "POST",
            "/analyze/stream",
            json={
                "question": "Summarize architecture",
                "repo_source": str(SAMPLE_REPO),
                "intent_hint": "summary",
            },
        ) as resp:
            assert resp.status_code == 200
            text = "".join(resp.iter_text())
            assert "run_started" in text
            assert "done" in text
            assert "data:" in text


def test_http_post_trace(tmp_path: Path):
    app = create_app()
    facade = _facade(tmp_path)
    with TestClient(app) as client:
        app.state.facade = facade
        resp = client.post(
            "/trace",
            json={
                "question": "用户登录流程是什么？",
                "repo_source": str(FASTAPI_LOGIN),
                "max_depth": 5,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report_markdown"]
        assert body["trace"]["steps"]
        assert body["meta"]["run_id"]


def test_analyze_architecture_spring_fixture(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.analyze_architecture(str(SPRING_LOGIN))
    assert result.meta.run_id
    assert result.report_markdown
    assert "Architecture Report" in result.report_markdown
    assert result.finding_count >= 1
    assert result.report.get("modules", {}).get("modules")
    for f in result.report.get("findings") or []:
        assert f.get("evidence"), f.get("title")
    record = facade.audit_store.get(result.meta.run_id)
    assert record is not None
    assert record.intent == "architecture"


def test_http_post_architecture(tmp_path: Path):
    app = create_app()
    facade = _facade(tmp_path)
    with TestClient(app) as client:
        app.state.facade = facade
        resp = client.post(
            "/architecture",
            json={"repo_source": str(SPRING_LOGIN)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["report_markdown"]
        assert body["primary_pattern"]
        assert body["meta"]["run_id"]
        assert body["finding_count"] >= 1
