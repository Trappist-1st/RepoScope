"""Module-level dependency metrics: fan-in/out, cycles, coupling, risk.

v1 metrics only — no PageRank / embeddings / community detection.
Produces evidence-backed ArchitectureFinding objects.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from app.intelligence.architecture.models import (
    ArchitectureFinding,
    ArchitectureFindingCategory,
    ArchitectureMetrics,
    ArchitectureModule,
    EvidenceRef,
    EvidenceRefKind,
    ModuleMap,
)
from app.intelligence.models import EdgeType, KnowledgeGraph
from app.intelligence.query import get_node


@dataclass
class DependencyAnalysisResult:
    metrics: ArchitectureMetrics
    findings: list[ArchitectureFinding] = field(default_factory=list)
    # module_id -> set of module_ids it depends on (outgoing)
    adjacency: dict[str, set[str]] = field(default_factory=dict)


def analyze_module_dependencies(
    graph: KnowledgeGraph,
    module_map: ModuleMap,
    *,
    high_fan_out_threshold: int = 5,
    high_coupling_threshold: int = 8,
) -> DependencyAnalysisResult:
    """
    Project KG import/call edges onto modules, then compute simple metrics.
    """
    file_to_mod = _file_to_module_id(module_map)
    mod_by_id = {m.id: m for m in module_map.modules}
    if len(mod_by_id) < 1:
        return DependencyAnalysisResult(metrics=ArchitectureMetrics())

    # adjacency: src_mod -> {dst_mod} with edge evidence samples
    adjacency: dict[str, set[str]] = defaultdict(set)
    edge_samples: dict[tuple[str, str], list[EvidenceRef]] = defaultdict(list)
    cross_edges = 0

    for e in graph.edges:
        if e.edge_type not in {EdgeType.IMPORT, EdgeType.CALL}:
            continue
        sm = _module_of_node(graph, e.source_id, file_to_mod)
        tm = _module_of_node(graph, e.target_id, file_to_mod)
        if sm is None or tm is None or sm == tm:
            continue
        cross_edges += 1
        adjacency[sm].add(tm)
        if len(edge_samples[(sm, tm)]) < 3:
            edge_samples[(sm, tm)].append(
                EvidenceRef(
                    kind=EvidenceRefKind.EDGE,
                    edge_id=e.id,
                    module_id=sm,
                    note=f"{e.edge_type.value}: {sm} → {tm}",
                )
            )

    fan_out: dict[str, int] = {mid: 0 for mid in mod_by_id}
    fan_in: dict[str, int] = {mid: 0 for mid in mod_by_id}
    for src, dsts in adjacency.items():
        fan_out[src] = len(dsts)
        for dst in dsts:
            fan_in[dst] = fan_in.get(dst, 0) + 1

    coupling = {
        mid: float(fan_in.get(mid, 0) + fan_out.get(mid, 0)) for mid in mod_by_id
    }

    n = len(mod_by_id)
    possible = n * (n - 1) if n > 1 else 1
    # unique directed module pairs
    directed_pairs = sum(len(dsts) for dsts in adjacency.values())
    density = directed_pairs / possible if possible else 0.0

    cycles = _find_cycles(adjacency)
    per_module = {
        mid: {
            "fan_in": float(fan_in.get(mid, 0)),
            "fan_out": float(fan_out.get(mid, 0)),
            "coupling": coupling.get(mid, 0.0),
        }
        for mid in mod_by_id
    }

    metrics = ArchitectureMetrics(
        module_count=n,
        cross_module_edges=cross_edges,
        dependency_density=density,
        cycle_count=len(cycles),
        max_fan_in=float(max(fan_in.values()) if fan_in else 0),
        max_fan_out=float(max(fan_out.values()) if fan_out else 0),
        per_module=per_module,
    )

    findings: list[ArchitectureFinding] = []
    findings.extend(_cycle_findings(cycles, edge_samples, mod_by_id))
    findings.extend(
        _coupling_findings(
            mod_by_id,
            fan_in,
            fan_out,
            coupling,
            edge_samples,
            adjacency,
            high_fan_out_threshold=high_fan_out_threshold,
            high_coupling_threshold=high_coupling_threshold,
        )
    )

    return DependencyAnalysisResult(
        metrics=metrics,
        findings=findings,
        adjacency={k: set(v) for k, v in adjacency.items()},
    )


def _file_to_module_id(module_map: ModuleMap) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in module_map.modules:
        for fp in m.file_paths:
            out[fp.replace("\\", "/")] = m.id
    return out


def _module_of_node(
    graph: KnowledgeGraph,
    node_id: str,
    file_to_mod: dict[str, str],
) -> str | None:
    n = get_node(graph, node_id)
    if n is None:
        return None
    fp = n.file_path
    if not fp and n.kind.value == "file":
        fp = n.name
    if not fp:
        return None
    return file_to_mod.get(fp.replace("\\", "/"))


def _find_cycles(adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Simple DFS cycle enumeration (unique cycles, capped)."""
    cycles: list[list[str]] = []
    seen_cycle_keys: set[tuple[str, ...]] = set()

    def normalize(path: list[str]) -> tuple[str, ...]:
        # rotate so smallest id is first
        if not path:
            return tuple()
        i = path.index(min(path))
        rot = path[i:] + path[:i]
        return tuple(rot)

    nodes = set(adjacency.keys())
    for dsts in adjacency.values():
        nodes |= set(dsts)

    for start in sorted(nodes):
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for nxt in sorted(adjacency.get(node, ())):
                if nxt == start and len(path) >= 2:
                    key = normalize(path)
                    if key not in seen_cycle_keys:
                        seen_cycle_keys.add(key)
                        cycles.append(list(key))
                    continue
                if nxt in path:
                    continue
                if len(path) >= 6:
                    continue
                stack.append((nxt, path + [nxt]))
            if len(cycles) >= 20:
                return cycles
    return cycles


