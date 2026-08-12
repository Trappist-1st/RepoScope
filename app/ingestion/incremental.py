from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db.postgres import FilesRepository, ReposRepository
from app.graph.builder import DependencyGraphBuilder
from app.ingestion.git_ops import ensure_repo
from app.ingestion.hashing import content_hash
from app.intelligence.adapter import build_knowledge_graph
from app.intelligence.io import (
    knowledge_graph_path,
    load_knowledge_graph,
    save_knowledge_graph,
    try_load_knowledge_graph,
)
from app.intelligence.models import KnowledgeGraph
from app.models.schemas import (
    Chunk,
    Definition,
    DependencyGraph,
    FileIndexRecord,
    GraphUpdateMode,
    IngestResult,
    ParseResult,
)
from app.parsing.ast_parser import AstParser
from app.parsing.chunker import Chunker
from app.parsing.languages import AST_LANGUAGES, SUPPORTED_EXTENSIONS, detect_language

# Prefer merge when the re-origin set stays small relative to the repo.
_MERGE_MAX_ORIGIN_FILES = 32
_MERGE_MAX_ORIGIN_RATIO = 0.35


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _affected_origin_paths(
    changed: list[str],
    deleted: list[str],
    existing: DependencyGraph,
) -> set[str]:
    """
    Files whose outgoing edges must be rebuilt after a change.

    Includes changed files plus prior callers/importers that linked into them,
    so merge_update can drop stale edges and re-attach without a full rebuild.
    """
    drop = set(changed) | set(deleted)
    affected = set(changed)
    for e in existing.file_edges:
        if e.target in drop or e.source in drop:
            affected.add(e.source)
    for e in existing.call_edges:
        caller_f = e.caller.split("::", 1)[0]
        callee_f = e.callee.split("::", 1)[0]
        if callee_f in drop or caller_f in drop:
            affected.add(caller_f)
    for e in existing.inherit_edges:
        child_f = e.child.split("::", 1)[0]
        parent_f = e.parent.split("::", 1)[0]
        if parent_f in drop or child_f in drop:
            affected.add(child_f)
    return affected


def iter_source_files(root: Path, exclude_dirs: frozenset[str] | None = None) -> list[Path]:
    exclude = exclude_dirs or settings.exclude_dirs
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in exclude for part in path.parts):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        results.append(path)
    return sorted(results)


