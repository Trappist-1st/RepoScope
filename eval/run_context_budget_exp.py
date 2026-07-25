"""
Context Engineering budget experiment on a *real* repository.

Example:
  python -m eval.run_context_budget_exp --repo https://github.com/psf/requests.git
  python -m eval.run_context_budget_exp --repo D:/path/to/local/django

Writes: eval/reports/context_budget_exp.md
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.context_engine import assemble_context, load_context_config
from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline
from app.parsing.languages import SUPPORTED_EXTENSIONS
from app.retrieval import IndexRequest, RetrievalService, RetrieveRequest
from app.retrieval.config import EmbeddingConfig, RetrievalConfig, RerankConfig
from app.retrieval.embedder import HashEmbedder
from app.retrieval.rerank import IdentityReranker
from app.retrieval.vector_store import InMemoryVectorStore

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "eval" / "reports"


def count_source_loc(repo_path: Path) -> tuple[int, int]:
    files = 0
    lines = 0
    exclude = {".git", "node_modules", ".venv", "venv", "__pycache__", "docs", "tests", "test"}
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in exclude for part in path.parts):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        files += 1
        try:
            lines += sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return files, lines


def ensure_repo(source: str, workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    path = Path(source)
    if path.exists() and path.is_dir():
        return path.resolve()

    # Remote URL → shallow clone
    name = source.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = workspace / name
    if dest.exists() and (dest / ".git").exists():
        return dest
    subprocess.run(
        ["git", "clone", "--depth", "1", source, str(dest)],
        check=True,
    )
    return dest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        default="https://github.com/psf/requests.git",
        help="Git URL or local path to a real repository (not synthetic)",
    )
    parser.add_argument(
        "--query",
        default="How does the Session object send HTTP requests and handle adapters?",
    )
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=40)
    args = parser.parse_args()

    workspace = ROOT / "data" / "exp_workspace"
    artifact_dir = ROOT / "data" / "exp_artifacts"
    repo_path = ensure_repo(args.repo, workspace)
    n_files, n_loc = count_source_loc(repo_path)

    cfg_ctx = load_context_config()
    budget = args.budget or cfg_ctx.token_budget

    pipeline = IngestionPipeline(
        workspace_root=workspace,
        artifact_dir=artifact_dir,
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    ingest = pipeline.run(str(repo_path))
    chunks, graph = pipeline.load_artifacts(ingest.repo_id)

    retrieval_cfg = RetrievalConfig(
        vector_backend="inmemory",
        embedding=EmbeddingConfig(provider="hash", model="hash"),
        rerank=RerankConfig(enabled=False, provider="identity"),
    )
    service = RetrievalService(
        config=retrieval_cfg,
        artifact_dir=artifact_dir,
        embedder=HashEmbedder(),
        vector_store=InMemoryVectorStore(),
        reranker=IdentityReranker(),
    )
    service.index(IndexRequest(repo_id=ingest.repo_id, chunks=chunks, force_reindex=True))

    # Oversample candidates to stress the budget allocator
    resp = service.retrieve(
        RetrieveRequest(
            repo_id=ingest.repo_id,
            query=args.query,
            mode="hybrid",
            top_k_vector=args.top_k,
            top_k_bm25=args.top_k,
            rerank_top_n=args.top_k,
            final_top_n=args.top_k,
            graph_expand=True,
            skip_rerank=True,
        )
    )

    assembled = assemble_context(
        question=args.query,
        plan_hint="architecture of request sending path",
        hits=resp.hits,
        expanded_hits=resp.expanded_hits,
        graph=graph,
        config=cfg_ctx,
        budget=budget,
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "context_budget_exp.md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    kept_lines = []
    for b in sorted(assembled.kept_breakdowns, key=lambda x: -x.priority)[:15]:
        kept_lines.append(
            f"| `{b.citation}` | {b.priority:.3f} | {b.entry:.0f} | {b.graph:.3f} | "
            f"{b.relevance:.3f} | {b.tier:.2f} | {b.file_ref}/{b.symbol_ref} |"
        )
    dropped_lines = []
    for b in sorted(assembled.dropped_breakdowns, key=lambda x: x.priority)[:10]:
        dropped_lines.append(
            f"| `{b.citation}` | {b.priority:.3f} | {b.entry:.0f} | {b.graph:.3f} | "
            f"{b.relevance:.3f} | {b.tier:.2f} |"
        )

    ratio_pct = assembled.trim_ratio * 100
    md = f"""# Context Engineering Budget Experiment

- Generated: {now}
- Repo source: `{args.repo}`
- Local path: `{repo_path}`
- Indexed source files: **{n_files}**
- Approximate source LOC (supported langs, excluding tests/venv): **{n_loc}**
- Vector backend for retrieval candidates: `inmemory/cosine` + HashEmbedder
  (allocator itself is backend-agnostic; re-run with Qdrant + real embeddings for official resume numbers)
- Query: {args.query}

## Budget config

- Total budget B: **{budget}**
- Weights: entry={cfg_ctx.weights.entry}, graph={cfg_ctx.weights.graph},
  relevance={cfg_ctx.weights.relevance}, tier={cfg_ctx.weights.tier}
- G mix: file={cfg_ctx.graph_mix.file}, symbol={cfg_ctx.graph_mix.symbol}
- Buckets: code={cfg_ctx.buckets.code}, graph={cfg_ctx.buckets.graph},
  history={cfg_ctx.buckets.history}, reserve={cfg_ctx.buckets.reserve}
- History policy: sliding window (latest {cfg_ctx.history_window} round), not priority-trimmed

## Results

| Metric | Value |
|---|---|
| Candidates (hits + expanded) | {len(resp.hits) + len(resp.expanded_hits)} |
| Tokens before trim (code+reserve approx) | **{assembled.before_tokens}** |
| Tokens after assemble | **{assembled.after_tokens}** |
| Trim ratio | **{ratio_pct:.1f}%** |
| Code tokens | {assembled.code_tokens} |
| Graph summary tokens | {assembled.graph_tokens} |
| History tokens | {assembled.history_tokens} |
| Reserve tokens | {assembled.reserve_tokens} |
| Kept chunks | {len(assembled.kept_breakdowns)} |
| Dropped chunks | {len(assembled.dropped_breakdowns)} |

## Top kept (by priority)

| citation | priority | E | G | R | T | file/sym refs |
|---|---:|---:|---:|---:|---:|---|
{chr(10).join(kept_lines) if kept_lines else "| (none) | | | | | | |"}

## Lowest dropped

| citation | priority | E | G | R | T |
|---|---:|---:|---:|---:|---:|
{chr(10).join(dropped_lines) if dropped_lines else "| (none) | | | | | |"}

## Notes for resume

- Use this table's before/after/ratio only if LOC and backend match what you claim.
- Prefer re-running with `--repo https://github.com/django/django.git` (or Spring Boot)
  and `REPOSCOPE_VECTOR_BACKEND=qdrant` + real embeddings for the authoritative figure.
- Do **not** replace this with synthetic duplicated fixtures.
"""
    report_path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nWrote {report_path}")


if __name__ == "__main__":
    main()
