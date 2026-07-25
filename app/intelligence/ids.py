"""Stable node ID helpers — interop with DependencyGraph symbol_ref."""

from __future__ import annotations

_FILE_PREFIX = "file:"
_SYM_PREFIX = "sym:"


def file_path_to_node_id(file_path: str) -> str:
    path = file_path.replace("\\", "/")
    return f"{_FILE_PREFIX}{path}"


def symbol_ref_to_node_id(symbol_ref: str) -> str:
    """Convert legacy `path::Symbol` / `path::Class.method` to node id."""
    ref = symbol_ref.replace("\\", "/")
    if ref.startswith(_SYM_PREFIX) or ref.startswith(_FILE_PREFIX):
        return ref
    return f"{_SYM_PREFIX}{ref}"


def node_id_to_symbol_ref(node_id: str) -> str | None:
    """Return legacy symbol_ref for sym nodes; None for file / unknown."""
    if node_id.startswith(_SYM_PREFIX):
        return node_id[len(_SYM_PREFIX) :]
    return None


def node_id_to_file_path(node_id: str) -> str | None:
    if node_id.startswith(_FILE_PREFIX):
        return node_id[len(_FILE_PREFIX) :]
    if node_id.startswith(_SYM_PREFIX):
        ref = node_id[len(_SYM_PREFIX) :]
        if "::" in ref:
            return ref.split("::", 1)[0]
    return None


def parse_symbol_ref(symbol_ref: str) -> tuple[str, str]:
    """Split `path::Name` into (file_path, symbol_part)."""
    ref = symbol_ref.replace("\\", "/")
    if ref.startswith(_SYM_PREFIX):
        ref = ref[len(_SYM_PREFIX) :]
    if "::" not in ref:
        raise ValueError(f"Invalid symbol_ref (missing '::'): {symbol_ref!r}")
    file_path, _, symbol_part = ref.partition("::")
    return file_path, symbol_part


def edge_id(edge_type: str, source_id: str, target_id: str) -> str:
    return f"{edge_type}:{source_id}->{target_id}"