class IngestionPipeline:
    def __init__(
        self,
        workspace_root: Path | None = None,
        files_repo: FilesRepository | None = None,
        repos_repo: ReposRepository | None = None,
        ast_parser: AstParser | None = None,
        chunker: Chunker | None = None,
        graph_builder: DependencyGraphBuilder | None = None,
        artifact_dir: Path | None = None,
    ) -> None:
        from app.db.postgres import create_repositories

        self.workspace_root = Path(workspace_root or settings.workspace_root)
        self.artifact_dir = Path(artifact_dir or settings.artifact_dir)
        if files_repo is None or repos_repo is None:
            default_files, default_repos = create_repositories(settings.database_url)
            self.files_repo = files_repo or default_files
            self.repos_repo = repos_repo or default_repos
        else:
            self.files_repo = files_repo
            self.repos_repo = repos_repo
        self.ast_parser = ast_parser or AstParser()
        self.chunker = chunker or Chunker(fallback_lines=settings.fallback_chunk_lines)
        self.graph_builder = graph_builder or DependencyGraphBuilder()

    def run(self, source: str, force_full: bool = False) -> IngestResult:
        t0 = time.perf_counter()
        checkout = ensure_repo(source, self.workspace_root)
        self.repos_repo.upsert_repo(
            checkout.repo_id,
            checkout.source,
            checkout.commit_hash,
            str(checkout.local_path),
        )

        source_files = iter_source_files(checkout.local_path)
        rel_paths = {
            p.relative_to(checkout.local_path).as_posix(): p for p in source_files
        }

        current_hashes: dict[str, str] = {}
        file_contents: dict[str, str] = {}
        for rel, abs_path in rel_paths.items():
            raw = abs_path.read_bytes()
            digest = content_hash(raw)
            current_hashes[rel] = digest
            file_contents[rel] = raw.decode("utf-8", errors="replace")

        previous = {} if force_full else self.files_repo.get_file_hashes(checkout.repo_id)
        changed = sorted(
            path for path, digest in current_hashes.items() if previous.get(path) != digest
        )
        deleted = sorted(path for path in previous if path not in current_hashes)
        unchanged_count = len(current_hashes) - len(changed)

        if deleted:
            self.files_repo.delete_files(checkout.repo_id, deleted)

        prev_chunks, prev_graph = self._try_load_artifacts(checkout.repo_id)
        cached_defs: dict[str, list[Definition]] = getattr(self, "_cached_definitions", {})

        unchanged_chunk_map: dict[str, list[Chunk]] = {}
        for ch in prev_chunks:
            unchanged_chunk_map.setdefault(ch.file_path, []).append(ch)

        definitions_by_file: dict[str, list[Definition]] = {}
        all_chunks: list[Chunk] = []
        parse_results: list[ParseResult] = []

        for rel in sorted(rel_paths.keys()):
            content = file_contents[rel]
            language = detect_language(rel)

            if rel not in changed and rel in cached_defs:
                definitions_by_file[rel] = cached_defs[rel]
                all_chunks.extend(unchanged_chunk_map.get(rel, []))
                continue

            definitions: list[Definition] = []
            parse_ok = False
            if language in AST_LANGUAGES:
                try:
                    definitions = self.ast_parser.parse_definitions(content, language)
                    parse_ok = True
                except Exception:
                    definitions = []
                    parse_ok = False

            chunks = self.chunker.chunk_file(rel, content, definitions, language)
            definitions_by_file[rel] = definitions
            all_chunks.extend(chunks)
            parse_results.append(
                ParseResult(
                    file_path=rel,
                    language=language,
                    content_hash=current_hashes[rel],
                    definitions=definitions,
                    chunks=chunks,
                    parse_ok=parse_ok,
                )
            )
            self.files_repo.upsert_file(
                FileIndexRecord(
                    repo_id=checkout.repo_id,
                    file_path=rel,
                    content_hash=current_hashes[rel],
                    last_indexed_at=_utc_now_iso(),
                )
            )

        graph, graph_update_mode = self._update_graph(
            repo_id=checkout.repo_id,
            commit_hash=checkout.commit_hash or None,
            file_contents=file_contents,
            definitions_by_file=definitions_by_file,
            changed=changed,
            deleted=deleted,
            force_full=force_full,
            previous_hashes=previous,
            prev_graph=prev_graph,
        )

        self._write_artifacts(checkout.repo_id, all_chunks, graph, definitions_by_file)
        self._ensure_knowledge_graph(checkout.repo_id, graph, definitions_by_file)

        return IngestResult(
            repo_id=checkout.repo_id,
            local_path=str(checkout.local_path),
            commit_hash=checkout.commit_hash,
            changed_files=changed,
            deleted_files=deleted,
            unchanged_count=unchanged_count,
            parse_results=parse_results,
            graph=graph,
            graph_update_mode=graph_update_mode,
            sync_took_ms=int((time.perf_counter() - t0) * 1000),
        )

    def _update_graph(
        self,
        *,
        repo_id: str,
        commit_hash: str | None,
        file_contents: dict[str, str],
        definitions_by_file: dict[str, list[Definition]],
        changed: list[str],
        deleted: list[str],
        force_full: bool,
        previous_hashes: dict[str, str],
        prev_graph: DependencyGraph,
    ) -> tuple[DependencyGraph, GraphUpdateMode]:
        def _full() -> DependencyGraph:
            return self.graph_builder.build(
                repo_id=repo_id,
                commit_hash=commit_hash,
                files=file_contents,
                definitions_by_file=definitions_by_file,
            )

        if force_full or not previous_hashes:
            return _full(), "full"

        if not changed and not deleted:
            if prev_graph.repo_id == repo_id and (
                prev_graph.file_edges or prev_graph.call_edges or prev_graph.inherit_edges
            ):
                return prev_graph, "cached"
            return _full(), "full"

        has_edges = bool(
            prev_graph.file_edges or prev_graph.call_edges or prev_graph.inherit_edges
        )
        if prev_graph.repo_id == repo_id and has_edges:
            origins = _affected_origin_paths(changed, deleted, prev_graph)
            origins &= set(file_contents.keys())
            n_files = max(1, len(file_contents))
            if (
                origins
                and len(origins) <= _MERGE_MAX_ORIGIN_FILES
                and (len(origins) / n_files) <= _MERGE_MAX_ORIGIN_RATIO
            ):
                partial = self.graph_builder.build(
                    repo_id=repo_id,
                    commit_hash=commit_hash,
                    files=file_contents,
                    definitions_by_file=definitions_by_file,
                    origin_paths=origins,
                )
                merged = self.graph_builder.merge_update(
                    prev_graph,
                    changed_paths=changed,
                    deleted_paths=deleted,
                    partial_graph=partial,
                    rebuild_origins=origins,
                )
                return merged, "merge"

        return _full(), "full"


    def load_artifacts(self, repo_id: str) -> tuple[list[Chunk], DependencyGraph]:
        chunks, graph = self._try_load_artifacts(repo_id)
        return chunks, graph

    def load_knowledge_graph(self, repo_id: str) -> KnowledgeGraph:
        """Load Code Intelligence Graph artifact (raises if missing)."""
        return load_knowledge_graph(repo_id, artifact_dir=self.artifact_dir)

    def try_load_knowledge_graph(self, repo_id: str) -> KnowledgeGraph | None:
        return try_load_knowledge_graph(repo_id, artifact_dir=self.artifact_dir)

    def _artifact_paths(self, repo_id: str) -> tuple[Path, Path, Path]:
        base = self.artifact_dir / repo_id
        return base / "chunks.json", base / "graph.json", base / "definitions.json"

    def _write_artifacts(
        self,
        repo_id: str,
        chunks: list[Chunk],
        graph: DependencyGraph,
        definitions_by_file: dict[str, list[Definition]],
    ) -> None:
        base = self.artifact_dir / repo_id
        base.mkdir(parents=True, exist_ok=True)
        chunks_path, graph_path, defs_path = self._artifact_paths(repo_id)
        chunks_path.write_text(
            json.dumps([c.model_dump() for c in chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        graph_path.write_text(
            json.dumps(graph.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        defs_payload = {
            path: [d.model_dump() for d in defs] for path, defs in definitions_by_file.items()
        }
        defs_path.write_text(
            json.dumps(defs_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_knowledge_graph(repo_id, graph, definitions_by_file)

    def _write_knowledge_graph(
        self,
        repo_id: str,
        graph: DependencyGraph,
        definitions_by_file: dict[str, list[Definition]],
    ) -> None:
        """Best-effort KG artifact; never fails the ingestion pipeline."""
        try:
            kg = build_knowledge_graph(graph, definitions_by_file)
            save_knowledge_graph(kg, artifact_dir=self.artifact_dir)
        except Exception:
            # Intelligence layer must not break ingest.
            pass

    def _ensure_knowledge_graph(
        self,
        repo_id: str,
        graph: DependencyGraph,
        definitions_by_file: dict[str, list[Definition]],
    ) -> None:
        """Rebuild KG when missing (e.g. artifacts from pre-intelligence runs)."""
        if knowledge_graph_path(repo_id, self.artifact_dir).exists():
            return
        self._write_knowledge_graph(repo_id, graph, definitions_by_file)

    def _try_load_artifacts(self, repo_id: str) -> tuple[list[Chunk], DependencyGraph]:
        chunks_path, graph_path, defs_path = self._artifact_paths(repo_id)
        chunks: list[Chunk] = []
        if chunks_path.exists():
            raw = json.loads(chunks_path.read_text(encoding="utf-8"))
            chunks = [Chunk.model_validate(item) for item in raw]

        if graph_path.exists():
            graph = DependencyGraph.model_validate(
                json.loads(graph_path.read_text(encoding="utf-8"))
            )
        else:
            graph = DependencyGraph(repo_id=repo_id)

        # Prefer definitions.json when reusing unchanged files
        if defs_path.exists():
            self._cached_definitions = {
                path: [Definition.model_validate(d) for d in defs]
                for path, defs in json.loads(defs_path.read_text(encoding="utf-8")).items()
            }
        else:
            self._cached_definitions = {}
        return chunks, graph
