"""ArchitectureAnalyzer: orchestrate modules → profile → dependency → patterns.

Independent of LangGraph and FlowTracer (include_flows reserved, default False).
"""

from __future__ import annotations

import time
from pathlib import Path

from app.intelligence.architecture.dependency import analyze_module_dependencies
from app.intelligence.architecture.format import format_architecture_markdown
from app.intelligence.architecture.models import (
    ArchitectureFinding,
    ArchitectureFindingCategory,
    ArchitectureReport,
    ArchitectureReportMeta,
    ModuleType,
)
from app.intelligence.architecture.modules import discover_modules
from app.intelligence.architecture.patterns import detect_patterns
from app.intelligence.architecture.profile import build_repository_profile
from app.intelligence.enrichers.roles import RoleIndex, build_role_index
from app.intelligence.models import KnowledgeGraph


class ArchitectureAnalyzer:
    """Library entry point for architecture intelligence."""

    def analyze(
        self,
        graph: KnowledgeGraph,
        *,
        workspace_root: Path | str | None = None,
        role_index: RoleIndex | None = None,
        include_flows: bool = False,
        high_fan_out_threshold: int = 5,
        high_coupling_threshold: int = 8,
    ) -> ArchitectureReport:
        t0 = time.perf_counter()
        warnings: list[str] = []
        unresolved: list[str] = []

        if include_flows:
            warnings.append(
                "include_flows=true is reserved; FlowTracer enhancement not enabled in v1"
            )

        roles = role_index or build_role_index(graph)
        module_map = discover_modules(graph, role_index=roles)
        unresolved.extend(module_map.unresolved_files)

        profile = build_repository_profile(
            graph,
            workspace_root=workspace_root,
            module_map=module_map,
        )

        dep = analyze_module_dependencies(
            graph,
            module_map,
            high_fan_out_threshold=high_fan_out_threshold,
            high_coupling_threshold=high_coupling_threshold,
        )

        matches, primary, pattern_findings = detect_patterns(
            graph,
            module_map,
            role_index=roles,
            profile=profile,
            dependency=dep,
        )

        findings: list[ArchitectureFinding] = []
        findings.extend(_module_boundary_findings(module_map.modules))
        findings.extend(pattern_findings)
        findings.extend(dep.findings)
        findings.extend(_profile_findings(profile))

        took = int((time.perf_counter() - t0) * 1000)
        return ArchitectureReport(
            schema_version="1.0",
            meta=ArchitectureReportMeta(
                repo_id=graph.repo_id,
                commit_hash=graph.commit_hash,
                took_ms=took,
                method="heuristic_v1",
                include_flows=False,
                kg_schema_version=graph.schema_version,
            ),
            profile=profile,
            modules=module_map,
            patterns=matches,
            primary_pattern=primary,
            findings=findings,
            metrics=dep.metrics,
            warnings=warnings,
            unresolved=unresolved,
        )


def analyze_architecture(
    graph: KnowledgeGraph,
    **kwargs,
) -> ArchitectureReport:
    return ArchitectureAnalyzer().analyze(graph, **kwargs)


def analyze_architecture_markdown(
    graph: KnowledgeGraph,
    **kwargs,
) -> tuple[ArchitectureReport, str]:
    report = analyze_architecture(graph, **kwargs)
    return report, format_architecture_markdown(report)


def _module_boundary_findings(modules) -> list[ArchitectureFinding]:
    findings: list[ArchitectureFinding] = []
    for m in modules:
        if not m.evidence:
            continue
        # Only emit for clearer boundaries to avoid noise
        if m.module_type == ModuleType.UNKNOWN and m.boundary_confidence == "low":
            continue
        findings.append(
            ArchitectureFinding(
                finding_id=f"mod-{m.id}",
                category=ArchitectureFindingCategory.MODULE_BOUNDARY,
                title=f"Architectural region: {m.name}",
                detail=(
                    f"Cluster '{m.name}' typed as {m.module_type.value} "
                    f"(boundary_confidence={m.boundary_confidence}). "
                    f"Responsibility: {m.responsibility or 'n/a'}."
                ),
                evidence=list(m.evidence)[:6],
                related_modules=[m.id],
                related_symbols=list(m.symbol_ids)[:8],
                confidence=m.boundary_confidence,
                reason="module_discovery",
                inference_reason=f"type={m.module_type.value}",
            )
        )
    return findings


def _profile_findings(profile) -> list[ArchitectureFinding]:
    findings: list[ArchitectureFinding] = []
    for fw in profile.frameworks:
        if not fw.evidence:
            continue
        findings.append(
            ArchitectureFinding(
                finding_id=f"fw-{fw.name.lower().replace(' ', '-')}",
                category=ArchitectureFindingCategory.PROFILE,
                title=f"Framework: {fw.name}",
                detail=f"Detected framework '{fw.name}' from repository manifests or heuristics.",
                evidence=list(fw.evidence)[:4],
                confidence=fw.confidence,
                reason="repository_profile",
                inference_reason="manifest_or_path_heuristic",
            )
        )
    for infra in profile.infra:
        if not infra.evidence:
            continue
        findings.append(
            ArchitectureFinding(
                finding_id=f"infra-{infra.name.lower()}",
                category=ArchitectureFindingCategory.PROFILE,
                title=f"Infrastructure: {infra.name}",
                detail=f"Detected {infra.kind.value} component '{infra.name}'.",
                evidence=list(infra.evidence)[:4],
                confidence=infra.confidence,
                reason="repository_profile",
                inference_reason="manifest_keyword",
            )
        )
    # Ensure profile section always has at least language evidence finding when possible
    if not findings and profile.evidence:
        findings.append(
            ArchitectureFinding(
                finding_id="profile-languages",
                category=ArchitectureFindingCategory.PROFILE,
                title="Language profile",
                detail=f"Languages: {profile.languages}",
                evidence=list(profile.evidence)[:4],
                confidence="medium",
                reason="repository_profile",
                inference_reason="kg_language_stats",
            )
        )
    return findings
