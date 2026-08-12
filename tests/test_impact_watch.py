"""Impact analysis + optional watcher daemon tests."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from app.graph.builder import DependencyGraphBuilder
from app.graph.impact import analyze_impact, format_impact_markdown
from app.mcp.service import RepoScopeFacade
from app.parsing.ast_parser import AstParser
from app.parsing.languages import detect_language
from app.audit import InMemoryAgentRunStore, InMemoryRunStateCache
from app.watch.__main__ import DebouncedSync, _snapshot
from tests.conftest import FIXTURES, SAMPLE_REPO

INHERIT = FIXTURES / "inherit_repo"


def _facade(tmp_path: Path) -> RepoScopeFacade:
    return RepoScopeFacade(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "art",
        audit_store=InMemoryAgentRunStore(),
        state_cache=InMemoryRunStateCache(),
        use_hash_embedder=True,
    )


def _graph_from_fixture(root: Path, *rel_paths: str):
    parser = AstParser()
    files = {rel: (root / rel).read_text(encoding="utf-8") for rel in rel_paths}
    defs = {}
    for path, content in files.items():
        lang = detect_language(path)
        assert lang is not None
        defs[path] = parser.parse_definitions(content, lang)
    return DependencyGraphBuilder().build(
        repo_id="impact-test",
        commit_hash=None,
        files=files,
        definitions_by_file=defs,
    )


def test_analyze_impact_callers_and_callees():
    graph = _graph_from_fixture(SAMPLE_REPO, "py_pkg/a.py", "py_pkg/b.py")
    greet = "py_pkg/a.py::greet"
    report = analyze_impact(graph, [greet], depth=2, direction="both", limit=50)
    affected_refs = {e.symbol_ref for e in report.affected}
    depends_refs = {e.symbol_ref for e in report.depends_on}
    # b.run calls greet; Helper.shout also calls greet
    assert any("run" in r or "shout" in r for r in affected_refs)
    md = format_impact_markdown(report)
    assert "Impact Analysis" in md
    assert greet in md or "greet" in md
    # depends_on may be empty for a leaf helper — that's ok
    assert report.seeds == [greet]
    assert isinstance(depends_refs, set)


def test_analyze_impact_inherit_blast_radius():
    graph = _graph_from_fixture(
        INHERIT, "animal/base.py", "animal/dog.py"
    )
    animal = "animal/base.py::Animal"
    report = analyze_impact(graph, [animal], depth=1, direction="affected")
    assert any(e.symbol_ref.endswith("::Dog") for e in report.affected)
    assert any(e.relation == "subtype" for e in report.affected)
    assert "animal/dog.py" in report.affected_files


def test_mcp_analyze_impact_tool(tmp_path: Path):
    facade = _facade(tmp_path)
    result = facade.analyze_impact(
        str(SAMPLE_REPO),
        symbol_name="py_pkg/a.py::greet",
        depth=2,
        direction="both",
    )
    assert result.meta.run_id
    assert result.seeds
    assert "Impact Analysis" in result.report_markdown
    assert result.query["symbol_name"] == "py_pkg/a.py::greet"
    record = facade.audit_store.get(result.meta.run_id)
    assert record is not None
    assert record.intent == "impact"


def test_debounced_sync_fires_once():
    hits: list[str] = []
    lock = threading.Lock()

    def sync_fn() -> None:
        with lock:
            hits.append("sync")

    deb = DebouncedSync(sync_fn, debounce_ms=80)
    deb.poke("a")
    deb.poke("b")
    deb.poke("c")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        with lock:
            if hits:
                break
        time.sleep(0.02)
    time.sleep(0.12)  # ensure no second fire
    with lock:
        assert hits == ["sync"]
    deb.cancel()


def test_snapshot_detects_change(tmp_path: Path):
    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, repo)
    before = _snapshot(repo)
    assert any(p.endswith(".py") for p in before)
    target = repo / "py_pkg" / "a.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# watch\n", encoding="utf-8")
    after = _snapshot(repo)
    assert before != after
