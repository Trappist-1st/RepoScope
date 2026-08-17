"""context_explore payload shape under use_advanced_kg.

The advanced path must return *less* text without returning fewer facts: the
markdown report is dropped because every fact in it is already present in the
structured fields, and low-confidence call edges are pruned.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.audit import InMemoryAgentRunStore, InMemoryRunStateCache
from app.mcp.service import RepoScopeFacade

FIXTURES = Path(__file__).parent / "fixtures"
CONFLICT = FIXTURES / "name_conflict_repo"
QUERY = "how does login find a user by username"


def _facade(tmp_path: Path, *, advanced: bool) -> RepoScopeFacade:
    return RepoScopeFacade(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "art",
        audit_store=InMemoryAgentRunStore(),
        state_cache=InMemoryRunStateCache(),
        use_hash_embedder=True,
        use_advanced_kg=advanced,
    )


def _explore(tmp_path: Path, name: str, *, advanced: bool):
    repo = tmp_path / name
    shutil.copytree(CONFLICT, repo)
    facade = _facade(tmp_path / name, advanced=advanced)
    return facade.context_explore(repo_url=str(repo), query=QUERY)


def _payload_size(result) -> int:
    return len(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


def test_legacy_keeps_the_markdown_report(tmp_path: Path):
    result = _explore(tmp_path, "legacy", advanced=False)
    assert result.report_markdown.strip()


def test_advanced_drops_the_report_but_keeps_the_facts(tmp_path: Path):
    legacy = _explore(tmp_path, "a_legacy", advanced=False)
    advanced = _explore(tmp_path, "a_adv", advanced=True)

    assert advanced.report_markdown == ""
    assert [s.symbol_ref for s in advanced.seeds] == [
        s.symbol_ref for s in legacy.seeds
    ]
    assert _payload_size(advanced) < _payload_size(legacy)


def test_seed_snippets_are_deduped_not_lost(tmp_path: Path):
    """A seed only drops its snippet when must_read carries the same one."""
    advanced = _explore(tmp_path, "dedup", advanced=True)
    must_read = {m.symbol_ref: m for m in advanced.must_read}
    for seed in advanced.seeds:
        if seed.snippet:
            continue
        assert seed.symbol_ref in must_read
        assert must_read[seed.symbol_ref].snippet
        # The pointer still stands on its own.
        assert seed.citation is not None


def test_blast_radius_hits_cite_a_line(tmp_path: Path):
    advanced = _explore(tmp_path, "cite", advanced=True)
    cited = [h for h in advanced.blast_radius if h.evidence]
    for hit in cited:
        span = hit.evidence[0].citation
        assert span.file_path and span.start_line > 0
