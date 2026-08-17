"""
Phase-6 retrieval evaluation harness.

Runs the same QA set under three retrieval modes:
  - vector  (dense only)
  - bm25    (sparse only)
  - hybrid  (fusion + optional rerank)

Metrics: Recall@5 / Precision@5 / MRR@5 with file+line-range *overlap* matching.

Examples:
  python -m eval.run_retrieval_eval --compare-modes --hash-embedder
  python -m eval.run_retrieval_eval --compare-modes --backend qdrant
  python -m eval.run_retrieval_eval --compare-modes --with-rerank
  python -m eval.run_retrieval_eval --dataset eval/datasets/qa_dataset.jsonl --compare-modes
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline
from app.retrieval import (
    IndexRequest,
    RetrievalService,
    RetrieveRequest,
    load_retrieval_config,
)
from app.retrieval.config import RetrievalConfig
from app.retrieval.embedder import HashEmbedder
from app.retrieval.rerank import IdentityReranker
from app.retrieval.vector_store import create_vector_store
from eval.dataset import QAItem, load_qa_dataset
from eval.metrics import macro_average, mrr_at_k, precision_at_k, recall_at_k


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "eval" / "datasets" / "qa_dataset.jsonl"
REPORT_DIR = ROOT / "eval" / "reports"
MODES = ("vector", "bm25", "hybrid")


@dataclass
class PerQueryResult:
    id: str
    question: str
    question_type: str
    gold: list[str]
    predicted: list[str]
    recall: float
    precision: float
    mrr: float


@dataclass
class ModeSummary:
    mode: str
    n: int
    recall_at_5: float
    precision_at_5: float
    mrr_at_5: float
    by_type: dict[str, dict[str, float]] = field(default_factory=dict)
    rows: list[PerQueryResult] = field(default_factory=list)


def ensure_repo(item: QAItem, workspace: Path) -> Path:
    """Resolve local path, or shallow-clone repo_url into workspace."""
    if item.repo_path:
        path = Path(item.repo_path)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists() and path.is_dir():
            return path.resolve()

    if not item.repo_url:
        raise FileNotFoundError(
            f"[{item.id}] repo_path not found and no repo_url to clone"
        )

    workspace.mkdir(parents=True, exist_ok=True)
    name = item.repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = workspace / name
    if dest.exists() and (dest / ".git").exists():
        return dest.resolve()
    if dest.exists() and dest.is_dir():
        return dest.resolve()
    subprocess.run(
        ["git", "clone", "--depth", "1", item.repo_url, str(dest)],
        check=True,
    )
    return dest.resolve()


def ensure_indexed(
    service: RetrievalService,
    repo_path: Path,
    artifact_dir: Path,
) -> str:
    pipeline = IngestionPipeline(
        workspace_root=artifact_dir / "workspace",
        artifact_dir=artifact_dir,
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    result = pipeline.run(str(repo_path))
    chunks, _graph = pipeline.load_artifacts(result.repo_id)
    service.index(
        IndexRequest(repo_id=result.repo_id, chunks=chunks, force_reindex=True)
    )
    return result.repo_id


def build_service(
    config: RetrievalConfig,
    use_hash: bool,
    artifact_dir: Path,
) -> RetrievalService:
    embedder = HashEmbedder() if use_hash else None
    reranker = IdentityReranker() if use_hash else None
    store = create_vector_store(config.vector_backend, config.qdrant_url)
    return RetrievalService(
        config=config,
        artifact_dir=artifact_dir,
        embedder=embedder,
        vector_store=store,
        reranker=reranker,
    )


def run_mode(
    service: RetrievalService,
    dataset: list[QAItem],
    mode: str,
    *,
    skip_rerank: bool,
    fusion: str | None,
    artifact_dir: Path,
    workspace: Path,
    k: int = 5,
) -> ModeSummary:
    path_to_id: dict[str, str] = {}
    rows: list[PerQueryResult] = []

    for item in dataset:
        repo_path = ensure_repo(item, workspace)
        key = str(repo_path)
        if key not in path_to_id:
            path_to_id[key] = ensure_indexed(service, repo_path, artifact_dir)

        response = service.retrieve(
            RetrieveRequest(
                repo_id=path_to_id[key],
                query=item.question,
                mode=mode,  # type: ignore[arg-type]
                skip_rerank=skip_rerank,
                fusion=fusion,  # type: ignore[arg-type]
                final_top_n=k,
            )
        )
        predicted = [h.citation.format() for h in response.hits]
        gold = item.gold_citations
        rows.append(
            PerQueryResult(
                id=item.id,
                question=item.question,
                question_type=item.question_type,
                gold=gold,
                predicted=predicted,
                recall=recall_at_k(predicted, gold, k),
                precision=precision_at_k(predicted, gold, k),
                mrr=mrr_at_k(predicted, gold, k),
            )
        )

    by_type: dict[str, dict[str, float]] = {}
    for qtype in sorted({r.question_type for r in rows}):
        subset = [r for r in rows if r.question_type == qtype]
        by_type[qtype] = {
            "n": float(len(subset)),
            "recall_at_5": macro_average([r.recall for r in subset]),
            "precision_at_5": macro_average([r.precision for r in subset]),
            "mrr_at_5": macro_average([r.mrr for r in subset]),
        }

    return ModeSummary(
        mode=mode,
        n=len(rows),
        recall_at_5=macro_average([r.recall for r in rows]),
        precision_at_5=macro_average([r.precision for r in rows]),
        mrr_at_5=macro_average([r.mrr for r in rows]),
        by_type=by_type,
        rows=rows,
    )


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def print_comparison_table(summaries: list[ModeSummary]) -> None:
    print()
    print("| Mode | N | Recall@5 | Precision@5 | MRR@5 |")
    print("|---|---:|---:|---:|---:|")
    for s in summaries:
        print(
            f"| {s.mode} | {s.n} | {_fmt_pct(s.recall_at_5)} | "
            f"{_fmt_pct(s.precision_at_5)} | {s.mrr_at_5:.3f} |"
        )
    print()

    # Relative lift vs vector baseline
    by_mode = {s.mode: s for s in summaries}
    if "vector" in by_mode and "hybrid" in by_mode:
        base = by_mode["vector"]
        hyb = by_mode["hybrid"]
        if base.recall_at_5 > 0:
            lift_r = (hyb.recall_at_5 - base.recall_at_5) / base.recall_at_5
            print(f"Hybrid vs Vector  dRecall@5: {lift_r * 100:+.1f}% relative")
        if base.precision_at_5 > 0:
            lift_p = (hyb.precision_at_5 - base.precision_at_5) / base.precision_at_5
            print(f"Hybrid vs Vector  dPrecision@5: {lift_p * 100:+.1f}% relative")
        print()


def print_mode_detail(summary: ModeSummary) -> None:
    print("-" * 72)
    print(f"mode={summary.mode}  R@5={summary.recall_at_5:.3f}  P@5={summary.precision_at_5:.3f}  MRR@5={summary.mrr_at_5:.3f}")
    for row in summary.rows:
        print(
            f"  [{row.id}|{row.question_type}] "
            f"R@5={row.recall:.2f} P@5={row.precision:.2f} MRR={row.mrr:.2f}"
        )
        print(f"    Q: {row.question}")
        print(f"    gold: {row.gold}")
        print(f"    pred: {row.predicted}")
    if summary.by_type:
        print("  by question_type:")
        for qtype, stats in summary.by_type.items():
            print(
                f"    {qtype}: n={int(stats['n'])} "
                f"R@5={stats['recall_at_5']:.3f} P@5={stats['precision_at_5']:.3f}"
            )


def write_reports(
    summaries: list[ModeSummary],
    *,
    config: RetrievalConfig,
    skip_rerank: bool,
    dataset_path: Path,
    use_hash: bool,
) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md_path = REPORT_DIR / "retrieval_compare.md"
    json_path = REPORT_DIR / "retrieval_compare.json"

    table_rows = []
    for s in summaries:
        table_rows.append(
            f"| {s.mode} | {s.n} | {_fmt_pct(s.recall_at_5)} | "
            f"{_fmt_pct(s.precision_at_5)} | {s.mrr_at_5:.3f} |"
        )

    by_type_blocks: list[str] = []
    for s in summaries:
        if not s.by_type:
            continue
        by_type_blocks.append(f"### {s.mode}")
        by_type_blocks.append("")
        by_type_blocks.append("| question_type | n | Recall@5 | Precision@5 | MRR@5 |")
        by_type_blocks.append("|---|---:|---:|---:|---:|")
        for qtype, stats in s.by_type.items():
            by_type_blocks.append(
                f"| {qtype} | {int(stats['n'])} | {_fmt_pct(stats['recall_at_5'])} | "
                f"{_fmt_pct(stats['precision_at_5'])} | {stats.get('mrr_at_5', 0):.3f} |"
            )
        by_type_blocks.append("")

    lift_note = ""
    by_mode = {s.mode: s for s in summaries}
    if "vector" in by_mode and "hybrid" in by_mode:
        base, hyb = by_mode["vector"], by_mode["hybrid"]
        if base.recall_at_5 > 0:
            lift_r = (hyb.recall_at_5 - base.recall_at_5) / base.recall_at_5 * 100
            lift_note += f"- Hybrid vs Vector relative ΔRecall@5: **{lift_r:+.1f}%**\n"
        if base.precision_at_5 > 0:
            lift_p = (hyb.precision_at_5 - base.precision_at_5) / base.precision_at_5 * 100
            lift_note += f"- Hybrid vs Vector relative ΔPrecision@5: **{lift_p:+.1f}%**\n"

    md = f"""# Retrieval Mode Comparison

