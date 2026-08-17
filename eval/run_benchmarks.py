"""Reproducible RepoScope benchmark harness.

Writes eval/reports/latest.{json,md}. Methodology: BENCHMARKS.md.

  .venv/Scripts/python.exe -m eval.run_benchmarks
  .venv/Scripts/python.exe -m eval.run_benchmarks --skip-remote
  .venv/Scripts/python.exe -m eval.run_benchmarks --real-embed
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.context_engine.features import estimate_tokens
from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.graph.impact import analyze_impact
from app.ingestion import IngestionPipeline
from app.intelligence.flow_tracer import FlowTracer
from app.mcp.service import RepoScopeFacade
from app.models.schemas import DependencyGraph, Definition
from app.parsing.languages import SUPPORTED_EXTENSIONS
from app.retrieval import IndexRequest, RetrievalService, RetrieveRequest
from app.retrieval.config import load_retrieval_config
from app.retrieval.embedder import HashEmbedder
from app.retrieval.rerank import IdentityReranker
from app.retrieval.schemas import Citation, RetrievalHit
from app.retrieval.vector_store import InMemoryVectorStore
from app.workflow.nodes.review import run_review
from app.workflow.schemas import Finding
from eval.dataset import load_qa_dataset
from eval.metrics import macro_average, precision_at_k, recall_at_k

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "eval" / "gold" / "structure.json"
REPORT_DIR = ROOT / "eval" / "reports"
QA_PATH = ROOT / "eval" / "datasets" / "qa_dataset.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (p / 100.0) * (len(s) - 1)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(s[lo])
    return float(s[lo] * (hi - k) + s[hi] * (k - lo))


def _pipeline(tmp: Path) -> IngestionPipeline:
    return IngestionPipeline(
        workspace_root=tmp / "ws",
        artifact_dir=tmp / "art",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )


def _count_loc(repo_path: Path) -> tuple[int, int]:
    files = 0
    lines = 0
    exclude = {".git", "node_modules", ".venv", "venv", "__pycache__"}
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


def _artifact_bytes(artifact_dir: Path, repo_id: str) -> int:
    base = artifact_dir / repo_id
    if not base.exists():
        return 0
    return sum(p.stat().st_size for p in base.rglob("*") if p.is_file())


def _edge_hit_call(graph: DependencyGraph, caller: str, callee: str) -> bool:
    for e in graph.call_edges:
        if e.caller == caller and e.callee == callee:
            return True
        if caller in e.caller and callee in e.callee:
            return True
    return False


def _edge_hit_import(graph: DependencyGraph, source: str, target: str) -> bool:
    src = source.replace("\\", "/")
    dst = target.replace("\\", "/")
    return any(e.source == src and e.target == dst for e in graph.file_edges)


def _edge_hit_inherit(
    graph: DependencyGraph, child: str, parent: str, relation: str | None
) -> bool:
    for e in graph.inherit_edges:
        if e.child == child and e.parent == parent:
            if relation is None or e.relation == relation:
                return True
    return False


def run_pytest() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else (proc.stderr or "").strip().splitlines()[-1:]
    line = summary[-1] if isinstance(summary, list) and summary else str(summary)
    return {
        "exit_code": proc.returncode,
        "summary": line,
        "passed": proc.returncode == 0,
    }


def bench_graph_gold(gold: dict, work: Path) -> dict[str, Any]:
    graphs: dict[str, DependencyGraph] = {}
    rows: list[dict[str, Any]] = []
    hit = 0
    total = 0
    for case in gold["graph_cases"]:
        rel = case["path"]
        if rel not in graphs:
            pipe = _pipeline(work / rel.replace("/", "_"))
            result = pipe.run(str(ROOT / rel))
            graphs[rel] = result.graph
        graph = graphs[rel]
        missed: list[str] = []
        for src, dst in case.get("imports") or []:
            total += 1
            ok = _edge_hit_import(graph, src, dst)
            hit += int(ok)
            if not ok:
                missed.append(f"import {src} -> {dst}")
        for caller, callee in case.get("calls") or []:
            total += 1
            ok = _edge_hit_call(graph, caller, callee)
            hit += int(ok)
            if not ok:
                missed.append(f"call {caller} -> {callee}")
        for item in case.get("inherit") or []:
            child, parent = item[0], item[1]
            reln = item[2] if len(item) > 2 else None
            total += 1
            ok = _edge_hit_inherit(graph, child, parent, reln)
            hit += int(ok)
            if not ok:
                missed.append(f"inherit {child} -> {parent}")
        rows.append(
            {
                "id": case["id"],
                "missed": missed,
                "ok": not missed,
                "import_edges": len(graph.file_edges),
                "call_edges": len(graph.call_edges),
                "inherit_edges": len(graph.inherit_edges),
            }
        )
    recall = (hit / total) if total else 0.0
    return {
        "gold_edges": total,
        "resolved": hit,
        "recall": recall,
        "cases": rows,
        "note": "Gold-edge recall only. Gold is a curated must-resolve set, not a complete edge census — do not report precision.",
    }


def bench_flow(gold: dict, work: Path) -> dict[str, Any]:
    rows = []
    covered = 0
    terms_hit = 0
    terms_total = 0
    for case in gold["flow_cases"]:
        pipe = _pipeline(work / f"flow_{case['id']}")
        result = pipe.run(str(ROOT / case["path"]))
        kg = pipe.load_knowledge_graph(result.repo_id)
        trace = FlowTracer().trace(kg, case["question"])
        blob = " ".join(
            filter(
                None,
                [s.symbol or "" for s in trace.steps]
                + [s.file_path or "" for s in trace.steps],
            )
        )
        missing = [t for t in case["must_contain"] if t not in blob]
        terms_total += len(case["must_contain"])
        terms_hit += len(case["must_contain"]) - len(missing)
        ok = not missing and bool(trace.steps)
        covered += int(ok)
        rows.append(
            {
                "id": case["id"],
                "ok": ok,
                "steps": len(trace.steps),
                "missing": missing,
                "cited": all(
                    (s.is_synthetic or (s.file_path and s.start_line is not None))
                    for s in trace.steps
                ),
            }
        )
    n = len(rows)
    return {
        "cases": n,
        "full_cover": covered,
        "full_cover_rate": covered / n if n else 0.0,
        "term_recall": terms_hit / terms_total if terms_total else 0.0,
        "rows": rows,
    }


def bench_impact(gold: dict, work: Path) -> dict[str, Any]:
    rows = []
    hit = 0
    for case in gold["impact_cases"]:
        pipe = _pipeline(work / f"impact_{case['id']}")
        result = pipe.run(str(ROOT / case["path"]))
        report = analyze_impact(
            result.graph,
            [case["seed"]],
            depth=2,
            direction=case.get("direction", "affected"),
        )
        found = {e.symbol_ref for e in report.all_hits}
        missing = [s for s in case["must_contain"] if s not in found]
        ok = not missing
        hit += int(ok)
        rows.append({"id": case["id"], "ok": ok, "missing": missing, "n_hits": len(found)})
    n = len(rows)
    return {"cases": n, "passed": hit, "rate": hit / n if n else 0.0, "rows": rows}


def bench_reviewer() -> dict[str, Any]:
    hits = [
        RetrievalHit(
            chunk_id="1",
            content="def greet(name):\n    return name\n",
            citation=Citation(file_path="py_pkg/a.py", start_line=4, end_line=5),
            score=1.0,
            source="bm25",
            symbol_name="greet",
        )
    ]
    base = {
        "hits": hits,
        "expanded_hits": [],
        "primary_citations": ["py_pkg/a.py:4-5"],
        "expanded_citations": [],
        "plan": ["locate greet"],
        "retry_count": 0,
        "max_review_retries": 2,
    }
    cases = [
        (
            "hallucinated_path",
            Finding(claim="greet exists", citations=["ghost.py:1-1"], symbols=["greet"], plan_step_idx=0),
            True,
        ),
        (
            "missing_citation",
            Finding(claim="greet exists", citations=[], symbols=["greet"], plan_step_idx=0),
            True,
        ),
        (
            "malformed_citation",
            Finding(claim="greet exists", citations=["py_pkg/a.py"], symbols=["greet"], plan_step_idx=0),
            True,
        ),
        (
            "symbol_not_in_snippet",
            Finding(
                claim="AuthService.login exists",
                citations=["py_pkg/a.py:4-5"],
                symbols=["AuthService"],
                plan_step_idx=0,
            ),
            True,
        ),
        (
            "grounded_ok",
            Finding(claim="greet exists", citations=["py_pkg/a.py:4-5"], symbols=["greet"], plan_step_idx=0),
            False,
        ),
    ]
    rows = []
    catchable = 0
    caught = 0
    for name, finding, should_fail in cases:
        out = run_review({**base, "findings": [finding]})
        failed = not out["review_passed"]
        ok = failed == should_fail
        if should_fail:
            catchable += 1
            caught += int(failed)
        rows.append(
            {
                "id": name,
                "should_fail": should_fail,
                "failed": failed,
                "ok": ok,
                "issues": [i.type for i in out.get("review_issues") or []],
            }
        )
    return {
        "catchable": catchable,
        "caught": caught,
        "catch_rate": caught / catchable if catchable else 0.0,
        "valid_passed": all(r["ok"] for r in rows if not r["should_fail"]),
        "rows": rows,
    }


def bench_ingest_one(source: Path, work: Path, *, mutate_rel: str | None) -> dict[str, Any]:
    n_files, n_loc = _count_loc(source)
    writable = work / "repo"
    if writable.exists():
        shutil.rmtree(writable, ignore_errors=True)
    shutil.copytree(source, writable, ignore=shutil.ignore_patterns(".git"))
    pipe = _pipeline(work / "pipe")
    t0 = time.perf_counter()
    first = pipe.run(str(writable), force_full=True)
    full_ms = int((time.perf_counter() - t0) * 1000)
    cached = pipe.run(str(writable))
    merge_ms = None
    merge_mode = None
    if mutate_rel:
        target = writable / mutate_rel
        if target.exists():
            target.write_text(
                target.read_text(encoding="utf-8") + "\n# reposcope-bench-touch\n",
                encoding="utf-8",
            )
            t1 = time.perf_counter()
            third = pipe.run(str(writable))
            merge_ms = int((time.perf_counter() - t1) * 1000)
            merge_mode = third.graph_update_mode
    art_bytes = _artifact_bytes(pipe.artifact_dir, first.repo_id)
    loc_per_s = (n_loc / (full_ms / 1000.0)) if full_ms else 0.0
    files_per_s = (n_files / (full_ms / 1000.0)) if full_ms else 0.0
    speedup = (full_ms / merge_ms) if merge_ms else None
    return {
        "source": str(source),
        "files": n_files,
        "loc": n_loc,
        "full_ms": full_ms,
        "cached_ms": cached.sync_took_ms,
        "cached_mode": cached.graph_update_mode,
        "incremental_ms": merge_ms,
        "incremental_mode": merge_mode,
        "incremental_speedup_vs_full": round(speedup, 2) if speedup else None,
        "files_per_s": round(files_per_s, 2),
        "loc_per_s": round(loc_per_s, 1),
        "artifact_bytes": art_bytes,
        "call_edges": len(first.graph.call_edges),
        "import_edges": len(first.graph.file_edges),
        "inherit_edges": len(first.graph.inherit_edges),
        "chunks": sum(len(p.chunks) for p in first.parse_results)
        if first.parse_results
        else None,
        "repo_id": first.repo_id,
    }


def ensure_remote(url: str, dest_parent: Path) -> Path:
    dest_parent.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = dest_parent / name
    if dest.exists() and (dest / ".git").exists():
        return dest
    subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], check=True)
    return dest


def bench_query_latency(fixture: Path, work: Path, repeats: int = 25) -> dict[str, Any]:
    facade = RepoScopeFacade(
        workspace_root=work / "ws",
        artifact_dir=work / "art",
        use_hash_embedder=True,
    )
    repo = str(fixture)
    facade.get_initial_context(repo_url=repo)  # warm index
    samples: dict[str, list[float]] = {
        "query_dependencies": [],
        "analyze_impact": [],
        "context_explore": [],
        "trace_flow": [],
    }
    for _ in range(repeats):
        t0 = time.perf_counter()
        facade.query_dependencies(repo_url=repo, symbol_name="login", direction="both")
        samples["query_dependencies"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        facade.analyze_impact(repo_url=repo, symbol_name="login", depth=2)
        samples["analyze_impact"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        facade.context_explore(repo_url=repo, query="How does login work?")
        samples["context_explore"].append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        facade.trace_flow(repo_url=repo, question="How does login work?")
        samples["trace_flow"].append((time.perf_counter() - t0) * 1000)
    out: dict[str, Any] = {"repeats": repeats, "fixture": str(fixture), "tools": {}}
    for name, vals in samples.items():
        out["tools"][name] = {
            "p50_ms": round(_pct(vals, 50), 1),
            "p95_ms": round(_pct(vals, 95), 1),
            "p99_ms": round(_pct(vals, 99), 1),
            "mean_ms": round(statistics.fmean(vals), 1),
        }
    return out


def bench_token_proxy(fixture: Path, work: Path, query: str) -> dict[str, Any]:
    """Proxy for agent cost: one MCP pack vs reading every file that greps the query."""
    facade = RepoScopeFacade(
        workspace_root=work / "ws",
        artifact_dir=work / "art",
        use_hash_embedder=True,
    )
    result = facade.context_explore(repo_url=str(fixture), query=query)
    mcp_text = json.dumps(result.model_dump(), ensure_ascii=False)
    mcp_tokens = estimate_tokens(mcp_text)

    terms = [t.lower() for t in query.replace("?", "").split() if len(t) > 3]
    grep_files: list[Path] = []
    grep_tokens = 0
    exclude = {".git", "__pycache__"}
    for path in fixture.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if any(part in exclude for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        if any(t in low for t in terms) or any(t in path.name.lower() for t in terms):
            grep_files.append(path)
            grep_tokens += estimate_tokens(text)

    tool_calls_mcp = 1
    tool_calls_grep = 1 + len(grep_files)  # one grep + N reads
    return {
        "query": query,
        "fixture": str(fixture),
        "mcp_tokens": mcp_tokens,
        "mcp_tool_calls": tool_calls_mcp,
        "grep_read_tokens": grep_tokens,
        "grep_read_files": len(grep_files),
        "grep_read_tool_calls": tool_calls_grep,
        "token_ratio_grep_over_mcp": round(grep_tokens / mcp_tokens, 2) if mcp_tokens else None,
        "note": (
            "Proxy, not a live-agent SWE-bench. 'grep+read' = one content scan + full-file "
            "reads of every matching source file. Token estimate is chars/4."
        ),
    }


def _lookup_def(
    definitions: dict[str, list[Definition]],
    *,
    file_suffix: str,
    symbol: str,
    parent: str | None,
) -> str | None:
    suffix = file_suffix.replace("\\", "/")
    for path, defs in definitions.items():
        if not path.replace("\\", "/").endswith(suffix):
            continue
        for d in defs:
            if d.name != symbol:
                continue
            if parent and d.parent_name != parent:
                continue
            if parent is None and d.parent_name:
                continue
            return f"{path}:{d.start_line}-{d.end_line}"
        for d in defs:
            if d.name == symbol:
                return f"{path}:{d.start_line}-{d.end_line}"
    return None


def _load_definitions(artifact_dir: Path, repo_id: str) -> dict[str, list[Definition]]:
    path = artifact_dir / repo_id / "definitions.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        p: [Definition.model_validate(d) for d in defs] for p, defs in raw.items()
    }


def bench_retrieval(
    *,
    real_embed: bool,
    remote_repo: Path | None,
    gold: dict,
    work: Path,
) -> dict[str, Any]:
    cfg = load_retrieval_config()
    cfg.vector_backend = "inmemory"
    use_hash = not real_embed
    service = RetrievalService(
        config=cfg,
        artifact_dir=work / "ret_art",
        embedder=HashEmbedder() if use_hash else None,
        vector_store=InMemoryVectorStore(),
        reranker=IdentityReranker(),
    )
    dataset = load_qa_dataset(QA_PATH)
    items: list[tuple[str, str, str, list[str]]] = []
    # (id, repo_key, question, gold_citations)
    path_to_id: dict[str, str] = {}

    def index_path(repo_path: Path) -> str:
        key = str(repo_path.resolve())
        if key in path_to_id:
            return path_to_id[key]
        pipe = IngestionPipeline(
            workspace_root=work / "ret_ws",
            artifact_dir=work / "ret_art",
            files_repo=InMemoryFilesRepository(),
            repos_repo=InMemoryReposRepository(),
        )
        result = pipe.run(str(repo_path))
        chunks, _ = pipe.load_artifacts(result.repo_id)
        service.index(IndexRequest(repo_id=result.repo_id, chunks=chunks, force_reindex=True))
        path_to_id[key] = result.repo_id
        return result.repo_id

    for item in dataset:
        repo_path = ROOT / item.repo_path if item.repo_path else None
        if repo_path is None or not repo_path.exists():
            continue
        repo_id = index_path(repo_path)
        items.append((item.id, repo_id, item.question, item.gold_citations))

    extra_rows = []
    if remote_repo is not None:
        repo_id = index_path(remote_repo)
        defs = _load_definitions(work / "ret_art", repo_id)
        # definitions live next to ingest artifacts; retrieval service uses same dir
        defs = _load_definitions(service.artifact_dir, repo_id) or defs
        for q in gold.get("requests_symbol_queries") or []:
            cite = _lookup_def(
                defs,
                file_suffix=q["file_suffix"],
                symbol=q["symbol"],
                parent=q.get("parent"),
            )
            if cite:
                items.append((q["id"], repo_id, q["question"], [cite]))
                extra_rows.append({"id": q["id"], "gold": cite})

    modes = ("vector", "bm25", "hybrid")
    summaries = []
    for mode in modes:
        recs = []
        precs = []
        for _id, repo_id, question, gold_cites in items:
            resp = service.retrieve(
                RetrieveRequest(
                    repo_id=repo_id,
                    query=question,
                    mode=mode,  # type: ignore[arg-type]
                    skip_rerank=True,
                    final_top_n=5,
                    graph_expand=False,
                )
            )
            pred = [h.citation.format() for h in resp.hits]
            recs.append(recall_at_k(pred, gold_cites, 5))
            precs.append(precision_at_k(pred, gold_cites, 5))
        summaries.append(
            {
                "mode": mode,
                "n": len(items),
                "recall_at_5": macro_average(recs),
                "precision_at_5": macro_average(precs),
            }
        )
    lift = None
    by_mode = {s["mode"]: s for s in summaries}
    if by_mode["vector"]["recall_at_5"] > 0:
        lift = (by_mode["hybrid"]["recall_at_5"] - by_mode["vector"]["recall_at_5"]) / by_mode[
            "vector"
        ]["recall_at_5"]
    return {
        "hash_embedder": use_hash,
        "embedding": "hash" if use_hash else f"{cfg.embedding.provider}/{cfg.embedding.model}",
        "rerank": "off",
        "n": len(items),
        "modes": summaries,
        "hybrid_vs_vector_recall_lift": lift,
        "resolved_symbol_golds": extra_rows,
        "note": (
            "HashEmbedder numbers are CI/smoke only. Quote MiniLM/Qdrant runs as official retrieval."
            if use_hash
            else "In-memory MiniLM, rerank off. Matching = file path + overlapping line range."
        ),
    }


def write_reports(payload: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "latest.json"
    md_path = REPORT_DIR / "latest.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    g = payload["graph_gold"]
    f = payload["flow"]
    i = payload["impact"]
    r = payload["reviewer"]
    ret = payload["retrieval"]
    lat = payload["latency"]
    tok = payload["token_proxy"]
    pytest_info = payload["pytest"]

    def pct(x: float | None) -> str:
        if x is None:
            return "—"
        return f"{x * 100:.1f}%"

    ingest_rows = []
    for row in payload["ingest"]:
        ingest_rows.append(
            f"| `{Path(row['source']).name}` | {row['files']} | {row['loc']} | "
            f"{row['full_ms']} | {row['incremental_ms'] or '—'} | "
            f"{row['incremental_mode'] or '—'} | {row['loc_per_s']} | "
            f"{row['artifact_bytes']} |"
        )
    lat_rows = []
    for name, stats in lat.get("tools", {}).items():
        lat_rows.append(
            f"| `{name}` | {stats['p50_ms']} | {stats['p95_ms']} | {stats['p99_ms']} |"
        )
    ret_rows = []
    for s in ret.get("modes") or []:
        ret_rows.append(
            f"| {s['mode']} | {s['n']} | {pct(s['recall_at_5'])} | {pct(s['precision_at_5'])} |"
        )

    md = f"""# RepoScope benchmark snapshot

