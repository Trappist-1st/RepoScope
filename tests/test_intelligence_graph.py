"""Iteration 1: DependencyGraph → KnowledgeGraph adapter tests."""

from __future__ import annotations

from pathlib import Path

from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.graph.builder import DependencyGraphBuilder
from app.ingestion import IngestionPipeline
from app.intelligence import (
    EdgeType,
    NodeKind,
    build_knowledge_graph,
    children_of,
    file_path_to_node_id,
    find_by_qualified_name,
    get_node,
    load_knowledge_graph,
    neighbors,
    node_id_to_symbol_ref,
    save_knowledge_graph,
    symbol_ref_to_node_id,
)
from app.models.schemas import CallEdge, DependencyGraph, FileDependencyEdge
from app.parsing.ast_parser import AstParser
from tests.conftest import SAMPLE_REPO


def _python_graph():
    parser = AstParser()
    files = {
        "py_pkg/a.py": (SAMPLE_REPO / "py_pkg/a.py").read_text(encoding="utf-8"),
        "py_pkg/b.py": (SAMPLE_REPO / "py_pkg/b.py").read_text(encoding="utf-8"),
    }
    definitions_by_file = {
        path: parser.parse_definitions(content, "python") for path, content in files.items()
    }
    graph = DependencyGraphBuilder().build(
        repo_id="test-kg",
        commit_hash="abc",
        files=files,
        definitions_by_file=definitions_by_file,
    )
    return graph, definitions_by_file


def test_id_roundtrip():
    ref = "py_pkg/a.py::Helper.shout"
    nid = symbol_ref_to_node_id(ref)
    assert nid == "sym:py_pkg/a.py::Helper.shout"
    assert node_id_to_symbol_ref(nid) == ref
    assert file_path_to_node_id("py_pkg/a.py") == "file:py_pkg/a.py"
    assert symbol_ref_to_node_id(nid) == nid  # idempotent


def test_python_projection_nodes_and_parents():
    graph, defs = _python_graph()
    kg = build_knowledge_graph(graph, defs)

    assert kg.schema_version == "1.0"
    assert kg.source.definitions is True
    assert kg.source.inherit_supported is True
    # sample_repo has no extends/implements; count may be zero
    assert kg.stats.edge_counts.get("inherit", 0) == 0

    file_a = get_node(kg, "file:py_pkg/a.py")
    assert file_a is not None
    assert file_a.kind == NodeKind.FILE

    helper = find_by_qualified_name(kg, "py_pkg/a.py::Helper")
    assert helper is not None
    assert helper.kind == NodeKind.CLASS
    assert helper.parent_id == "file:py_pkg/a.py"
    assert helper.start_line is not None

    shout = find_by_qualified_name(kg, "py_pkg/a.py::Helper.shout")
    assert shout is not None
    assert shout.kind == NodeKind.METHOD
    assert shout.parent_id == helper.id

    greet = find_by_qualified_name(kg, "py_pkg/a.py::greet")
    assert greet is not None
    assert greet.kind == NodeKind.FUNCTION
    assert greet.parent_id == "file:py_pkg/a.py"

    kids = children_of(kg, helper.id)
    assert any(k.qualified_name.endswith("Helper.shout") for k in kids)


def test_import_and_call_edges():
    graph, defs = _python_graph()
    kg = build_knowledge_graph(graph, defs)

    imports = [
        e
        for e in kg.edges
        if e.edge_type == EdgeType.IMPORT
        and e.source_id == "file:py_pkg/b.py"
        and e.target_id == "file:py_pkg/a.py"
    ]
    assert imports
    assert imports[0].confidence == "high"

    # Caller attribution follows DependencyGraphBuilder (may be class-scoped).
    greet_callers = [
        e.source_id
        for e in kg.edges
        if e.edge_type == EdgeType.CALL and e.target_id == "sym:py_pkg/a.py::greet"
    ]
    assert greet_callers, f"expected call→greet; got {[e.id for e in kg.edges if e.edge_type == EdgeType.CALL]}"
    assert any("Helper" in c or "shout" in c or c.endswith("::run") for c in greet_callers)
    same_file_calls = [
        e for e in kg.edges if e.edge_type == EdgeType.CALL and e.meta.get("same_file")
    ]
    assert same_file_calls
    assert all(e.confidence == "high" for e in same_file_calls)


def test_neighbors_query():
    graph, defs = _python_graph()
    kg = build_knowledge_graph(graph, defs)
    out = neighbors(kg, "file:py_pkg/b.py", edge_type=EdgeType.IMPORT, direction="out")
    assert any(e.target_id == "file:py_pkg/a.py" for e in out)


def test_without_definitions_shadow_nodes():
    graph = DependencyGraph(
        repo_id="shadow",
        commit_hash=None,
        file_edges=[
            FileDependencyEdge(source="a.py", target="b.py", edge_type="imports"),
        ],
        call_edges=[
            CallEdge(
                caller="a.py::foo",
                callee="b.py::Bar.baz",
                same_file=False,
            ),
        ],
    )
    kg = build_knowledge_graph(graph, None)
    assert kg.source.definitions is False
    assert get_node(kg, "file:a.py") is not None
    assert get_node(kg, "sym:a.py::foo") is not None
    assert get_node(kg, "sym:b.py::Bar.baz") is not None
    assert get_node(kg, "sym:b.py::Bar") is not None  # inferred class parent
    assert kg.stats.edge_counts["call"] == 1
    assert kg.stats.edge_counts["import"] == 1
    assert kg.stats.orphan_symbol_refs  # shadow nodes recorded


def test_edge_dedupe():
    graph = DependencyGraph(
        repo_id="dedupe",
        file_edges=[
            FileDependencyEdge(source="a.py", target="b.py"),
            FileDependencyEdge(source="a.py", target="b.py"),
        ],
        call_edges=[
            CallEdge(caller="a.py::f", callee="b.py::g", same_file=False),
            CallEdge(caller="a.py::f", callee="b.py::g", same_file=False),
        ],
    )
    # DependencyGraphBuilder would dedupe; adapter also keys by edge id
    kg = build_knowledge_graph(graph, None)
    assert kg.stats.edge_counts["import"] == 1
    assert kg.stats.edge_counts["call"] == 1


def test_io_roundtrip(tmp_path: Path):
    graph, defs = _python_graph()
    kg = build_knowledge_graph(graph, defs)
    path = save_knowledge_graph(kg, artifact_dir=tmp_path)
    assert path.exists()
    loaded = load_knowledge_graph(kg.repo_id, artifact_dir=tmp_path)
    assert loaded.repo_id == kg.repo_id
    assert len(loaded.nodes) == len(kg.nodes)
    assert len(loaded.edges) == len(kg.edges)
    assert loaded.stats.node_counts == kg.stats.node_counts


def test_ingestion_writes_knowledge_graph(tmp_path: Path):
    pipe = IngestionPipeline(
        workspace_root=tmp_path / "ws",
        artifact_dir=tmp_path / "artifacts",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    result = pipe.run(str(SAMPLE_REPO))
    kg_path = tmp_path / "artifacts" / result.repo_id / "knowledge_graph.json"
    assert kg_path.exists()
    # legacy artifacts untouched
    assert (tmp_path / "artifacts" / result.repo_id / "graph.json").exists()
    assert (tmp_path / "artifacts" / result.repo_id / "chunks.json").exists()

    kg = pipe.load_knowledge_graph(result.repo_id)
    assert kg.repo_id == result.repo_id
    assert kg.source.inherit_supported is True
    assert kg.stats.node_counts.get("file", 0) >= 1
