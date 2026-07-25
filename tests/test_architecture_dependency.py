"""Tests for module dependency analysis."""

from __future__ import annotations

from app.intelligence.architecture.dependency import analyze_module_dependencies
from app.intelligence.architecture.models import (
    ArchitectureFindingCategory,
    ArchitectureModule,
    EvidenceRef,
    EvidenceRefKind,
    ModuleMap,
    ModuleType,
)
from app.intelligence.architecture.modules import discover_modules
from app.intelligence.ids import edge_id, symbol_ref_to_node_id
from app.intelligence.models import (
    EdgeType,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    NodeKind,
)


def _file(path: str, lang: str = "python") -> KnowledgeNode:
    return KnowledgeNode(
        id=f"file:{path}",
        kind=NodeKind.FILE,
        name=path,
        qualified_name=path,
        file_path=path,
        language=lang,
    )


def _fn(path: str, name: str) -> KnowledgeNode:
    qn = f"{path}::{name}"
    return KnowledgeNode(
        id=symbol_ref_to_node_id(qn),
        kind=NodeKind.FUNCTION,
        name=name,
        qualified_name=qn,
        file_path=path,
        language="python",
        start_line=1,
        end_line=5,
    )


def _call(src_qn: str, dst_qn: str) -> KnowledgeEdge:
    s = symbol_ref_to_node_id(src_qn)
    t = symbol_ref_to_node_id(dst_qn)
    return KnowledgeEdge(
        id=edge_id(EdgeType.CALL.value, s, t),
        source_id=s,
        target_id=t,
        edge_type=EdgeType.CALL,
        confidence="high",
    )


def _mod(name: str, files: list[str], mtype: ModuleType = ModuleType.FEATURE) -> ArchitectureModule:
    return ArchitectureModule(
        id=f"mod:{name}",
        name=name,
        path_roots=[name],
        module_type=mtype,
        boundary_confidence="medium",
        file_paths=files,
        evidence=[
            EvidenceRef(kind=EvidenceRefKind.MODULE, module_id=f"mod:{name}", note="test")
        ],
    )


def test_detects_circular_dependency():
    # a/foo.py ↔ b/bar.py via calls
    nodes = [
        _file("a/foo.py"),
        _file("b/bar.py"),
        _fn("a/foo.py", "fa"),
        _fn("b/bar.py", "fb"),
    ]
    edges = [
        _call("a/foo.py::fa", "b/bar.py::fb"),
        _call("b/bar.py::fb", "a/foo.py::fa"),
    ]
    graph = KnowledgeGraph(repo_id="cyc", nodes=nodes, edges=edges)
    mmap = ModuleMap(modules=[_mod("a", ["a/foo.py"]), _mod("b", ["b/bar.py"])])
    result = analyze_module_dependencies(graph, mmap)
    assert result.metrics.cycle_count >= 1
    cycles = [
        f
        for f in result.findings
        if f.category == ArchitectureFindingCategory.CIRCULAR_DEPENDENCY
    ]
    assert cycles
    assert cycles[0].evidence
    assert cycles[0].related_modules


def test_high_fan_out_finding():
    # hub depends on many leaves
    files = [f"hub/h.py"] + [f"m{i}/x.py" for i in range(6)]
    nodes = [_file(fp) for fp in files]
    nodes += [_fn("hub/h.py", "run")]
    nodes += [_fn(f"m{i}/x.py", "f") for i in range(6)]
    edges = [_call("hub/h.py::run", f"m{i}/x.py::f") for i in range(6)]
    graph = KnowledgeGraph(repo_id="fo", nodes=nodes, edges=edges)
    mods = [_mod("hub", ["hub/h.py"])] + [
        _mod(f"m{i}", [f"m{i}/x.py"]) for i in range(6)
    ]
    result = analyze_module_dependencies(
        graph, ModuleMap(modules=mods), high_fan_out_threshold=5
    )
    assert result.metrics.max_fan_out >= 5
    fanout = [f for f in result.findings if f.reason == "high_fan_out"]
    assert fanout
    assert fanout[0].evidence
    assert "hub" in fanout[0].title.lower() or "hub" in fanout[0].detail.lower()


def test_metrics_on_spring_fixture(tmp_path):
    from pathlib import Path

    from tests.helpers_flow import SPRING_LOGIN, ingest_fixture, seed_spring_login_calls

    _, kg = ingest_fixture(SPRING_LOGIN, Path(tmp_path))
    kg = seed_spring_login_calls(kg)
    mmap = discover_modules(kg)
    result = analyze_module_dependencies(kg, mmap, high_fan_out_threshold=10)
    assert result.metrics.module_count >= 2
    assert result.metrics.cross_module_edges >= 1
    assert "mod:auth" in result.metrics.per_module or any(
        "auth" in k for k in result.metrics.per_module
    )