- Generated: {payload['generated_at']}
- Host: {payload['host']}
- Command: `python -m eval.run_benchmarks`
- Pytest: `{pytest_info['summary']}` (exit {pytest_info['exit_code']})

## Structure quality (fixture gold)

| Metric | Value |
|---|---:|
| Gold-edge recall (import/call/inherit must-resolve set) | **{pct(g['recall'])}** ({g['resolved']}/{g['gold_edges']}) |
| Flow term coverage | **{pct(f['term_recall'])}** |
| Flow cases fully covered | {f['full_cover']}/{f['cases']} |
| Impact gold seeds | {i['passed']}/{i['cases']} |
| Reviewer catch rate (fabricated citations) | **{pct(r['catch_rate'])}** ({r['caught']}/{r['catchable']}) |

Gold is a curated must-resolve set, not a complete call-graph census. Do not report precision.

## Retrieval (Recall@5 / Precision@5)

Embedding: `{ret.get('embedding')}` · rerank: `{ret.get('rerank')}` · N={ret.get('n')}

| Mode | N | Recall@5 | Precision@5 |
|---|---:|---:|---:|
{chr(10).join(ret_rows)}

Hybrid vs vector relative ΔRecall@5: **{(ret.get('hybrid_vs_vector_recall_lift') or 0) * 100:+.1f}%**

