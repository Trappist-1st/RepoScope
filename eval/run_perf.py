"""Ingest throughput + query latency by size bucket.

  python -m eval.run_perf --bucket small
  python -m eval.run_perf --bucket medium          # clones flask / sqlalchemy
  python -m eval.run_perf --bucket large           # clones django; slow

Does not invent kernel-scale numbers. File counts are measured after ingest filters.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.ingestion import IngestionPipeline
from app.ingestion.incremental import iter_source_files
from app.mcp.service import RepoScopeFacade
from eval.repo_io import ROOT, resolve_repo

REPORT_DIR = ROOT / "eval" / "reports"
REGISTRY = ROOT / "eval" / "repos.yaml"


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (p / 100.0) * (len(s) - 1)
    lo = int(math.floor(k))
    hi = min(len(s) - 1, int(math.ceil(k)))
    if lo == hi:
        return float(s[lo])
    return float(s[lo] * (hi - k) + s[hi] * (k - lo))


def _artifact_bytes(art: Path, repo_id: str) -> int:
    base = art / repo_id
    if not base.exists():
        return 0
    return sum(p.stat().st_size for p in base.rglob("*") if p.is_file())


def ingest_one(spec: dict, work: Path) -> dict:
    src = resolve_repo(spec.get("local_path"), spec.get("url"), ROOT / "data" / "eval_repos")
    n_files = len(iter_source_files(src))
    pipe = IngestionPipeline(
        workspace_root=work / "ws",
        artifact_dir=work / "art",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )
    t0 = time.perf_counter()
    result = pipe.run(str(src), force_full=True)
    full_ms = int((time.perf_counter() - t0) * 1000)
    cached = pipe.run(str(src))
    loc = 0
    for p in iter_source_files(src):
        try:
            loc += sum(1 for _ in p.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return {
        "id": spec["id"],
        "path": str(src),
        "files": n_files,
        "loc": loc,
        "full_ms": full_ms,
        "cached_ms": cached.sync_took_ms,
        "files_per_s": round(n_files / (full_ms / 1000.0), 2) if full_ms else 0,
        "loc_per_s": round(loc / (full_ms / 1000.0), 1) if full_ms else 0,
        "artifact_bytes": _artifact_bytes(pipe.artifact_dir, result.repo_id),
        "call_edges": len(result.graph.call_edges),
        "import_edges": len(result.graph.file_edges),
        "inherit_edges": len(result.graph.inherit_edges),
        "repo_id": result.repo_id,
    }


def latency_one(spec: dict, work: Path, repeats: int) -> dict:
    src = resolve_repo(spec.get("local_path"), spec.get("url"), ROOT / "data" / "eval_repos")
    facade = RepoScopeFacade(
        workspace_root=work / "lat_ws",
        artifact_dir=work / "lat_art",
        use_hash_embedder=True,
    )
    repo = str(src)
    facade.get_initial_context(repo_url=repo)
    samples: dict[str, list[float]] = {"query_dependencies": [], "context_explore": []}
    for _ in range(repeats):
        t0 = time.perf_counter()
        facade.query_dependencies(repo_url=repo, symbol_name="send", direction="both")
        samples["query_dependencies"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        facade.context_explore(repo_url=repo, query="How does a request get sent?")
        samples["context_explore"].append((time.perf_counter() - t0) * 1000)
    out = {}
    for name, vals in samples.items():
        out[name] = {
            "p50_ms": round(_pct(vals, 50), 1),
            "p95_ms": round(_pct(vals, 95), 1),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", choices=["small", "medium", "large"], default="small")
    parser.add_argument("--latency-repeats", type=int, default=10)
    parser.add_argument("--skip-latency", action="store_true")
    args = parser.parse_args()
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    specs = registry["buckets"][args.bucket]["repos"]
    work = Path(tempfile.mkdtemp(prefix=f"reposcope-perf-{args.bucket}-"))
    rows = []
    for spec in specs:
        print(f"… ingest {spec['id']}")
        row = ingest_one(spec, work / spec["id"])
        if not args.skip_latency:
            print(f"… latency {spec['id']}")
            row["latency"] = latency_one(spec, work / f"lat_{spec['id']}", args.latency_repeats)
        rows.append(row)
        print(
            f"  files={row['files']} loc={row['loc']} full_ms={row['full_ms']} "
            f"artifact={row['artifact_bytes']}"
        )
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "bucket": args.bucket,
        "rows": rows,
        "note": (
            "Indexed file count uses RepoScope filters (no tests/venv). "
            "A repo listed under medium may measure as small — we report measured counts, "
            "not the YAML bucket label. Memory RSS is not recorded (optional psutil later)."
        ),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"perf_{args.bucket}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
