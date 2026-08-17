"""Cascading call resolution (config.use_advanced_kg).

The fixture under tests/fixtures/name_conflict_repo defines `find_by_username`
three times on purpose: as a UserRepo method, as a module-level function in
admin_repo, and as a local helper in auth_service. Every test here pins one
disambiguation rule that a bare-name resolver gets wrong.
"""

from __future__ import annotations

from pathlib import Path

from app.graph.builder import DependencyGraphBuilder
from app.parsing.ast_parser import AstParser

FIXTURES = Path(__file__).parent / "fixtures"
CONFLICT = FIXTURES / "name_conflict_repo"


def _build(advanced: bool):
    parser = AstParser()
    files = {
        str(p.relative_to(CONFLICT)).replace("\\", "/"): p.read_text(encoding="utf-8")
        for p in sorted(CONFLICT.rglob("*.py"))
    }
    definitions_by_file = {
        path: parser.parse_definitions(content, "python")
        for path, content in files.items()
    }
    return DependencyGraphBuilder().build(
        repo_id="conflict",
        commit_hash=None,
        files=files,
        definitions_by_file=definitions_by_file,
        advanced=advanced,
    )


def _callees(graph, caller_suffix: str, callee_name: str) -> list[str]:
    """Callee refs for one caller. Methods are `file::Class.name`, so match on
    the trailing name segment rather than on `::name`."""
    return [
        e.callee
        for e in graph.call_edges
        if e.caller.endswith(caller_suffix)
        and e.callee.rsplit("::", 1)[-1].split(".")[-1] == callee_name
    ]


def test_receiver_beats_same_file_local_helper():
    """`repo.find_by_username()` binds to UserRepo, not the local helper.

    This is the original defect: auth_service.py defines its own
    find_by_username, and same-file preference used to win over the receiver's
    declared type.
    """
    graph = _build(advanced=True)
    callees = _callees(graph, "auth_service.py::login", "find_by_username")
    assert callees, "no call edge produced for repo.find_by_username"
    assert all("user_repo.py" in c for c in callees), callees
    assert not any("auth_service.py" in c for c in callees), callees


def test_import_map_disambiguates_unqualified_call():
    """A bare `find_by_username()` follows the import statement above it."""
    graph = _build(advanced=True)
    callees = _callees(graph, "admin_service.py::promote", "find_by_username")
    assert callees, "no call edge produced for the unqualified call"
    assert all("admin_repo.py" in c for c in callees), callees


def test_definition_header_is_not_a_call_site():
    """`def find_by_username(...)` must not register as calling itself.

    The regex fallback used to emit this, which in a repo with same-named
    functions turned into a bogus cross-file edge.
    """
    graph = _build(advanced=True)
    for edge in graph.call_edges:
        caller_name = edge.caller.rsplit("::", 1)[-1].split(".")[-1]
        callee_name = edge.callee.rsplit("::", 1)[-1].split(".")[-1]
        assert not (
            caller_name == "find_by_username" and callee_name == "find_by_username"
        ), edge


def test_no_self_loops_from_declaration_lines():
    """`class Dog(Animal):` and `def f(...)` are declarations, not calls."""
    graph = _build(advanced=True)
    assert not [e for e in graph.call_edges if e.caller == e.callee], graph.call_edges


def test_resolved_edges_carry_confidence_and_evidence():
    graph = _build(advanced=True)
    resolved = [
        e
        for e in graph.call_edges
        if e.callee.endswith("find_by_username") and not e.same_file
    ]
    assert resolved
    for edge in resolved:
        assert 0.0 < edge.confidence <= 1.0
        assert edge.resolution_strategy != "legacy"
        assert edge.call_line is not None and edge.call_line > 0


def test_legacy_mode_edges_are_untouched():
    """With the switch off, edges keep their pre-refactor shape exactly."""
    graph = _build(advanced=False)
    assert graph.call_edges
    for edge in graph.call_edges:
        assert edge.confidence == 1.0
        assert edge.resolution_strategy == "legacy"