{ret.get('note')}

## Index / incremental

| Repo | files | LOC | full ms | incr. ms | incr. mode | LOC/s | artifact bytes |
|---|---:|---:|---:|---:|---|---:|---:|
{chr(10).join(ingest_rows)}

## Query latency (warm index, FastAPI login fixture)

| Tool | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|
{chr(10).join(lat_rows)}

## Token proxy (not a live-agent eval)

Query: `{tok['query']}` on `{Path(str(tok.get('fixture', ''))).name}`

| Path | Tokens | Tool calls |
|---|---:|---:|
| `context_explore` (1 call) | {tok['mcp_tokens']} | {tok['mcp_tool_calls']} |
| grep + full-file reads | {tok['grep_read_tokens']} | {tok['grep_read_tool_calls']} |

Ratio (grep+read / MCP): **{tok.get('token_ratio_grep_over_mcp')}×**

{tok['note']}
"""
    extra = tok.get("requests")
    if isinstance(extra, dict):
        md += f"""
### Token proxy (`requests`)

Query: `{extra.get('query')}`

| Path | Tokens | Tool calls |
|---|---:|---:|
| `context_explore` | {extra.get('mcp_tokens')} | {extra.get('mcp_tool_calls')} |
| grep + full-file reads | {extra.get('grep_read_tokens')} | {extra.get('grep_read_tool_calls')} |

