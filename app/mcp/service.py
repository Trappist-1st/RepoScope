from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from app.audit import (
    AgentRunRecord,
    AgentRunStore,
    RunStateCache,
    create_agent_run_store,
    create_run_state_cache,
    new_run_id,
)
from app.config import settings
from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.graph.query import callees_of, callers_of, file_imports, files_imported_by
from app.ingestion import IngestionPipeline
from app.mcp.schemas import (
    CitationOut,
    DependenciesResult,
    DependencyEdgeOut,
    Evidence,
    FindingOut,
    KeyFlow,
    MCPMeta,
    ModuleSummary,
    RefactorResult,
    RefactorSuggestion,
    RepoSummaryResult,
    TraceFlowResult,
    ArchitectureResult,
)
from app.models.schemas import Chunk, DependencyGraph, IngestResult
from app.retrieval import IndexRequest, RetrievalService
from app.retrieval.config import load_retrieval_config
from app.retrieval.embedder import HashEmbedder
from app.retrieval.rerank import IdentityReranker
from app.retrieval.vector_store import InMemoryVectorStore
from app.workflow import WorkflowInput, create_default_runner
from app.workflow.analyzers import Analyzer
from app.workflow.schemas import Finding

SNIPPET_MAX = 400
IndexingStatus = Literal["cached", "incremental", "full_reindex"]


