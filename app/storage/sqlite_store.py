"""Single-file SQLite backend for knowledge-graph and chunk artifacts.

This is a storage backend, not a query engine. ``load_knowledge_graph_db``
returns a fully materialised :class:`KnowledgeGraph`, exactly like the JSON
loader, so the 37 in-memory scans across ``app/intelligence/`` keep working
untouched. What it buys is differential writes: a one-line edit no longer
rewrites a multi-megabyte artifact from scratch.

Indexes on ``file_path`` / ``qualified_name`` / endpoint columns are created
even though nothing reads them yet, so a later query-pushdown pass does not
need a migration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import settings
from app.intelligence.models import (
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeGraphSource,
    KnowledgeGraphStats,
    KnowledgeNode,
)
from app.models.schemas import Chunk

DB_NAME = "reposcope.db"
SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path      TEXT,
    start_line     INTEGER,
    end_line       INTEGER,
    language       TEXT,
    parent_id      TEXT,
    meta_json      TEXT NOT NULL DEFAULT '{}',
    row_hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_file  ON kg_nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_qname ON kg_nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_kind  ON kg_nodes(kind);

CREATE TABLE IF NOT EXISTS kg_edges (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL,
    target_id           TEXT NOT NULL,
    edge_type           TEXT NOT NULL,
    confidence          TEXT NOT NULL DEFAULT 'high',
    confidence_score    REAL,
    resolution_strategy TEXT,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    meta_json           TEXT NOT NULL DEFAULT '{}',
    row_hash            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_edges_src  ON kg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_tgt  ON kg_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_type ON kg_edges(edge_type);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    ordinal      INTEGER NOT NULL DEFAULT 0,
    file_path    TEXT NOT NULL,
    start_line   INTEGER NOT NULL,
    end_line     INTEGER NOT NULL,
    content      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    symbol_name  TEXT,
    language     TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    row_hash     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file   ON chunks(file_path);
CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol_name);
"""


def db_path(repo_id: str, artifact_dir: Path | None = None) -> Path:
    base = Path(artifact_dir or settings.artifact_dir)
    return base / repo_id / DB_NAME


@contextmanager
def connect(
    path: Path, *, create: bool = True, write: bool = False
) -> Iterator[sqlite3.Connection]:
    """Open a connection and always close it.

    Windows keeps a file lock for as long as the handle is open, which makes
    pytest ``tmp_path`` teardown fail with PermissionError. Every access goes
    through this manager so no handle outlives a call.

    ``write`` controls the closing WAL checkpoint. Checkpointing is only
    meaningful after a write, and doing it on every read made ``context_explore``
    roughly eight times slower than the JSON backend.
    """
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        if write:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        if write:
            try:
                # Fold the WAL back into the main db so artifact-size
                # measurements are not inflated by -wal / -shm sidecars.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        conn.close()


def _row_hash(values: tuple[Any, ...]) -> str:
    payload = "\x1f".join("" if v is None else str(v) for v in values)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def _sync_table(
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    columns: list[str],
    rows: list[tuple[Any, ...]],
) -> dict[str, int]:
    """Write only rows that are new or whose hash changed; delete the rest.

    ``rows`` carry the key first and ``row_hash`` last.
    """
    existing = {
        str(r[0]): str(r[1])
        for r in conn.execute(f"SELECT {key_column}, row_hash FROM {table}")
    }
    incoming = {str(r[0]): str(r[-1]) for r in rows}

    changed = [r for r in rows if existing.get(str(r[0])) != str(r[-1])]
    removed = [(k,) for k in existing.keys() - incoming.keys()]

    if removed:
        conn.executemany(f"DELETE FROM {table} WHERE {key_column} = ?", removed)
    if changed:
        placeholders = ", ".join("?" * len(columns))
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            changed,
        )
    return {
        "written": len(changed),
        "deleted": len(removed),
        "unchanged": len(rows) - len(changed),
    }


# --------------------------------------------------------------------------
# Knowledge graph
# --------------------------------------------------------------------------

_NODE_COLUMNS = [
    "id",
    "kind",
    "name",
    "qualified_name",
    "file_path",
    "start_line",
    "end_line",
    "language",
    "parent_id",
    "meta_json",
    "row_hash",
]

_EDGE_COLUMNS = [
    "id",
    "source_id",
    "target_id",
    "edge_type",
    "confidence",
    "confidence_score",
    "resolution_strategy",
    "evidence_json",
    "meta_json",
    "row_hash",
]


def _node_row(node: KnowledgeNode) -> tuple[Any, ...]:
    meta_json = json.dumps(node.meta, ensure_ascii=False, sort_keys=True)
    values = (
        node.id,
        node.kind.value,
        node.name,
        node.qualified_name,
        node.file_path,
        node.start_line,
        node.end_line,
        node.language,
        node.parent_id,
        meta_json,
    )
    return (*values, _row_hash(values))


def _edge_row(edge: KnowledgeEdge) -> tuple[Any, ...]:
    evidence_json = json.dumps(
        [e.model_dump(mode="json") for e in edge.evidence], ensure_ascii=False
    )
    meta_json = json.dumps(edge.meta, ensure_ascii=False, sort_keys=True)
    values = (
        edge.id,
        edge.source_id,
        edge.target_id,
        edge.edge_type.value,
        edge.confidence,
        edge.confidence_score,
        edge.resolution_strategy,
        evidence_json,
        meta_json,
    )
    return (*values, _row_hash(values))