def _cycle_findings(
    cycles: list[list[str]],
    edge_samples: dict[tuple[str, str], list[EvidenceRef]],
    mod_by_id: dict[str, ArchitectureModule],
) -> list[ArchitectureFinding]:
    findings: list[ArchitectureFinding] = []
    for cycle in cycles[:10]:
        evidence: list[EvidenceRef] = []
        for i, mid in enumerate(cycle):
            nxt = cycle[(i + 1) % len(cycle)]
            evidence.extend(edge_samples.get((mid, nxt), []))
            m = mod_by_id.get(mid)
            if m and m.file_paths:
                evidence.append(
                    EvidenceRef(
                        kind=EvidenceRefKind.FILE,
                        file_path=m.file_paths[0],
                        module_id=mid,
                        note=f"module in cycle: {m.name}",
                    )
                )
        if not evidence:
            # should not happen if cycle came from edges; skip invalid
            continue
        # dedupe evidence by (kind, edge_id, file_path, note)
        evidence = _dedupe_evidence(evidence)[:8]
        names = " → ".join(mod_by_id[m].name if m in mod_by_id else m for m in cycle)
        findings.append(
            ArchitectureFinding(
                finding_id=f"cycle-{uuid.uuid4().hex[:8]}",
                category=ArchitectureFindingCategory.CIRCULAR_DEPENDENCY,
                title="Circular module dependency",
                detail=f"Cycle detected among modules: {names} → {mod_by_id[cycle[0]].name if cycle[0] in mod_by_id else cycle[0]}",
                evidence=evidence,
                related_modules=list(cycle),
                confidence="high" if len(evidence) >= 2 else "medium",
                score=float(len(cycle)),
                reason="module_graph_cycle",
                inference_reason="dfs_cycle_on_cross_module_edges",
            )
        )
    return findings


