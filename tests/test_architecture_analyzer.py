"""Tests for ArchitectureAnalyzer orchestration."""

from __future__ import annotations

from pathlib import Path

from app.intelligence.architecture.analyzer import (
    ArchitectureAnalyzer,
    analyze_architecture_markdown,
)
from app.intelligence.architecture.models import (
    ArchitectureFindingCategory,
    ArchitecturePatternKind,
)
from tests.helpers_flow import FASTAPI_LOGIN, SPRING_LOGIN, ingest_fixture, seed_spring_login_calls


def test_analyzer_spring_fixture(tmp_path: Path):
    _, kg = ingest_fixture(SPRING_LOGIN, tmp_path)
    kg = seed_spring_login_calls(kg)
    report = ArchitectureAnalyzer().analyze(kg, workspace_root=SPRING_LOGIN)

    assert report.meta.repo_id == kg.repo_id
    assert report.meta.commit_hash == kg.commit_hash
    assert report.meta.generated_at
    assert report.meta.include_flows is False
    assert report.modules.modules
    assert report.profile.file_count >= 1
    assert report.primary_pattern in {
        ArchitecturePatternKind.LAYERED,
        ArchitecturePatternKind.MVC,
        ArchitecturePatternKind.UNKNOWN,
    }
    assert report.findings
    for f in report.findings:
        assert f.evidence, f.title
    cats = {f.category for f in report.findings}
    assert ArchitectureFindingCategory.MODULE_BOUNDARY in cats
    assert ArchitectureFindingCategory.PATTERN in cats or report.primary_pattern == ArchitecturePatternKind.UNKNOWN


def test_analyzer_fastapi_markdown(tmp_path: Path):
    _, kg = ingest_fixture(FASTAPI_LOGIN, tmp_path)
    report, md = analyze_architecture_markdown(kg, workspace_root=FASTAPI_LOGIN)
    assert "Architecture Report" in md
    assert "Modules" in md
    assert "Findings" in md
    assert report.metrics.module_count == len(report.modules.modules)
    assert report.profile.module_count == len(report.modules.modules)


def test_include_flows_warns_but_does_not_require_tracer(tmp_path: Path):
    _, kg = ingest_fixture(SPRING_LOGIN, tmp_path)
    report = ArchitectureAnalyzer().analyze(kg, include_flows=True)
    assert any("include_flows" in w for w in report.warnings)
    assert report.meta.include_flows is False
