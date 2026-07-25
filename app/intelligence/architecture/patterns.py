"""Architecture pattern detection (heuristic, evidence-backed).

v1 patterns: layered | mvc | hexagonal | event_driven | unknown
Does not claim DDD. Does not require FlowTracer.
"""

from __future__ import annotations

from app.intelligence.architecture.dependency import DependencyAnalysisResult
from app.intelligence.architecture.models import (
    ArchitectureFinding,
    ArchitectureFindingCategory,
    ArchitecturePatternKind,
    EvidenceRef,
    EvidenceRefKind,
    ModuleMap,
    ModuleType,
    PatternMatch,
    RepositoryProfile,
)
from app.intelligence.enrichers.roles import FlowRole, RoleIndex, build_role_index, role_of
from app.intelligence.models import EdgeType, KnowledgeGraph, NodeKind

_PRIMARY_THRESHOLD = 0.45


def detect_patterns(
    graph: KnowledgeGraph,
    module_map: ModuleMap,
    *,
    role_index: RoleIndex | None = None,
    profile: RepositoryProfile | None = None,
    dependency: DependencyAnalysisResult | None = None,
) -> tuple[list[PatternMatch], ArchitecturePatternKind, list[ArchitectureFinding]]:
    """
    Score v1 patterns; return (matches, primary, findings).

    Primary is the top score if >= threshold, else unknown.
    """
    roles = role_index or build_role_index(graph)
    role_counts = _count_roles(graph, roles)
    path_blob = " ".join(sorted({(n.file_path or "").lower() for n in graph.nodes}))

    matches = [
        _score_layered(graph, roles, role_counts, module_map),
        _score_mvc(graph, roles, role_counts, module_map, path_blob),
        _score_hexagonal(module_map, path_blob, role_counts),
        _score_event_driven(graph, roles, role_counts, path_blob, profile),
    ]
    matches.sort(key=lambda m: (-m.score, m.pattern.value))

    primary = ArchitecturePatternKind.UNKNOWN
    if matches and matches[0].score >= _PRIMARY_THRESHOLD:
        primary = matches[0].pattern
    else:
        matches.append(
            PatternMatch(
                pattern=ArchitecturePatternKind.UNKNOWN,
                score=0.0,
                confidence="low",
                signals=["no_pattern_above_threshold"],
                counter_signals=[f"best_score={matches[0].score:.2f}" if matches else "empty"],
                evidence=_fallback_evidence(module_map, graph),
            )
        )

    findings = _pattern_findings(matches, primary, dependency)
    return matches, primary, findings


def _count_roles(graph: KnowledgeGraph, roles: RoleIndex) -> dict[str, int]:
    counts: dict[str, int] = {}
    for n in graph.nodes:
        if n.kind == NodeKind.FILE:
            continue
        r = role_of(roles, n.id).value
        counts[r] = counts.get(r, 0) + 1
    return counts


def _score_layered(
    graph: KnowledgeGraph,
    roles: RoleIndex,
    role_counts: dict[str, int],
    module_map: ModuleMap,
) -> PatternMatch:
    signals: list[str] = []
    counter: list[str] = []
    evidence: list[EvidenceRef] = []
    score = 0.0

    has_c = role_counts.get(FlowRole.CONTROLLER.value, 0) > 0
    has_s = role_counts.get(FlowRole.SERVICE.value, 0) > 0
    has_r = role_counts.get(FlowRole.REPOSITORY.value, 0) > 0
    if has_c:
        score += 0.2
        signals.append("has_controller_role")
    if has_s:
        score += 0.2
        signals.append("has_service_role")
    if has_r:
        score += 0.2
        signals.append("has_repository_role")
    if has_c and has_s and has_r:
        score += 0.15
        signals.append("full_c_s_r_trio")

    down, up, samples = _layer_edge_stats(graph, roles)
    if down + up > 0:
        ratio = down / (down + up)
        score += 0.25 * ratio
        signals.append(f"downward_call_ratio={ratio:.2f}")
        if up > down:
            counter.append("more_upward_than_downward_calls")
            score -= 0.1
    else:
        counter.append("no_cross_role_call_edges")

    evidence.extend(samples[:5])
    evidence.extend(_role_file_evidence(graph, roles, FlowRole.CONTROLLER, limit=1))
    evidence.extend(_role_file_evidence(graph, roles, FlowRole.SERVICE, limit=1))
    evidence.extend(_role_file_evidence(graph, roles, FlowRole.REPOSITORY, limit=1))
    evidence = _dedupe(evidence)

    # layer-type modules also hint technical layering
    layer_mods = [m for m in module_map.modules if m.module_type == ModuleType.LAYER]
    if len(layer_mods) >= 2:
        score += 0.1
        signals.append(f"layer_modules={len(layer_mods)}")
        evidence.append(
            EvidenceRef(
                kind=EvidenceRefKind.MODULE,
                module_id=layer_mods[0].id,
                note="technical layer cluster present",
            )
        )

    conf = _conf_from_score(score)
    if not evidence:
        evidence = _fallback_evidence(module_map, graph)
        conf = "low"

    return PatternMatch(
        pattern=ArchitecturePatternKind.LAYERED,
        score=min(score, 1.0),
        confidence=conf,
        signals=signals,
        counter_signals=counter,
        evidence=evidence[:8],
    )