def _coupling_findings(
    mod_by_id: dict[str, ArchitectureModule],
    fan_in: dict[str, int],
    fan_out: dict[str, int],
    coupling: dict[str, float],
    edge_samples: dict[tuple[str, str], list[EvidenceRef]],
    adjacency: dict[str, set[str]],
    *,
    high_fan_out_threshold: int,
    high_coupling_threshold: int,
) -> list[ArchitectureFinding]:
    findings: list[ArchitectureFinding] = []
    for mid, m in mod_by_id.items():
        fo = fan_out.get(mid, 0)
        fi = fan_in.get(mid, 0)
        coup = int(coupling.get(mid, 0))

        evidence: list[EvidenceRef] = []
        if m.file_paths:
            evidence.append(
                EvidenceRef(
                    kind=EvidenceRefKind.FILE,
                    file_path=m.file_paths[0],
                    module_id=mid,
                    note=f"module root sample ({m.module_type.value})",
                )
            )
        for dst in sorted(adjacency.get(mid, ())):
            evidence.extend(edge_samples.get((mid, dst), [])[:1])
        for src, dsts in adjacency.items():
            if mid in dsts:
                evidence.extend(edge_samples.get((src, mid), [])[:1])
        evidence = _dedupe_evidence(evidence)
        if not evidence:
            continue

        if fo >= high_fan_out_threshold:
            deps = sorted(
                mod_by_id[d].name if d in mod_by_id else d for d in adjacency.get(mid, ())
            )
            findings.append(
                ArchitectureFinding(
                    finding_id=f"fanout-{uuid.uuid4().hex[:8]}",
                    category=ArchitectureFindingCategory.COUPLING,
                    title=f"High fan-out module: {m.name}",
                    detail=(
                        f"Module '{m.name}' depends on {fo} other modules"
                        + (f" ({', '.join(deps[:8])})" if deps else "")
                        + f". module_type={m.module_type.value}."
                    ),
                    evidence=evidence[:8],
                    related_modules=[mid, *sorted(adjacency.get(mid, ()))],
                    related_symbols=m.symbol_ids[:10],
                    confidence="medium",
                    score=float(fo),
                    reason="high_fan_out",
                    inference_reason=f"fan_out>={high_fan_out_threshold}",
                )
            )

        if coup >= high_coupling_threshold:
            findings.append(
                ArchitectureFinding(
                    finding_id=f"coup-{uuid.uuid4().hex[:8]}",
                    category=ArchitectureFindingCategory.RISK,
                    title=f"High-risk coupling hub: {m.name}",
                    detail=(
                        f"Module '{m.name}' has coupling={coup} "
                        f"(fan-in={fi}, fan-out={fo})."
                    ),
                    evidence=evidence[:8],
                    related_modules=[mid],
                    related_symbols=m.symbol_ids[:10],
                    confidence="medium",
                    score=float(coup),
                    reason="high_coupling",
                    inference_reason=f"fan_in+fan_out>={high_coupling_threshold}",
                )
            )
        elif fi >= high_fan_out_threshold and fo <= 1:
            findings.append(
                ArchitectureFinding(
                    finding_id=f"fanin-{uuid.uuid4().hex[:8]}",
                    category=ArchitectureFindingCategory.RISK,
                    title=f"High fan-in module: {m.name}",
                    detail=(
                        f"Module '{m.name}' is depended on by {fi} modules "
                        f"(fan-out={fo}). Changes here may be high-impact."
                    ),
                    evidence=evidence[:8],
                    related_modules=[mid],
                    related_symbols=m.symbol_ids[:10],
                    confidence="medium",
                    score=float(fi),
                    reason="high_fan_in",
                    inference_reason=f"fan_in>={high_fan_out_threshold}",
                )
            )
    return findings


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple] = set()
    out: list[EvidenceRef] = []
    for e in items:
        key = (e.kind.value, e.edge_id, e.file_path, e.module_id, e.note)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
