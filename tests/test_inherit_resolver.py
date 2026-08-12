"""Inherit edges + SymbolResolver regression (extract → resolve)."""

from __future__ import annotations

from pathlib import Path

from app.graph.builder import DependencyGraphBuilder
from app.graph.query import callees_of, children_of_type, parents_of
from app.graph.resolver import SymbolResolver
from app.intelligence import EdgeType, build_knowledge_graph, neighbors
from app.parsing.ast_parser import AstParser
from app.parsing.languages import detect_language

INHERIT = Path(__file__).parent / "fixtures" / "inherit_repo"


def _load_files(*rel_paths: str) -> dict[str, str]:
    return {
        rel: (INHERIT / rel).read_text(encoding="utf-8") for rel in rel_paths
    }


def _defs_for(files: dict[str, str]) -> dict[str, list]:
    parser = AstParser()
    out = {}
    for path, content in files.items():
        lang = detect_language(path)
        assert lang is not None, path
        out[path] = parser.parse_definitions(content, lang)
    return out


def test_python_bases_extracted():
    parser = AstParser()
    content = (INHERIT / "animal/dog.py").read_text(encoding="utf-8")
    defs = parser.parse_definitions(content, "python")
    dog = next(d for d in defs if d.name == "Dog")
    assert any(b.name == "Animal" and b.relation == "extends" for b in dog.bases)


def test_java_extends_and_implements_extracted():
    parser = AstParser()
    impl = parser.parse_definitions(
        (INHERIT / "service/UserServiceImpl.java").read_text(encoding="utf-8"),
        "java",
    )
    cls = next(d for d in impl if d.name == "UserServiceImpl")
    assert any(b.name == "UserService" and b.relation == "implements" for b in cls.bases)

    auth = parser.parse_definitions(
        (INHERIT / "service/AuthController.java").read_text(encoding="utf-8"),
        "java",
    )
    ctrl = next(d for d in auth if d.name == "AuthController")
    assert any(b.name == "BaseController" and b.relation == "extends" for b in ctrl.bases)


def test_python_cross_file_inherit_edge():
    files = _load_files("animal/base.py", "animal/dog.py")
    defs = _defs_for(files)
    graph = DependencyGraphBuilder().build(
        repo_id="inherit-py",
        commit_hash=None,
        files=files,
        definitions_by_file=defs,
    )
    dog = "animal/dog.py::Dog"
    animal = "animal/base.py::Animal"
    assert animal in parents_of(graph, dog)
    assert dog in children_of_type(graph, animal)
    edge = next(e for e in graph.inherit_edges if e.child == dog)
    assert edge.relation == "extends"


def test_java_inherit_and_impl_prefer_call_resolution():
    files = _load_files(
        "service/UserService.java",
        "service/UserServiceImpl.java",
        "service/BaseController.java",
        "service/AuthController.java",
    )
    defs = _defs_for(files)
    graph = DependencyGraphBuilder().build(
        repo_id="inherit-java",
        commit_hash=None,
        files=files,
        definitions_by_file=defs,
    )

    assert "service/BaseController.java::BaseController" in parents_of(
        graph, "service/AuthController.java::AuthController"
    )
    assert "service/UserService.java::UserService" in parents_of(
        graph, "service/UserServiceImpl.java::UserServiceImpl"
    )
    impl_edge = next(
        e
        for e in graph.inherit_edges
        if e.child.endswith("::UserServiceImpl")
    )
    assert impl_edge.relation == "implements"

    # field.method() should prefer *Impl (resolver regression)
    ctrl_login = "service/AuthController.java::AuthController.login"
    impl_find = "service/UserServiceImpl.java::UserServiceImpl.findUser"
    assert impl_find in callees_of(graph, ctrl_login) or any(
        e.caller == ctrl_login and e.callee == impl_find for e in graph.call_edges
    ), graph.call_edges


def test_typescript_extends_edge():
    files = _load_files("ui/base.ts", "ui/child.ts")
    defs = _defs_for(files)
    graph = DependencyGraphBuilder().build(
        repo_id="inherit-ts",
        commit_hash=None,
        files=files,
        definitions_by_file=defs,
    )
    child = "ui/child.ts::FancyWidget"
    base = "ui/base.ts::WidgetBase"
    assert base in parents_of(graph, child)


def test_knowledge_graph_projects_inherit_edges():
    files = _load_files("animal/base.py", "animal/dog.py")
    defs = _defs_for(files)
    graph = DependencyGraphBuilder().build(
        repo_id="inherit-kg",
        commit_hash="x",
        files=files,
        definitions_by_file=defs,
    )
    kg = build_knowledge_graph(graph, defs)
    assert kg.source.inherit_supported is True
    assert kg.stats.edge_counts.get("inherit", 0) >= 1
    out = neighbors(
        kg,
        "sym:animal/dog.py::Dog",
        edge_type=EdgeType.INHERIT,
        direction="out",
    )
    assert any(e.target_id == "sym:animal/base.py::Animal" for e in out)
    assert out[0].meta.get("relation") == "extends"


def test_symbol_resolver_prefer_impl():
    files = _load_files(
        "service/UserService.java",
        "service/UserServiceImpl.java",
        "service/AuthController.java",
        "service/BaseController.java",
    )
    defs = _defs_for(files)
    builder = DependencyGraphBuilder()
    path_index = builder._build_path_index(files.keys())
    # Build import maps the same way as DependencyGraphBuilder
    import_map_by_file: dict[str, dict[str, str]] = {}
    for file_path, content in files.items():
        language = detect_language(file_path)
        _, name_to_file, _ = builder._resolve_imports(
            file_path, content, language, path_index, files
        )
        import_map_by_file[file_path] = name_to_file

    resolver = SymbolResolver(
        files=files,
        definitions_by_file=defs,
        path_index=path_index,
        import_map_by_file=import_map_by_file,
    )
    hit = resolver.resolve_method_on_type(
        type_name="UserService",
        method_name="findUser",
        from_file="service/AuthController.java",
        prefer_impl=True,
    )
    assert hit == "service/UserServiceImpl.java::UserServiceImpl.findUser"

    parent = resolver.resolve_type(
        "BaseController",
        from_file="service/AuthController.java",
    )
    assert parent == "service/BaseController.java::BaseController"