def _score_mvc(
    graph: KnowledgeGraph,
    roles: RoleIndex,
    role_counts: dict[str, int],
    module_map: ModuleMap,
    path_blob: str,
) -> PatternMatch:
    signals: list[str] = []
    counter: list[str] = []
    evidence: list[EvidenceRef] = []
    score = 0.0

    if role_counts.get(FlowRole.CONTROLLER.value, 0) > 0:
        score += 0.35
        signals.append("controller_as_view_entry")
        evidence.extend(_role_file_evidence(graph, roles, FlowRole.CONTROLLER, limit=2))

    # model-ish: repository / entity / model paths
    if role_counts.get(FlowRole.REPOSITORY.value, 0) > 0:
        score += 0.25
        signals.append("repository_as_model_access")
        evidence.extend(_role_file_evidence(graph, roles, FlowRole.REPOSITORY, limit=1))
    if any(seg in path_blob for seg in ("/model", "/models", "/entity", "/entities", "/dto")):
        score += 0.15
        signals.append("model_or_entity_path")

    if role_counts.get(FlowRole.SERVICE.value, 0) > 0:
        score += 0.15
        signals.append("service_as_controller_support")
    else:
        counter.append("no_service_role")

    # Views optional in API-only apps — mild penalty not applied; note instead
    if any(seg in path_blob for seg in ("/view", "/views", "/templates", "/thymeleaf")):
        score += 0.15
        signals.append("view_templates_present")
    else:
        counter.append("no_classic_view_layer")

    # MVC is weaker if hexagonal signals dominate
    if "domain/" in path_blob and "adapter" in path_blob:
        score -= 0.1
        counter.append("hexagonal_paths_present")

    evidence = _dedupe(evidence)
    conf = _conf_from_score(score)
    if not evidence:
        evidence = _fallback_evidence(module_map, graph)
        conf = "low"

    return PatternMatch(
        pattern=ArchitecturePatternKind.MVC,
        score=min(max(score, 0.0), 1.0),
        confidence=conf,
        signals=signals,
        counter_signals=counter,
        evidence=evidence[:8],
    )