Ratio: **{extra.get('token_ratio_grep_over_mcp')}×**
"""
    md_path.write_text(md, encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoScope reproducible benchmarks")
    parser.add_argument("--skip-remote", action="store_true", help="Skip cloning psf/requests")
    parser.add_argument(
        "--repo",
        default="https://github.com/psf/requests.git",
        help="Real repo URL or local path for ingest/retrieval extras",
    )
    parser.add_argument(
        "--real-embed",
        action="store_true",
        help="Use sentence-transformers MiniLM (official retrieval). Default is HashEmbedder.",
    )
    parser.add_argument("--latency-repeats", type=int, default=25)
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args()

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="reposcope-bench-"))
    host = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
    }

    print("=" * 72)
    print("RepoScope benchmarks")
    print(f"work dir: {work}")
    print("=" * 72)

    pytest_info = {"exit_code": None, "summary": "skipped", "passed": None}
    if not args.skip_pytest:
        print("… pytest")
        pytest_info = run_pytest()
        print(" ", pytest_info["summary"])

    print("… graph gold")
    graph_gold = bench_graph_gold(gold, work / "graph")
    print(f"  recall={graph_gold['recall']:.3f} ({graph_gold['resolved']}/{graph_gold['gold_edges']})")

    print("… flow")
    flow = bench_flow(gold, work / "flow")
    print(f"  term_recall={flow['term_recall']:.3f}")

    print("… impact")
    impact = bench_impact(gold, work / "impact")
    print(f"  {impact['passed']}/{impact['cases']}")

    print("… reviewer")
    reviewer = bench_reviewer()
    print(f"  catch_rate={reviewer['catch_rate']:.3f}")

    ingest_rows = []
    fixture_specs = [
        (ROOT / "tests/fixtures/sample_repo", "py_pkg/a.py"),
        (ROOT / "tests/fixtures/flow_fastapi_login", "app/api/auth.py"),
        (ROOT / "tests/fixtures/flow_spring_login", "auth/AuthController.java"),
        (ROOT / "tests/fixtures/inherit_repo", "animal/dog.py"),
    ]
    remote_path = None
    if not args.skip_remote:
        print(f"… clone/ensure {args.repo}")
        src = Path(args.repo)
        if src.exists() and src.is_dir():
            remote_path = src.resolve()
        else:
            remote_path = ensure_remote(args.repo, ROOT / "data" / "eval_repos")
        # Prefer a Python file that exists in requests
        mutate = "src/requests/sessions.py"
        if not (remote_path / mutate).exists():
            mutate = "requests/sessions.py"
        if not (remote_path / mutate).exists():
            py = next(remote_path.rglob("*.py"), None)
            mutate = py.relative_to(remote_path).as_posix() if py else None
        fixture_specs.append((remote_path, mutate))

    for i, (path, mutate) in enumerate(fixture_specs):
        print(f"… ingest {path.name}")
        ingest_rows.append(bench_ingest_one(path, work / f"ingest_{i}", mutate_rel=mutate))

    print("… query latency")
    latency = bench_query_latency(
        ROOT / "tests/fixtures/flow_fastapi_login",
        work / "lat",
        repeats=args.latency_repeats,
    )

    print("… token proxy")
    token_proxy = bench_token_proxy(
        ROOT / "tests/fixtures/flow_fastapi_login",
        work / "tok",
        "How does login work?",
    )
    if remote_path is not None:
        print("… token proxy (requests)")
        token_proxy["requests"] = bench_token_proxy(
            remote_path,
            work / "tok_req",
            "How does Session.send an HTTP request?",
        )

    print("… retrieval")
    retrieval = bench_retrieval(
        real_embed=args.real_embed,
        remote_repo=remote_path,
        gold=gold,
        work=work / "ret",
    )
    for s in retrieval["modes"]:
        print(f"  {s['mode']}: R@5={s['recall_at_5']:.3f} P@5={s['precision_at_5']:.3f}")

    payload = {
        "generated_at": _utc_now(),
        "host": host,
        "pytest": pytest_info,
        "graph_gold": graph_gold,
        "flow": flow,
        "impact": impact,
        "reviewer": reviewer,
        "ingest": ingest_rows,
        "latency": latency,
        "token_proxy": token_proxy,
        "retrieval": retrieval,
    }
    json_path, md_path = write_reports(payload)
    print()
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print("Copy numbers into BENCHMARKS.md. Do not quote HashEmbedder as official retrieval.")


if __name__ == "__main__":
    main()
