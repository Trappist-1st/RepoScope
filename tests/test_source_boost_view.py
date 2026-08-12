"""Tests for source-quality boost and view_source continuation."""

from __future__ import annotations

from pathlib import Path

from app.audit import InMemoryAgentRunStore, InMemoryRunStateCache
from app.mcp.service import VIEW_LINE_LIMIT, RepoScopeFacade
from app.retrieval.source_boost import source_quality_multiplier


def test_source_boost_demotes_docs_and_boosts_code():
    assert source_quality_multiplier(file_path="docs/API.md") < 0.5
    assert source_quality_multiplier(file_path="README.md") < 0.7
    code = source_quality_multiplier(
        file_path="src/main/java/Foo.java", language="java", kind="method"
    )
    assert code > 1.0
    assert code > source_quality_multiplier(file_path="docs/guide.md", kind="fallback")


def test_view_source_continuation_fields(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "small.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    big = repo / "big_file.py"
    big.write_text(
        "\n".join(f"line_{i} = {i}" for i in range(1, VIEW_LINE_LIMIT + 80)),
        encoding="utf-8",
    )

    facade = RepoScopeFacade(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "art",
        audit_store=InMemoryAgentRunStore(),
        state_cache=InMemoryRunStateCache(),
        use_hash_embedder=True,
    )

    result = facade.view_source(str(repo), file_path="big_file.py")
    assert result.truncated is True
    assert result.total_lines == VIEW_LINE_LIMIT + 79
    assert result.next_start_line == VIEW_LINE_LIMIT + 1
    assert any("Continue with start_line=" in n for n in result.notes)

    cont = facade.view_source(
        str(repo),
        file_path="big_file.py",
        start_line=result.next_start_line,
        end_line=result.total_lines,
    )
    assert f"line_{result.next_start_line}" in cont.content
    assert cont.citation is not None
    assert cont.citation.start_line == result.next_start_line