def _score_hexagonal(
    module_map: ModuleMap,
    path_blob: str,
    role_counts: dict[str, int],
) -> PatternMatch:
    signals: list[str] = []
    counter: list[str] = []
    evidence: list[EvidenceRef] = []
    score = 0.0

    domainish = any(
        seg in path_blob
        for seg in ("/domain/", "/application/", "/usecase", "/port/", "/ports/")
    )
    adapterish = any(
        seg in path_blob
        for seg in (
            "/adapter",
            "/adapters",
            "/infra/",
            "/infrastructure/",
            "/persistence/",
        )
    )
    if domainish:
        score += 0.35
        signals.append("domain_or_application_path")
    else:
        counter.append("no_domain_application_path")
    if adapterish:
        score += 0.35
        signals.append("adapter_or_infra_path")
    else:
        counter.append("no_adapter_infra_path")
    if domainish and adapterish:
        score += 0.2
        signals.append("ports_adapters_separation")

    for m in module_map.modules:
        leaf = m.name.lower()
        if leaf in {"domain", "application", "adapter", "adapters", "infra", "infrastructure"}:
            evidence.append(
                EvidenceRef(
                    kind=EvidenceRefKind.MODULE,
                    module_id=m.id,
                    note=f"hexagonal-ish module cluster: {m.name}",
                )
            )
            if m.file_paths:
                evidence.append(
                    EvidenceRef(
                        kind=EvidenceRefKind.FILE,
                        file_path=m.file_paths[0],
                        module_id=m.id,
                    )
                )

    # Classic layered trio without ports paths → not hexagonal
    if (
        role_counts.get(FlowRole.CONTROLLER.value, 0)
        and role_counts.get(FlowRole.SERVICE.value, 0)
        and not domainish
    ):
        counter.append("looks_like_classic_layered_not_hexagonal")
        score -= 0.15

    evidence = _dedupe(evidence)
    conf = _conf_from_score(score)
    if not evidence and module_map.modules:
        evidence = [
            EvidenceRef(
                kind=EvidenceRefKind.MODULE,
                module_id=module_map.modules[0].id,
                note="insufficient hexagonal path evidence",
            )
        ]
        conf = "low"

    return PatternMatch(
        pattern=ArchitecturePatternKind.HEXAGONAL,
        score=min(max(score, 0.0), 1.0),
        confidence=conf,
        signals=signals,
        counter_signals=counter,
        evidence=evidence[:8] or [
            EvidenceRef(kind=EvidenceRefKind.MODULE, module_id="mod:none", note="no modules")
        ],
    )


def _score_event_driven(
    graph: KnowledgeGraph,
    roles: RoleIndex,
    role_counts: dict[str, int],
    path_blob: str,
    profile: RepositoryProfile | None,
) -> PatternMatch:
    signals: list[str] = []
    counter: list[str] = []
    evidence: list[EvidenceRef] = []
    score = 0.0

    mq_roles = role_counts.get(FlowRole.MQ.value, 0)
    if mq_roles > 0:
        score += 0.4
        signals.append(f"mq_role_nodes={mq_roles}")
        evidence.extend(_role_file_evidence(graph, roles, FlowRole.MQ, limit=3))

    if any(seg in path_blob for seg in ("/messaging", "/kafka", "/rabbit", "/events", "/consumer")):
        score += 0.25
        signals.append("messaging_path")

    if profile:
        for hit in profile.infra:
            if hit.kind.value == "mq":
                score += 0.25
                signals.append(f"infra:{hit.name}")
                evidence.extend(hit.evidence[:2])

    # symbol name hints
    for n in graph.nodes:
        if n.kind == NodeKind.FILE:
            continue
        lower = n.name.lower()
        if any(k in lower for k in ("listener", "consumer", "producer", "publisher", "onmessage")):
            score += 0.05
            signals.append(f"symbol_hint:{n.name}")
            if n.file_path and len(evidence) < 6:
                evidence.append(
                    EvidenceRef(
                        kind=EvidenceRefKind.SYMBOL,
                        file_path=n.file_path,
                        start_line=n.start_line,
                        end_line=n.end_line,
                        node_id=n.id,
                    )
                )
            if score >= 0.9:
                break

    if score < 0.2:
        counter.append("little_or_no_messaging_evidence")

    evidence = _dedupe(evidence)
    conf = _conf_from_score(score)
    if not evidence:
        # still need evidence field for PatternMatch consumers / findings
        sample = next((n.file_path for n in graph.nodes if n.file_path), None)
        evidence = [
            EvidenceRef(
                kind=EvidenceRefKind.FILE,
                file_path=sample or ".",
                note="no event-driven evidence found",
            )
        ]
        conf = "low"

    return PatternMatch(
        pattern=ArchitecturePatternKind.EVENT_DRIVEN,
        score=min(score, 1.0),
        confidence=conf,
        signals=signals,
        counter_signals=counter,
        evidence=evidence[:8],
    )


