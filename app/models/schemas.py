from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"


class SuperTypeRef(BaseModel):
    """Unresolved superclass / interface name extracted from AST (simple name)."""

    name: str
    relation: Literal["extends", "implements"] = "extends"


class Definition(BaseModel):
    name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    language: str
    parent_name: str | None = None
    bases: list[SuperTypeRef] = Field(default_factory=list)


class Chunk(BaseModel):
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    content: str
    kind: str
    symbol_name: str | None = None
    language: str | None = None
    content_hash: str = ""


class FileIndexRecord(BaseModel):
    repo_id: str
    file_path: str
    content_hash: str
    last_indexed_at: str


ResolutionStrategy = Literal[
    "legacy",
    "import_map",
    "import_suffix",
    "same_module",
    "unique_name",
    "import_distance",
    "fuzzy",
    "type_resolved",
]


class FileDependencyEdge(BaseModel):
    source: str
    target: str
    edge_type: str = "imports"
    import_line: int | None = None


class CallEdge(BaseModel):
    caller: str
    callee: str
    edge_type: str = "calls"
    same_file: bool = True
    confidence: float = 1.0
    resolution_strategy: ResolutionStrategy = "legacy"
    call_line: int | None = None


class InheritEdge(BaseModel):
    """child symbol_ref inherits from / implements parent symbol_ref."""

    child: str
    parent: str
    relation: Literal["extends", "implements"] = "extends"
    same_file: bool = False
    confidence: float = 1.0
    resolution_strategy: ResolutionStrategy = "legacy"
    decl_line: int | None = None


class DependencyGraph(BaseModel):
    repo_id: str
    commit_hash: str | None = None
    file_edges: list[FileDependencyEdge] = Field(default_factory=list)
    call_edges: list[CallEdge] = Field(default_factory=list)
    inherit_edges: list[InheritEdge] = Field(default_factory=list)


class ParseResult(BaseModel):
    file_path: str
    language: str | None
    content_hash: str
    definitions: list[Definition]
    chunks: list[Chunk]
    parse_ok: bool


# "structure_cached": file bytes changed but no AST structure did (comments,
# formatting), so the previous graph is reused without a merge.
GraphUpdateMode = Literal["full", "merge", "cached", "structure_cached"]


class IngestResult(BaseModel):
    repo_id: str
    local_path: str
    commit_hash: str
    changed_files: list[str]
    deleted_files: list[str]
    unchanged_count: int
    parse_results: list[ParseResult]
    graph: DependencyGraph
    graph_update_mode: GraphUpdateMode = "full"
    sync_took_ms: int = 0
