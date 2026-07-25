from pathlib import Path

from app.retrieval.schemas import Citation, RetrievalHit
from app.workflow.analyzers import HallucinatingAnalyzer, StubAnalyzer
from app.workflow.graph import create_default_runner
from app.workflow.nodes.finalize import build_report_markdown
from app.workflow.nodes.retrieve import _build_retrieve_query
from app.workflow.nodes.review import run_review
from app.workflow.nodes.route import route_node
from app.workflow.schemas import Finding, WorkflowInput
from tests.conftest import SAMPLE_REPO


def test_route_keywords():
    out = route_node({"question": "请给出面试追问", "repo_source": "x"})
    assert out["intent"] == "interview"
    out2 = route_node({"question": "如何重构这个模块", "repo_source": "x"})
    assert out2["intent"] == "refactor"
    out3 = route_node(
        {"question": "hello", "repo_source": "x", "intent_hint": "summary"}
    )
    assert out3["intent"] == "summary"


def test_retry_query_uses_hints_not_raw_question_only():
    state = {
        "question": "architecture overview",
        "retry_hints": ["nonexistent/Fake.py:1-2", "symbol:greet"],
    }
    q = _build_retrieve_query(state)
    assert "architecture overview" in q
    assert "greet" in q
    assert "Fake" in q or "nonexistent" in q
    assert q != "architecture overview"


def test_retrieve_query_uses_planner_search_queries():
    from app.workflow.nodes.retrieve import build_retrieve_candidates
    from app.workflow.schemas import AnalysisPlan, PlanStep

    plan = AnalysisPlan(
        intent="summary",
        overall_goal="understand scheduler",
        source="llm",
        steps=[
            PlanStep(
                step_id=1,
                action="Locate scheduler entry",
                search_query="Scheduler Bootstrap ApplicationRunner",
                keywords=["Scheduler", "Bootstrap"],
            ),
            PlanStep(
                step_id=2,
                action="Trace task submit flow",
                search_query="task submit schedule execute",
                keywords=["submit", "schedule"],
            ),
        ],
    )
    cands = build_retrieve_candidates(
        {
            "question": "Summarize the architecture",
            "analysis_plan": plan,
            "retry_hints": [],
            "retry_count": 0,
        }
    )
    assert cands
    labels = [c[0] for c in cands]
    assert "plan_compact" in labels
    assert "question" in labels
    joined = " ".join(q for _, q in cands)
    assert "Scheduler" in joined
    # Must stay short — no mega semicolon dumps
    assert all(len(q) <= 220 for _, q in cands)