- Generated: {now}
- Dataset: `{dataset_path.as_posix()}`
- Vector backend: `{config.backend_label}`
- Embedding: `{config.embedding.provider}/{config.embedding.model}`
- Fusion: `{config.fusion.default}`
- Rerank: `{"off" if skip_rerank else "on"}` ({config.rerank.model})
- Hash embedder (CI-only): `{use_hash}`

> Matching rule: a prediction hits gold when **file path matches** and **line ranges overlap**
> (not exact string equality).

## Macro results

| Mode | N | Recall@5 | Precision@5 | MRR@5 |
|---|---:|---:|---:|---:|
{chr(10).join(table_rows)}

{lift_note}
## By question type

{chr(10).join(by_type_blocks) if by_type_blocks else "_No type breakdown._"}

## Notes

- Fill these numbers into `eval/evaluation_report_template.md`.
- Prefer `vector_backend=qdrant` + real embeddings for resume / interview figures.
- Do **not** quote hash-embedder numbers as official Hybrid gains.
"""
    md_path.write_text(md, encoding="utf-8")

    payload: dict[str, Any] = {
        "generated_at": now,
        "dataset": str(dataset_path),
        "backend": config.backend_label,
        "embedding": f"{config.embedding.provider}/{config.embedding.model}",
        "fusion": config.fusion.default,
        "rerank": "off" if skip_rerank else "on",
        "hash_embedder": use_hash,
        "modes": [
            {
                "mode": s.mode,
                "n": s.n,
                "recall_at_5": s.recall_at_5,
                "precision_at_5": s.precision_at_5,
                "mrr_at_5": s.mrr_at_5,
                "by_type": s.by_type,
                "rows": [asdict(r) for r in s.rows],
            }
            for s in summaries
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoScope Phase-6 retrieval eval")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mode", choices=list(MODES), default="hybrid")
    parser.add_argument("--backend", choices=["inmemory", "qdrant"], default=None)
    parser.add_argument("--fusion", choices=["rrf", "weighted"], default=None)
    parser.add_argument("--skip-rerank", action="store_true")
    parser.add_argument(
        "--hash-embedder",
        action="store_true",
        help="Deterministic HashEmbedder (CI / smoke only; not for official report)",
    )
    parser.add_argument(
        "--compare-modes",
        action="store_true",
        help="Run vector / bm25 / hybrid and print a comparison table (recommended)",
    )
    parser.add_argument(
        "--with-rerank",
        action="store_true",
        help="Also run hybrid+rerank (ignored with --hash-embedder)",
    )
    parser.add_argument("--k", type=int, default=5, help="Cutoff for Recall@k / Precision@k")
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write eval/reports/retrieval_compare.{md,json}",
    )
    args = parser.parse_args()

    config = load_retrieval_config()
    if args.backend:
        config.vector_backend = args.backend  # type: ignore[assignment]
    if args.fusion:
        config.fusion.default = args.fusion  # type: ignore[assignment]

    dataset = load_qa_dataset(args.dataset)
    artifact_dir = ROOT / "data" / "eval_artifacts"
    workspace = ROOT / "data" / "eval_repos"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("RepoScope Retrieval Eval (Phase 6)")
    print(f"dataset     : {args.dataset}")
    print(f"backend     : {config.backend_label}")
    print(f"fusion      : {config.fusion.default}")
    print(f"rerank      : {'off' if (args.skip_rerank or args.hash_embedder) else 'on'}")
    print(f"embedding   : {config.embedding.provider}/{config.embedding.model}")
    print(f"hash        : {args.hash_embedder}")
    print(f"items       : {len(dataset)}")
    print("=" * 72)

    if not dataset:
        print(f"(empty dataset — add rows to {args.dataset})")
        print("See eval/README.md for the QA schema.")
        return

    modes = list(MODES) if args.compare_modes else [args.mode]
    summaries: list[ModeSummary] = []
    for mode in modes:
        # Fresh service per mode so BM25/vector state stays consistent with config
        service = build_service(
            config, use_hash=args.hash_embedder, artifact_dir=artifact_dir
        )
        summary = run_mode(
            service,
            dataset,
            mode,
            skip_rerank=args.skip_rerank or args.hash_embedder,
            fusion=args.fusion,
            artifact_dir=artifact_dir,
            workspace=workspace,
            k=args.k,
        )
        summaries.append(summary)
        print_mode_detail(summary)

    if args.with_rerank and not args.hash_embedder:
        service = build_service(config, use_hash=False, artifact_dir=artifact_dir)
        summary = run_mode(
            service,
            dataset,
            "hybrid",
            skip_rerank=False,
            fusion=args.fusion,
            artifact_dir=artifact_dir,
            workspace=workspace,
            k=args.k,
        )
        summary.mode = "hybrid+rerank"
        summaries.append(summary)
        print_mode_detail(summary)

    if len(summaries) > 1:
        print_comparison_table(summaries)

    if not args.no_write:
        md_path, json_path = write_reports(
            summaries,
            config=config,
            skip_rerank=args.skip_rerank or args.hash_embedder,
            dataset_path=args.dataset,
            use_hash=args.hash_embedder,
        )
        print(f"Wrote {md_path}")
        print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
