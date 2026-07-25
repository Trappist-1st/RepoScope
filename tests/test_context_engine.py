from pathlib import Path

from app.context_engine import HistoryWindow, assemble_context, load_context_config
from app.context_engine.features import is_entry_file
from app.context_engine.priority import score_candidates
from app.models.schemas import CallEdge, DependencyGraph, FileDependencyEdge
from app.retrieval.schemas import Citation, RetrievalHit


def _hit(
    cid: str,
    path: str,
    content: str,
    score: float,
    source: str = "rerank",
    symbol: str | None = None,
    reason: str | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=cid,
        content=content,
        citation=Citation(file_path=path, start_line=1, end_line=max(1, content.count("\n") + 1)),
        score=score,
        source=source,
        symbol_name=symbol,
        expansion_reason=reason,
        scores={"rerank": score} if source != "graph_expand" else {},
    )


def test_entry_file_detection():
    cfg = load_context_config()
    assert is_entry_file("src/main.py", cfg.entry_files)
    assert is_entry_file("pkg/Application.java", cfg.entry_files)
    assert not is_entry_file("pkg/util.py", cfg.entry_files)


def test_g_splits_file_and_symbol_normalization():
    graph = DependencyGraph(
        repo_id="t",
        file_edges=[
            FileDependencyEdge(source="a.py", target="util.py"),
            FileDependencyEdge(source="b.py", target="util.py"),
            FileDependencyEdge(source="c.py", target="util.py"),
        ],
        call_edges=[
            CallEdge(caller="a.py::f", callee="core.py::important", same_file=False),
            CallEdge(caller="b.py::g", callee="core.py::important", same_file=False),
        ],
    )
    hits = [
        _hit("1", "util.py", "def helper():\n    return 1\n" * 20, 0.1, symbol="helper"),
        _hit("2", "core.py", "def important():\n    return 2\n" * 5, 0.9, symbol="important"),
        _hit("3", "main.py", "def main():\n    important()\n", 0.5, symbol="main"),
    ]
    scored = score_candidates(hits, [], graph=graph)
    by_id = {s.hit.chunk_id: s for s in scored}
    # util has high file_ref, core has high symbol_ref — G should not be pure file domination
    assert by_id["1"].breakdown.file_ref == 3
    assert by_id["2"].breakdown.symbol_ref == 2
    assert by_id["2"].breakdown.graph > 0
    # main is entry + decent relevance → should rank high
    assert by_id["3"].breakdown.entry == 1.0
    ordered = sorted(scored, key=lambda s: s.breakdown.priority, reverse=True)
    assert ordered[0].hit.chunk_id in {"2", "3"}


def test_assemble_drops_low_priority_under_budget():
    hits = [
        _hit("low", "misc.py", "x = 1\n" * 400, 0.05, symbol="x"),
        _hit("high", "main.py", "def main():\n    return 1\n" * 20, 0.95, symbol="main"),
    ]
    expanded = [
        _hit(
            "exp",
            "misc.py",
            "def unused():\n    pass\n" * 200,
            0.0,
            source="graph_expand",
            symbol="unused",
            reason="calls: misc.py::unused",
        )
    ]
    assembled = assemble_context(
        question="what does main do?",
        plan_hint="find entry",
        hits=hits,
        expanded_hits=expanded,
        budget=200,
    )
    kept_ids = {h.chunk_id for h in assembled.code_hits + assembled.expanded_hits}
    assert "high" in kept_ids
    assert assembled.after_tokens <= assembled.budget or len(kept_ids) == 1
    assert assembled.before_tokens >= assembled.after_tokens


def test_history_sliding_window_keeps_latest_only():
    cfg = load_context_config()
    hist = HistoryWindow(window=1)
    hist.push("round1 summary about Foo", round_idx=1)
    hist.push("round2 summary about Bar fix", round_idx=2)
    text = hist.text(token_limit=500)
    assert "round2" in text or "Bar" in text
    assert "Foo" not in text  # old round discarded, not priority-trimmed