def test_retrieve_falls_back_when_queries_miss(tmp_path: Path):
    from app.db import InMemoryFilesRepository, InMemoryReposRepository
    from app.ingestion import IngestionPipeline
    from app.retrieval import IndexRequest, RetrievalService
    from app.retrieval.embedder import HashEmbedder
    from app.retrieval.rerank import IdentityReranker
    from app.retrieval.schemas import RetrieveResponse
    from app.retrieval.vector_store import InMemoryVectorStore
    from app.workflow.nodes.retrieve import make_retrieve_node

    pipeline = IngestionPipeline(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "art",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    ingest = pipeline.run(str(SAMPLE_REPO))
    chunks, _ = pipeline.load_artifacts(ingest.repo_id)
    retrieval = RetrievalService(
        artifact_dir=tmp_path / "art",
        embedder=HashEmbedder(),
        vector_store=InMemoryVectorStore(),
        reranker=IdentityReranker(),
    )
    retrieval.index(IndexRequest(repo_id=ingest.repo_id, chunks=chunks))

    # Simulate total query miss (HashEmbedder normally always returns neighbors).
    def _empty_retrieve(req):
        return RetrieveResponse(repo_id=req.repo_id, query=req.query, hits=[], expanded_hits=[])

    retrieval.retrieve = _empty_retrieve  # type: ignore[method-assign]
    node = make_retrieve_node(retrieval)
    out = node(
        {
            "repo_id": ingest.repo_id,
            "question": "zzzznonexistenttokenqqq",
            "analysis_plan": None,
            "retry_hints": [],
            "retry_count": 2,
        }
    )
    assert out["hits"], "explore fallback should provide grounded chunks"
    assert any(h.source == "explore" for h in out["hits"])


def test_markdown_files_are_indexed(tmp_path: Path):
    from app.db import InMemoryFilesRepository, InMemoryReposRepository
    from app.ingestion import IngestionPipeline

    repo = tmp_path / "md_repo"
    repo.mkdir()
    (repo / "topics").mkdir()
    (repo / "topics" / "two-pointers.md").write_text(
        "# Two Pointers\n\nUse left/right indices.\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Notes\n\nAlgo notes repo.\n", encoding="utf-8")

    pipeline = IngestionPipeline(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "art",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    ingest = pipeline.run(str(repo))
    chunks, _ = pipeline.load_artifacts(ingest.repo_id)
    paths = {c.file_path for c in chunks}
    assert "topics/two-pointers.md" in paths
    assert "README.md" in paths


def test_template_analysis_plan_fallback():
    from app.workflow.planner import template_analysis_plan

    plan = template_analysis_plan(question="Summarize X", intent="summary")
    assert plan.source == "template"
    assert len(plan.steps) == 3
    assert all(s.search_query for s in plan.steps)
    assert plan.action_list()[0].startswith("Identify")


def test_review_rejects_hallucinated_citation():
    hits = [
        RetrievalHit(
            chunk_id="1",
            content="def greet(name):\n    return name\n",
            citation=Citation(file_path="py_pkg/a.py", start_line=4, end_line=5),
            score=1.0,
            source="bm25",
            symbol_name="greet",
        )
    ]
    state = {
        "hits": hits,
        "expanded_hits": [],
        "primary_citations": ["py_pkg/a.py:4-5"],
        "expanded_citations": [],
        "plan": ["step1"],
        "findings": [
            Finding(
                claim="greet exists",
                citations=["ghost.py:1-1"],
                symbols=["greet"],
                plan_step_idx=0,
            )
        ],
        "retry_count": 0,
        "max_review_retries": 2,
    }
    out = run_review(state)
    assert out["review_passed"] is False
    assert out["review_should_retry"] is True
    assert out["retry_count"] == 1
    assert any(i.type == "citation_not_in_retrieve" for i in out["review_issues"])
    assert out["retry_hints"]
    assert "ghost.py:1-1" in out["retry_hints"]


def test_review_soft_expand_keeps_expansion_reason():
    expanded = [
        RetrievalHit(
            chunk_id="2",
            content="class Helper:\n    def shout(self):\n        return greet()\n",
            citation=Citation(file_path="py_pkg/a.py", start_line=8, end_line=13),
            score=0.0,
            source="graph_expand",
            symbol_name="Helper",
            expansion_reason="called_by: py_pkg/a.py::Helper.shout",
        )
    ]
    state = {
        "hits": [],
        "expanded_hits": expanded,
        "primary_citations": [],
        "expanded_citations": ["py_pkg/a.py:8-13"],
        "plan": ["step1"],
        "findings": [
            Finding(
                claim="Helper shouts",
                citations=["py_pkg/a.py:8-13"],
                symbols=["Helper"],
                plan_step_idx=0,
            )
        ],
        "retry_count": 0,
        "max_review_retries": 2,
    }
    out = run_review(state)
    assert out["review_passed"] is True
    soft = [i for i in out["review_issues"] if i.type == "citation_from_expand"]
    assert soft
    finding = out["findings"][0]
    assert finding.evidence_tier == "expanded"
    assert finding.expansion_reasons
    assert "called_by:" in finding.expansion_reasons[0]
    assert finding.confidence in {"medium", "low"}

    report = build_report_markdown({**state, **out, "intent": "summary", "question": "q"})
    assert "间接推断" in report
    assert "called_by:" in report


def test_end_to_end_stub_workflow(tmp_path: Path):
    runner = create_default_runner(
        workspace_root=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        analyzer=StubAnalyzer(),
        use_hash_embedder=True,
    )
    result = runner.run(
        WorkflowInput(
            question="Summarize the architecture of this sample repo",
            repo_source=str(SAMPLE_REPO),
            intent_hint="summary",
        )
    )
    assert result.repo_id
    assert result.intent == "summary"
    assert result.findings
    assert all(f.citations for f in result.findings)
    assert "RepoScope Report" in result.report_markdown
    assert result.retry_count == 0
    assert result.low_confidence is False


def test_hallucination_triggers_retry_then_recovers(tmp_path: Path):
    analyzer = HallucinatingAnalyzer()
    runner = create_default_runner(
        workspace_root=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        analyzer=analyzer,
        use_hash_embedder=True,
    )
    result = runner.run(
        WorkflowInput(
            question="Where is greet defined?",
            repo_source=str(SAMPLE_REPO),
            intent_hint="summary",
        )
    )
    assert analyzer.calls >= 2
    assert result.retry_count >= 1
    # Eventually should not keep the hallucinated citation in final findings
    all_cites = [c for f in result.findings for c in f.citations]
    assert analyzer.hallucinated_citation not in all_cites
    assert result.review_issues or result.retry_count >= 1


def test_analyze_history_is_per_run_not_shared_closure():
    """Sequential invokes of the same node must not leak history across runs."""
    from app.workflow.nodes.analyze import make_analyze_node

    node = make_analyze_node(StubAnalyzer())
    base = {
        "question": "q",
        "intent": "summary",
        "hits": [
            RetrievalHit(
                chunk_id="1",
                content="def greet():\n    return 1\n",
                citation=Citation(file_path="a.py", start_line=1, end_line=2),
                score=1.0,
                source="bm25",
                symbol_name="greet",
            )
        ],
        "expanded_hits": [],
        "token_budget": 4000,
        "history_rounds": 0,
        "analysis_history": [],
    }
    out1 = node(base)
    assert out1["history_rounds"] == 1
    assert len(out1["analysis_history"]) == 1

    # Fresh run with empty history — must not see prior run's rounds
    out2 = node({**base, "history_rounds": 0, "analysis_history": []})
    assert out2["history_rounds"] == 1
    assert len(out2["analysis_history"]) == 1

    # Same-run retry: prior analysis_history is carried in state
    out3 = node({**base, "history_rounds": 1, "analysis_history": out1["analysis_history"]})
    assert out3["history_rounds"] == 2
    assert len(out3["analysis_history"]) >= 1