def save_knowledge_graph_db(
    graph: KnowledgeGraph,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
) -> Path:
    target = path or db_path(graph.repo_id, artifact_dir)
    with connect(target, write=True) as conn:
        conn.executescript(_SCHEMA)
        _sync_table(
            conn, "kg_nodes", "id", _NODE_COLUMNS, [_node_row(n) for n in graph.nodes]
        )
        _sync_table(
            conn, "kg_edges", "id", _EDGE_COLUMNS, [_edge_row(e) for e in graph.edges]
        )
        meta = {
            "schema_version": graph.schema_version,
            "db_schema_version": SCHEMA_VERSION,
            "repo_id": graph.repo_id,
            "commit_hash": graph.commit_hash or "",
            "stats_json": json.dumps(graph.stats.model_dump(mode="json"), ensure_ascii=False),
            "source_json": json.dumps(graph.source.model_dump(mode="json"), ensure_ascii=False),
        }
        conn.executemany(
            "INSERT OR REPLACE INTO kg_meta (key, value) VALUES (?, ?)",
            list(meta.items()),
        )
        conn.commit()
    return target


def load_knowledge_graph_db(
    repo_id: str,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
) -> KnowledgeGraph:
    target = path or db_path(repo_id, artifact_dir)
    if not target.exists():
        raise FileNotFoundError(f"knowledge_graph db not found: {target}")

    with connect(target, create=False) as conn:
        if not _has_table(conn, "kg_nodes"):
            raise FileNotFoundError(f"knowledge_graph db has no kg_nodes: {target}")
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM kg_meta")}
        nodes = [
            KnowledgeNode(
                id=r["id"],
                kind=r["kind"],
                name=r["name"],
                qualified_name=r["qualified_name"],
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                language=r["language"],
                parent_id=r["parent_id"],
                meta=json.loads(r["meta_json"]),
            )
            for r in conn.execute(
                "SELECT * FROM kg_nodes ORDER BY kind, id"
            )
        ]
        edges = [
            KnowledgeEdge(
                id=r["id"],
                source_id=r["source_id"],
                target_id=r["target_id"],
                edge_type=r["edge_type"],
                confidence=r["confidence"],
                confidence_score=r["confidence_score"],
                resolution_strategy=r["resolution_strategy"],
                evidence=json.loads(r["evidence_json"]),
                meta=json.loads(r["meta_json"]),
            )
            for r in conn.execute("SELECT * FROM kg_edges ORDER BY id")
        ]

    stats = KnowledgeGraphStats.model_validate(json.loads(meta.get("stats_json") or "{}"))
    source = KnowledgeGraphSource.model_validate(json.loads(meta.get("source_json") or "{}"))
    return KnowledgeGraph(
        schema_version=meta.get("schema_version") or "1.0",
        repo_id=meta.get("repo_id") or repo_id,
        commit_hash=meta.get("commit_hash") or None,
        nodes=nodes,
        edges=edges,
        stats=stats,
        source=source,
    )


# --------------------------------------------------------------------------
# Chunks
# --------------------------------------------------------------------------

_CHUNK_COLUMNS = [
    "chunk_id",
    "ordinal",
    "file_path",
    "start_line",
    "end_line",
    "content",
    "kind",
    "symbol_name",
    "language",
    "content_hash",
    "row_hash",
]


def _chunk_row(chunk: Chunk, ordinal: int) -> tuple[Any, ...]:
    values = (
        chunk.chunk_id,
        ordinal,
        chunk.file_path,
        chunk.start_line,
        chunk.end_line,
        chunk.content,
        chunk.kind,
        chunk.symbol_name,
        chunk.language,
        chunk.content_hash,
    )
    return (*values, _row_hash(values))


def save_chunks(
    repo_id: str,
    chunks: list[Chunk],
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
) -> Path:
    target = path or db_path(repo_id, artifact_dir)
    with connect(target, write=True) as conn:
        conn.executescript(_SCHEMA)
        _sync_table(
            conn,
            "chunks",
            "chunk_id",
            _CHUNK_COLUMNS,
            [_chunk_row(c, i) for i, c in enumerate(chunks)],
        )
        conn.commit()
    return target


def load_chunks(
    repo_id: str,
    *,
    artifact_dir: Path | None = None,
    path: Path | None = None,
) -> list[Chunk]:
    target = path or db_path(repo_id, artifact_dir)
    if not target.exists():
        raise FileNotFoundError(f"chunks db not found: {target}")
    with connect(target, create=False) as conn:
        if not _has_table(conn, "chunks"):
            raise FileNotFoundError(f"chunks db has no chunks table: {target}")
        return [
            Chunk(
                chunk_id=r["chunk_id"],
                file_path=r["file_path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                content=r["content"],
                kind=r["kind"],
                symbol_name=r["symbol_name"],
                language=r["language"],
                content_hash=r["content_hash"],
            )
            # Retrieval breaks score ties by list position, so the JSON
            # backend's insertion order has to survive the round trip.
            for r in conn.execute("SELECT * FROM chunks ORDER BY ordinal, chunk_id")
        ]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None
