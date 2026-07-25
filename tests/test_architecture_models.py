"""Tests for Architecture Intelligence models (Iteration 3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.intelligence.architecture.models import (
    ArchitectureFinding,
    ArchitectureFindingCategory,
    ArchitectureModule,
    ArchitecturePatternKind,
    ArchitectureReport,
    ArchitectureReportMeta,
    EvidenceRef,
    EvidenceRefKind,
    ModuleType,
    PatternMatch,
)


def test_architecture_finding_requires_evidence():
    with pytest.raises(ValidationError):
        ArchitectureFinding(
            finding_id="f1",
            category=ArchitectureFindingCategory.COUPLING,
            title="High coupling",
            evidence=[],
        )


def test_architecture_finding_with_evidence_ok():
    f = ArchitectureFinding(
        finding_id="f1",
        category=ArchitectureFindingCategory.PATTERN,
        title="Layered architecture",
        detail="Controller → Service → Repository role chain observed.",
        evidence=[
            EvidenceRef(
                kind=EvidenceRefKind.FILE,
                file_path="auth/AuthController.java",
                start_line=10,
                end_line=20,
            )
        ],
        related_symbols=["auth/AuthController.java::AuthController.login"],
        related_modules=["auth"],
        confidence="high",
        score=0.82,
        reason="role_layering",
        inference_reason="stable_downward_call_roles",
    )
    assert f.evidence
    assert f.confidence == "high"


def test_module_has_type_and_boundary_confidence():
    m = ArchitectureModule(
        id="mod:auth",
        name="auth",
        path_roots=["auth"],
        module_type=ModuleType.FEATURE,
        boundary_confidence="medium",
        responsibility="authentication",
        role_mix={"controller": 1, "service": 1},
        file_paths=["auth/AuthController.java"],
        evidence=[
            EvidenceRef(kind=EvidenceRefKind.MODULE, module_id="mod:auth", note="path root auth/")
        ],
    )
    assert m.module_type == ModuleType.FEATURE
    assert m.boundary_confidence == "medium"


def test_layer_cluster_not_forced_as_feature():
    m = ArchitectureModule(
        id="mod:controller",
        name="controller",
        path_roots=["controller"],
        module_type=ModuleType.LAYER,
        boundary_confidence="low",
        responsibility="http_api",
    )
    assert m.module_type == ModuleType.LAYER


def test_report_meta_evolution_ready():
    report = ArchitectureReport(
        meta=ArchitectureReportMeta(
            repo_id="abc",
            commit_hash="deadbeef",
            include_flows=False,
        ),
        patterns=[
            PatternMatch(
                pattern=ArchitecturePatternKind.LAYERED,
                score=0.7,
                confidence="medium",
                signals=["controller_service_repository"],
                evidence=[
                    EvidenceRef(kind=EvidenceRefKind.FILE, file_path="auth/AuthService.java")
                ],
            )
        ],
        primary_pattern=ArchitecturePatternKind.LAYERED,
    )
    assert report.meta.commit_hash == "deadbeef"
    assert report.meta.generated_at
    assert report.meta.include_flows is False
    assert report.schema_version == "1.0"