class RepoScopeFacade:
    """Shared business logic for FastAPI + MCP."""

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        artifact_dir: Path | None = None,
        audit_store: AgentRunStore | None = None,
        state_cache: RunStateCache | None = None,
        use_hash_embedder: bool = True,
        analyzer: Analyzer | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or settings.workspace_root)
        self.artifact_dir = Path(artifact_dir or settings.artifact_dir)
        self.audit_store = audit_store or create_agent_run_store(settings.database_url)
        self.state_cache = state_cache or create_run_state_cache(settings.redis_url)
        self.files_repo = InMemoryFilesRepository()
        self.repos_repo = InMemoryReposRepository()
        self.ingestion = IngestionPipeline(
            workspace_root=self.workspace_root,
            artifact_dir=self.artifact_dir,
            files_repo=self.files_repo,
            repos_repo=self.repos_repo,
        )
        cfg = load_retrieval_config()
        self.retrieval = RetrievalService(
            config=cfg,
            artifact_dir=self.artifact_dir,
            embedder=HashEmbedder() if use_hash_embedder else None,
            vector_store=InMemoryVectorStore(),
            reranker=IdentityReranker(),
        )
        self.runner = create_default_runner(
            workspace_root=self.workspace_root,
            artifact_dir=self.artifact_dir,
            files_repo=self.files_repo,
            repos_repo=self.repos_repo,
            use_hash_embedder=use_hash_embedder,
            analyzer=analyzer,
        )

    def _audit_warnings(self) -> list[str]:
        warnings: list[str] = []
        w = self.audit_store.persistence_warning()
        if w:
            warnings.append(w)
        if self.state_cache.backend == "in_memory":
            warnings.append(
                "run_state_cache: in_memory (non-persistent, will be lost on restart)"
            )
        return warnings

    def ensure_indexed(
        self, repo_url: str, force_reindex: bool = False
    ) -> tuple[IngestResult, IndexingStatus]:
        result = self.ingestion.run(repo_url, force_full=force_reindex)
        chunks, _ = self.ingestion.load_artifacts(result.repo_id)
        self.retrieval.index(
            IndexRequest(
                repo_id=result.repo_id,
                chunks=chunks,
                force_reindex=force_reindex,
            )
        )
        if force_reindex:
            status: IndexingStatus = "full_reindex"
        elif result.changed_files or result.deleted_files:
            status = "incremental" if result.unchanged_count > 0 else "full_reindex"
        else:
            status = "cached"
        return result, status

    def get_repo_summary(
        self,
        repo_url: str,
        question: str | None = None,
        force_reindex: bool = False,
    ) -> RepoSummaryResult:
        t0 = time.perf_counter()
        ingest, indexing_status = self.ensure_indexed(repo_url, force_reindex=force_reindex)
        q = question or "Provide a structured architecture summary of this repository."
        run_id = new_run_id()
        warnings = self._audit_warnings()
        if indexing_status == "full_reindex":
            warnings.append("indexing_status=full_reindex (potentially slow for large repos)")

        self.state_cache.set(run_id, {"status": "running", "node": "workflow", "repo_id": ingest.repo_id})
        result = self.runner.run(
            WorkflowInput(question=q, repo_source=repo_url, intent_hint="summary")
        )

        findings_out = [
            FindingOut(
                claim=f.claim,
                evidence=self._finding_to_evidence(f),
                confidence=f.confidence,  # type: ignore[arg-type]
            )
            for f in result.findings
        ]
        modules = [
            ModuleSummary(
                name=(f.symbols[0] if f.symbols else (f.citations[0] if f.citations else "module")),
                role=f.claim,
                evidence=self._finding_to_evidence(f),
            )
            for f in result.findings[:5]
        ]
        key_flows = [
            KeyFlow(description=f.claim, evidence=self._finding_to_evidence(f))
            for f in result.findings
            if f.plan_step_idx == 2
        ] or [
            KeyFlow(description=f.claim, evidence=self._finding_to_evidence(f))
            for f in result.findings[:2]
        ]

        took_ms = int((time.perf_counter() - t0) * 1000)
        meta = MCPMeta(
            repo_id=result.repo_id or ingest.repo_id,
            repo_url=repo_url,
            commit_hash=ingest.commit_hash or None,
            run_id=run_id,
            took_ms=took_ms,
            indexing_status=indexing_status,
            warnings=warnings,
            audit_backend=self.audit_store.backend,
        )
        payload = RepoSummaryResult(
            meta=meta,
            summary={
                "headline": f"Architecture summary ({result.intent})",
                "modules": [m.model_dump() for m in modules],
                "key_flows": [k.model_dump() for k in key_flows],
                "findings": [f.model_dump() for f in findings_out],
            },
            report_markdown=result.report_markdown,
            review_passed=(result.status == "ok" and not result.low_confidence),
            low_confidence=result.low_confidence,
        )
        self._persist_run(
            run_id=run_id,
            repo_id=meta.repo_id,
            question=q,
            intent="summary",
            result=payload.model_dump(),
            review_passed=payload.review_passed,
            low_confidence=payload.low_confidence,
            status=result.status,
            warnings=warnings,
            node_timings={"total_ms": float(took_ms)},
        )
        return payload

    def query_dependencies(
        self,
        repo_url: str,
        symbol_name: str,
        direction: Literal["both", "callers", "callees", "imports"] = "both",
        limit: int = 20,
        force_reindex: bool = False,
    ) -> DependenciesResult:
        t0 = time.perf_counter()
        limit = max(1, min(limit, 50))
        ingest, indexing_status = self.ensure_indexed(repo_url, force_reindex=force_reindex)
        _, graph = self.ingestion.load_artifacts(ingest.repo_id)
        chunk_index = self._chunks_by_symbol(ingest.repo_id)
        resolved = self._resolve_symbol_refs(symbol_name, graph, chunk_index)

        notes: list[str] = []
        warnings = self._audit_warnings()
        if len(resolved) > 1:
            notes.append(
                "Multiple symbols matched; results are merged. "
                "Pass a fully-qualified `file::symbol` for precise targeting."
            )

        callers: list[DependencyEdgeOut] = []
        callees: list[DependencyEdgeOut] = []
        imports: list[DependencyEdgeOut] = []

        def edge_evidence(ref: str) -> list[Evidence]:
            chunk = chunk_index.get(ref)
            if chunk is None and "::" in ref:
                file_path, _, sym = ref.partition("::")
                if "." in sym:
                    chunk = chunk_index.get(f"{file_path}::{sym.split('.', 1)[0]}")
            if chunk is None:
                return []
            return [self._evidence_from_chunk(chunk)]

        if direction in {"both", "callers"}:
            for ref in resolved:
                for caller in callers_of(graph, ref):
                    callers.append(
                        DependencyEdgeOut(
                            symbol_ref=caller,
                            edge_type="calls",
                            same_file=caller.split("::", 1)[0] == ref.split("::", 1)[0],
                            evidence=edge_evidence(caller),
                        )
                    )
        if direction in {"both", "callees"}:
            for ref in resolved:
                for callee in callees_of(graph, ref):
                    callees.append(
                        DependencyEdgeOut(
                            symbol_ref=callee,
                            edge_type="calls",
                            same_file=callee.split("::", 1)[0] == ref.split("::", 1)[0],
                            evidence=edge_evidence(callee),
                        )
                    )
        if direction in {"both", "imports"}:
            files = {r.split("::", 1)[0] for r in resolved}
            if "/" in symbol_name or Path(symbol_name).suffix:
                files.add(symbol_name.replace("\\", "/"))
            for fpath in files:
                for target in file_imports(graph, fpath):
                    imports.append(
                        DependencyEdgeOut(source=fpath, target=target, edge_type="imports")
                    )
                for source in files_imported_by(graph, fpath):
                    imports.append(
                        DependencyEdgeOut(source=source, target=fpath, edge_type="imports")
                    )

        def dedupe(edges: list[DependencyEdgeOut]) -> list[DependencyEdgeOut]:
            seen: set[tuple] = set()
            out: list[DependencyEdgeOut] = []
            for e in edges:
                key = (e.symbol_ref, e.source, e.target, e.edge_type)
                if key in seen:
                    continue
                seen.add(key)
                out.append(e)
            return out

        callers = dedupe(callers)[:limit]
        callees = dedupe(callees)[:limit]
        imports = dedupe(imports)[:limit]
        total = len(callers) + len(callees) + len(imports)
        if direction == "both" and total >= limit:
            warnings.append(
                f"Results may be truncated at limit={limit}; "
                "narrow `direction` or raise `limit` (max 50)."
            )
        if any(not e.evidence for e in callers + callees):
            notes.append("Some edges have graph linkage only (no code chunk evidence).")

        took_ms = int((time.perf_counter() - t0) * 1000)
        run_id = new_run_id()
        meta = MCPMeta(
            repo_id=ingest.repo_id,
            repo_url=repo_url,
            commit_hash=ingest.commit_hash or None,
            run_id=run_id,
            took_ms=took_ms,
            indexing_status=indexing_status,
            warnings=warnings,
            audit_backend=self.audit_store.backend,
        )
        result = DependenciesResult(
            meta=meta,
            query={
                "symbol_name": symbol_name,
                "resolved_refs": resolved,
                "direction": direction,
                "limit": limit,
            },
            callers=callers,
            callees=callees,
            file_imports=imports,
            notes=notes,
        )
        self._persist_run(
            run_id=run_id,
            repo_id=ingest.repo_id,
            question=f"dependencies:{symbol_name}",
            intent="dependencies",
            result=result.model_dump(),
            review_passed=True,
            low_confidence=False,
            status="ok",
            warnings=warnings,
            node_timings={"total_ms": float(took_ms)},
        )
        return result

    def suggest_refactor(
        self,
        repo_url: str,
        file_path: str,
        focus: str | None = None,
        max_suggestions: int = 5,
        force_reindex: bool = False,
    ) -> RefactorResult:
        t0 = time.perf_counter()
        ingest, indexing_status = self.ensure_indexed(repo_url, force_reindex=force_reindex)
        focus_bit = f" Focus on {focus}." if focus else ""
        question = (
            f"Suggest refactors for file `{file_path}`.{focus_bit} "
            "Cite concrete symbols and coupling risks."
        )
        warnings = self._audit_warnings()
        run_id = new_run_id()
        self.state_cache.set(run_id, {"status": "running", "node": "workflow", "repo_id": ingest.repo_id})
        result = self.runner.run(
            WorkflowInput(question=question, repo_source=repo_url, intent_hint="refactor")
        )
        suggestions: list[RefactorSuggestion] = []
        for f in result.findings[:max_suggestions]:
            evidence = self._finding_to_evidence(f)
            conf = f.confidence
            if f.evidence_tier == "expanded" and conf == "high":
                conf = "medium"
            suggestions.append(
                RefactorSuggestion(
                    title=(f.claim[:80] + "…") if len(f.claim) > 80 else f.claim,
                    rationale=f.claim,
                    severity="medium" if conf != "low" else "low",
                    category=focus or "other",
                    evidence=evidence,
                    related_symbols=list(f.symbols),
                    confidence=conf,  # type: ignore[arg-type]
                )
            )

        took_ms = int((time.perf_counter() - t0) * 1000)
        meta = MCPMeta(
            repo_id=result.repo_id or ingest.repo_id,
            repo_url=repo_url,
            commit_hash=ingest.commit_hash or None,
            run_id=run_id,
            took_ms=took_ms,
            indexing_status=indexing_status,
            warnings=warnings,
            audit_backend=self.audit_store.backend,
        )
        out = RefactorResult(
            meta=meta,
            file_path=file_path,
            suggestions=suggestions,
            report_markdown=result.report_markdown,
            review_passed=(result.status == "ok" and not result.low_confidence),
            low_confidence=result.low_confidence,
        )
        self._persist_run(
            run_id=run_id,
            repo_id=meta.repo_id,
            question=question,
            intent="refactor",
            result=out.model_dump(),
            review_passed=out.review_passed,
            low_confidence=out.low_confidence,
            status=result.status,
            warnings=warnings,
            node_timings={"total_ms": float(took_ms)},
        )
        return out

    def trace_flow(
        self,
        repo_url: str,
        question: str,
        *,
        entry_hint: str | None = None,
        max_depth: int = 5,
        force_reindex: bool = False,
        use_retrieval: bool = True,
    ) -> TraceFlowResult:
        """Repository flow understanding via KnowledgeGraph + FlowTracer (no LangGraph)."""
        t0 = time.perf_counter()
        ingest, indexing_status = self.ensure_indexed(repo_url, force_reindex=force_reindex)
        warnings = self._audit_warnings()
        if indexing_status == "full_reindex":
            warnings.append("indexing_status=full_reindex (potentially slow for large repos)")

        run_id = new_run_id()
        self.state_cache.set(
            run_id,
            {"status": "running", "node": "trace_flow", "repo_id": ingest.repo_id},
        )

        kg = self._load_or_build_knowledge_graph(ingest.repo_id)
        retrieve_fn = None
        if use_retrieval:
            retrieve_fn = self._make_trace_retrieve_fn(ingest.repo_id)

        from app.intelligence.flow_format import format_flow_markdown
        from app.intelligence.flow_tracer import FlowTracer

        trace = FlowTracer(retrieve_fn=retrieve_fn).trace(
            kg,
            question,
            entry_hint=entry_hint,
            max_depth=max(1, min(max_depth, 10)),
        )
        report = format_flow_markdown(trace)
        low_confidence = trace.confidence == "low" or not trace.steps

        took_ms = int((time.perf_counter() - t0) * 1000)
        # Prefer tracer timing inside meta, but expose end-to-end in MCPMeta
        meta = MCPMeta(
            repo_id=ingest.repo_id,
            repo_url=repo_url,
            commit_hash=ingest.commit_hash or kg.commit_hash,
            run_id=run_id,
            took_ms=took_ms,
            indexing_status=indexing_status,
            warnings=warnings + list(trace.warnings),
            audit_backend=self.audit_store.backend,
        )
        out = TraceFlowResult(
            meta=meta,
            query={
                "question": question,
                "topic": trace.query.topic,
                "topic_terms": list(trace.query.topic_terms),
                "entry_hint": entry_hint,
                "max_depth": max_depth,
            },
            trace=trace.model_dump(mode="json"),
            report_markdown=report,
            low_confidence=low_confidence,
        )
        self._persist_run(
            run_id=run_id,
            repo_id=meta.repo_id,
            question=question,
            intent="trace",
            result=out.model_dump(),
            review_passed=None if low_confidence else True,
            low_confidence=low_confidence,
            status="partial" if low_confidence else "ok",
            warnings=meta.warnings,
            node_timings={"total_ms": float(took_ms), "trace_ms": float(trace.meta.took_ms)},
        )
        return out

    def analyze_architecture(
        self,
        repo_url: str,
        *,
        force_reindex: bool = False,
        include_flows: bool = False,
    ) -> ArchitectureResult:
        """Structured architecture intelligence (modules/patterns/coupling). No LangGraph."""
        t0 = time.perf_counter()
        ingest, indexing_status = self.ensure_indexed(repo_url, force_reindex=force_reindex)
        warnings = self._audit_warnings()
        if indexing_status == "full_reindex":
            warnings.append("indexing_status=full_reindex (potentially slow for large repos)")

        run_id = new_run_id()
        self.state_cache.set(
            run_id,
            {"status": "running", "node": "analyze_architecture", "repo_id": ingest.repo_id},
        )

        kg = self._load_or_build_knowledge_graph(ingest.repo_id)

        from app.intelligence.architecture import (
            ArchitectureAnalyzer,
            format_architecture_markdown,
        )

        arch = ArchitectureAnalyzer().analyze(
            kg,
            workspace_root=ingest.local_path,
            include_flows=include_flows,
        )
        markdown = format_architecture_markdown(arch)

        # low confidence if primary unknown and few/no high findings
        high_findings = sum(1 for f in arch.findings if f.confidence == "high")
        low_confidence = arch.primary_pattern.value == "unknown" and high_findings == 0

        took_ms = int((time.perf_counter() - t0) * 1000)
        meta = MCPMeta(
            repo_id=ingest.repo_id,
            repo_url=repo_url,
            commit_hash=ingest.commit_hash or kg.commit_hash,
            run_id=run_id,
            took_ms=took_ms,
            indexing_status=indexing_status,
            warnings=warnings + list(arch.warnings),
            audit_backend=self.audit_store.backend,
        )
        out = ArchitectureResult(
            meta=meta,
            report=arch.model_dump(mode="json"),
            report_markdown=markdown,
            primary_pattern=arch.primary_pattern.value,
            finding_count=len(arch.findings),
            low_confidence=low_confidence,
        )
        self._persist_run(
            run_id=run_id,
            repo_id=meta.repo_id,
            question="analyze_architecture",
            intent="architecture",
            result=out.model_dump(),
            review_passed=None if low_confidence else True,
            low_confidence=low_confidence,
            status="partial" if low_confidence else "ok",
            warnings=meta.warnings,
            node_timings={
                "total_ms": float(took_ms),
                "architecture_ms": float(arch.meta.took_ms),
            },
        )
        return out

    def _load_or_build_knowledge_graph(self, repo_id: str):
        from app.intelligence import build_knowledge_graph, try_load_knowledge_graph
        from app.models.schemas import Definition

        kg = try_load_knowledge_graph(repo_id, artifact_dir=self.artifact_dir)
        if kg is not None:
            return kg
        _, dep = self.ingestion.load_artifacts(repo_id)
        defs_path = self.artifact_dir / repo_id / "definitions.json"
        definitions_by_file: dict = {}
        if defs_path.exists():
            import json

            raw = json.loads(defs_path.read_text(encoding="utf-8"))
            definitions_by_file = {
                path: [Definition.model_validate(d) for d in defs]
                for path, defs in raw.items()
            }
        kg = build_knowledge_graph(dep, definitions_by_file or None)
        try:
            from app.intelligence import save_knowledge_graph

            save_knowledge_graph(kg, artifact_dir=self.artifact_dir)
        except Exception:
            pass
        return kg

    def _make_trace_retrieve_fn(self, repo_id: str):
        from app.retrieval.schemas import RetrieveRequest

        def _retrieve(query: str):
            try:
                resp = self.retrieval.retrieve(
                    RetrieveRequest(
                        repo_id=repo_id,
                        query=query,
                        final_top_n=8,
                        graph_expand=False,
                        skip_rerank=True,
                    )
                )
                return list(resp.hits)
            except Exception:
                return []

        return _retrieve

    def _chunks_by_symbol(self, repo_id: str) -> dict[str, Chunk]:
        chunks, _ = self.ingestion.load_artifacts(repo_id)
        index: dict[str, Chunk] = {}
        for c in chunks:
            if not c.symbol_name:
                continue
            index[f"{c.file_path}::{c.symbol_name}"] = c
            index[c.symbol_name] = c
            short = c.symbol_name.split(".")[-1]
            index[f"{c.file_path}::{short}"] = c
            index[short] = c
        return index

    def _evidence_from_chunk(
        self,
        chunk: Chunk,
        *,
        tier: Literal["direct", "expanded", "mixed", "none"] = "direct",
        reason: str | None = None,
        confidence: Literal["high", "medium", "low"] = "high",
    ) -> Evidence:
        if tier == "expanded" and confidence == "high":
            confidence = "medium"
        return Evidence(
            citation=CitationOut.from_parts(chunk.file_path, chunk.start_line, chunk.end_line),
            symbol_name=chunk.symbol_name,
            snippet=chunk.content[:SNIPPET_MAX],
            evidence_tier=tier,
            expansion_reason=reason,
            confidence=confidence,
        )

    def _finding_to_evidence(self, finding: Finding) -> list[Evidence]:
        out: list[Evidence] = []
        for cite in finding.citations:
            path, _, span = cite.rpartition(":")
            start_s, _, end_s = span.partition("-")
            try:
                start, end = int(start_s), int(end_s)
            except ValueError:
                continue
            tier = finding.evidence_tier if finding.evidence_tier != "none" else "direct"
            conf = finding.confidence
            if tier == "expanded" and conf == "high":
                conf = "medium"
            reason = finding.expansion_reasons[0] if finding.expansion_reasons else None
            out.append(
                Evidence(
                    citation=CitationOut.from_parts(path, start, end),
                    symbol_name=finding.symbols[0] if finding.symbols else None,
                    snippet="",
                    evidence_tier=tier,  # type: ignore[arg-type]
                    expansion_reason=reason,
                    confidence=conf,  # type: ignore[arg-type]
                )
            )
        return out

    def _resolve_symbol_refs(
        self,
        symbol_name: str,
        graph: DependencyGraph,
        chunk_index: dict[str, Chunk],
    ) -> list[str]:
        if "::" in symbol_name:
            return [symbol_name]
        refs: set[str] = set()
        for key, chunk in chunk_index.items():
            if key == symbol_name or key.endswith(f"::{symbol_name}"):
                refs.add(f"{chunk.file_path}::{chunk.symbol_name or symbol_name}")
            if chunk.symbol_name and (
                chunk.symbol_name == symbol_name
                or chunk.symbol_name.endswith(f".{symbol_name}")
            ):
                refs.add(f"{chunk.file_path}::{chunk.symbol_name}")
        for e in graph.call_edges:
            for side in (e.caller, e.callee):
                if side.endswith(f"::{symbol_name}") or side.endswith(f".{symbol_name}"):
                    refs.add(side)
        return sorted(refs)

    def _persist_run(
        self,
        *,
        run_id: str,
        repo_id: str,
        question: str,
        intent: str,
        result: dict,
        review_passed: bool | None,
        low_confidence: bool,
        status: str,
        warnings: list[str],
        node_timings: dict[str, float],
    ) -> None:
        self.audit_store.save(
            AgentRunRecord(
                run_id=run_id,
                repo_id=repo_id,
                question=question,
                intent=intent,
                node_timings=node_timings,
                result=result,
                review_passed=review_passed,
                low_confidence=low_confidence,
                status=status,
                warnings=warnings,
            )
        )
        self.state_cache.set(
            run_id,
            {
                "status": "done",
                "repo_id": repo_id,
                "intent": intent,
                "review_passed": review_passed,
            },
        )
