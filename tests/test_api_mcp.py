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


def test_search_code_returns_ranked_hits(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.search_code(str(SAMPLE_REPO), query="greet", top_k=5)
    assert result.meta.run_id
    assert result.meta.indexing_status in {"cached", "incremental", "full_reindex"}
    assert result.hits
    top = result.hits[0]
    assert top.citation.file_path
    assert top.citation.start_line >= 1


def test_search_code_falls_back_to_explore_on_zero_hits(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.search_code(str(SAMPLE_REPO), query="zzz_no_such_term_xyz", top_k=3)
    assert result.meta.run_id
    # hybrid search over a hash embedder rarely yields literally zero hits,
    # but when it does, explore() must still return cited chunks with a note.
    if any("falling back" in n for n in result.notes):
        assert result.hits


def test_view_source_by_symbol(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.view_source(
        str(SAMPLE_REPO), file_path="py_pkg/a.py", symbol_name="greet"
    )
    assert result.meta.run_id
    assert "def greet" in result.content
    assert result.citation is not None
    assert result.citation.file_path == "py_pkg/a.py"
    assert not result.outline  # symbol hit -> no outline needed


def test_view_source_whole_file_has_outline(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.view_source(str(SAMPLE_REPO), file_path="py_pkg/a.py")
    assert result.content
    assert "greet" in result.content
    names = {d.name for d in result.outline}
    assert "greet" in names
    assert "Helper" in names


def test_view_source_line_range(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.view_source(
        str(SAMPLE_REPO), file_path="py_pkg/a.py", start_line=1, end_line=2
    )
    assert result.content
    assert result.citation is not None
    assert result.citation.start_line == 1
    assert result.citation.end_line == 2


def test_view_source_unknown_symbol_falls_back_to_file(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.view_source(
        str(SAMPLE_REPO), file_path="py_pkg/a.py", symbol_name="does_not_exist"
    )
    assert result.content  # falls back to whole-file view
    assert any("not found" in n for n in result.notes)
    assert result.outline


def test_get_initial_context_structure(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.get_initial_context(str(SAMPLE_REPO))
    assert result.meta.run_id
    assert result.languages  # python at least
    # no README in the fixture repo -> explicit warning, not a silent gap
    assert result.readme_path is None
    assert any("README" in w for w in result.meta.warnings)
    assert result.core_modules or result.remaining_modules
    for cf in result.core_files:
        assert cf.file_path
        assert cf.content


def test_get_initial_context_spring_fixture(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.get_initial_context(str(SPRING_LOGIN), top_k_core_files=3)
    assert result.meta.run_id
    assert "java" in result.languages
    assert result.core_modules
    record = facade.audit_store.get(result.meta.run_id)
    assert record is not None
    assert record.intent == "bootstrap"


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


def test_http_post_mcp_tool_routes(tmp_path: Path):
    app = create_app()
    facade = _facade(tmp_path)
    repo = str(SAMPLE_REPO)
    with TestClient(app) as client:
        app.state.facade = facade

        search = client.post("/search", json={"repo_source": repo, "query": "greet", "top_k": 5})
        assert search.status_code == 200, search.text
        assert search.json()["hits"]

        source = client.post(
            "/source",
            json={"repo_source": repo, "file_path": "py_pkg/a.py", "symbol_name": "greet"},
        )
        assert source.status_code == 200, source.text
        assert "def greet" in source.json()["content"]

        deps = client.post(
            "/dependencies",
            json={"repo_source": repo, "symbol_name": "greet", "direction": "both"},
        )
        assert deps.status_code == 200, deps.text
        assert "resolved_refs" in deps.json()["query"]

        impact = client.post(
            "/impact",
            json={"repo_source": repo, "symbol_name": "greet", "depth": 2},
        )
        assert impact.status_code == 200, impact.text
        assert impact.json()["meta"]["run_id"]

        bootstrap = client.post("/initial-context", json={"repo_source": repo})
        assert bootstrap.status_code == 200, bootstrap.text
        assert bootstrap.json()["languages"]

        explore = client.post(
            "/explore",
            json={"repo_source": repo, "query": "greet helper", "include_flow": False},
        )
        assert explore.status_code == 200, explore.text
        body = explore.json()
        assert body["report_markdown"]
        assert body["seeds"] or body["must_read"]

        summary = client.post("/summary", json={"repo_source": repo})
        assert summary.status_code == 200, summary.text
        assert summary.json()["report_markdown"]

        refactor = client.post(
            "/refactor",
            json={"repo_source": repo, "file_path": "py_pkg/a.py", "focus": "coupling"},
        )
        assert refactor.status_code == 200, refactor.text
        assert refactor.json()["meta"]["run_id"]


def test_context_explore_returns_pack(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.context_explore(
        str(SAMPLE_REPO),
        query="greet helper shout",
        top_k=5,
        include_flow=False,
    )
    assert result.meta.run_id
    assert result.meta.graph_update_mode in {"full", "merge", "cached", None}
    assert result.report_markdown
    assert "Context Explore" in result.report_markdown
    assert result.seeds or result.must_read
    record = facade.audit_store.get(result.meta.run_id)
    assert record is not None
    assert record.intent == "context_explore"


def test_context_explore_flow_question(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.context_explore(
        str(FASTAPI_LOGIN),
        query="How does login work?",
        top_k=6,
    )
    assert result.meta.run_id
    assert result.seeds or result.call_paths
    # Flow-ish questions should prefer flow_tracer paths when available
    if result.call_paths:
        assert any(p.source in {"flow_tracer", "graph"} for p in result.call_paths)
