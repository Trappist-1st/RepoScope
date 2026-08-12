from __future__ import annotations

from app.models.schemas import DependencyGraph


def callers_of(graph: DependencyGraph, symbol_ref: str) -> list[str]:
    return sorted({e.caller for e in graph.call_edges if e.callee == symbol_ref})


def callees_of(graph: DependencyGraph, symbol_ref: str) -> list[str]:
    return sorted({e.callee for e in graph.call_edges if e.caller == symbol_ref})


def file_imports(graph: DependencyGraph, file_path: str) -> list[str]:
    return sorted({e.target for e in graph.file_edges if e.source == file_path})


def files_imported_by(graph: DependencyGraph, file_path: str) -> list[str]:
    return sorted({e.source for e in graph.file_edges if e.target == file_path})


def parents_of(graph: DependencyGraph, symbol_ref: str) -> list[str]:
    """Direct superclass / interface symbol_refs for a type."""
    return sorted({e.parent for e in graph.inherit_edges if e.child == symbol_ref})


def children_of_type(graph: DependencyGraph, symbol_ref: str) -> list[str]:
    """Types that extend / implement the given type."""
    return sorted({e.child for e in graph.inherit_edges if e.parent == symbol_ref})