def _layer_edge_stats(
    graph: KnowledgeGraph,
    roles: RoleIndex,
) -> tuple[int, int, list[EvidenceRef]]:
    rank = {
        FlowRole.CONTROLLER: 0,
        FlowRole.GATEWAY: 0,
        FlowRole.SERVICE: 1,
        FlowRole.MQ: 2,
        FlowRole.CACHE: 2,
        FlowRole.REPOSITORY: 3,
        FlowRole.DATABASE: 4,
        FlowRole.UNKNOWN: 2,
        FlowRole.EXTERNAL: 4,
    }
    down = up = 0
    samples: list[EvidenceRef] = []
    for e in graph.edges:
        if e.edge_type != EdgeType.CALL:
            continue
        sr = role_of(roles, e.source_id)
        tr = role_of(roles, e.target_id)
        if sr == FlowRole.UNKNOWN or tr == FlowRole.UNKNOWN:
            continue
        if rank[tr] > rank[sr]:
            down += 1
            if len(samples) < 5:
                samples.append(
                    EvidenceRef(
                        kind=EvidenceRefKind.EDGE,
                        edge_id=e.id,
                        note=f"downward {sr.value}->{tr.value}",
                    )
                )
        elif rank[tr] < rank[sr]:
            up += 1
            if len(samples) < 5:
                samples.append(
                    EvidenceRef(
                        kind=EvidenceRefKind.EDGE,
                        edge_id=e.id,
                        note=f"upward {sr.value}->{tr.value}",
                    )
                )
    return down, up, samples


def _role_file_evidence(
    graph: KnowledgeGraph,
    roles: RoleIndex,
    role: FlowRole,
    *,
    limit: int,
) -> list[EvidenceRef]:
    out: list[EvidenceRef] = []
    for n in graph.nodes:
        if role_of(roles, n.id) != role:
            continue
        if not n.file_path:
            continue
        out.append(
            EvidenceRef(
                kind=EvidenceRefKind.SYMBOL if n.kind != NodeKind.FILE else EvidenceRefKind.FILE,
                file_path=n.file_path,
                start_line=n.start_line,
                end_line=n.end_line,
                node_id=n.id,
                note=f"role:{role.value}",
            )
        )
        if len(out) >= limit:
            break
    return out


def _conf_from_score(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _fallback_evidence(module_map: ModuleMap, graph: KnowledgeGraph) -> list[EvidenceRef]:
    if module_map.modules and module_map.modules[0].file_paths:
        m = module_map.modules[0]
        return [
            EvidenceRef(
                kind=EvidenceRefKind.FILE,
                file_path=m.file_paths[0],
                module_id=m.id,
                note="fallback evidence",
            )
        ]
    fp = next((n.file_path for n in graph.nodes if n.file_path), ".")
    return [EvidenceRef(kind=EvidenceRefKind.FILE, file_path=fp, note="fallback evidence")]


def _dedupe(items: list[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple] = set()
    out: list[EvidenceRef] = []
    for e in items:
        key = (e.kind.value, e.file_path, e.edge_id, e.node_id, e.note)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _pattern_findings(
    matches: list[PatternMatch],
    primary: ArchitecturePatternKind,
    dependency: DependencyAnalysisResult | None,
) -> list[ArchitectureFinding]:
    findings: list[ArchitectureFinding] = []
    primary_match = next((m for m in matches if m.pattern == primary), None)
    if primary_match and primary != ArchitecturePatternKind.UNKNOWN and primary_match.evidence:
        findings.append(
            ArchitectureFinding(
                finding_id=f"pattern-{primary.value}",
                category=ArchitectureFindingCategory.PATTERN,
                title=f"Architecture pattern: {primary.value}",
                detail=(
                    f"Detected {primary.value} with score={primary_match.score:.2f}. "
                    f"Signals: {', '.join(primary_match.signals[:5]) or 'n/a'}."
                ),
                evidence=primary_match.evidence[:8],
                confidence=primary_match.confidence,
                score=primary_match.score,
                reason="pattern_detection",
                inference_reason=";".join(primary_match.signals[:6]) or "heuristic_v1",
            )
        )

    # Layer violations if layered is primary and we have upward edges in dependency notes
    if primary == ArchitecturePatternKind.LAYERED and dependency is not None:
        # optional: already covered by edge samples in layered score; skip heavy logic here
        pass

    return findings
