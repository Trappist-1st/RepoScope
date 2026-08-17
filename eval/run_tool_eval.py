"""NL → structured tool eval (Spider-style): query_dependencies / trace_flow / analyze_impact / analyze_architecture.

  python -m eval.run_tool_eval
  python -m eval.run_tool_eval --skip-remote
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.graph.impact import analyze_impact
from app.ingestion import IngestionPipeline
from app.intelligence.flow_tracer import FlowTracer
from app.db import InMemoryFilesRepository, InMemoryReposRepository
from app.graph.query import callees_of, callers_of
from eval.metrics import macro_average, ordered_coverage, set_precision, set_recall
from eval.repo_io import ROOT, load_jsonl, resolve_repo

GOLD = ROOT / "eval" / "gold" / "tools.jsonl"
REPORT_DIR = ROOT / "eval" / "reports"


def _pipe(work: Path) -> IngestionPipeline:
    return IngestionPipeline(
        workspace_root=work / "ws",
        artifact_dir=work / "art",
        files_repo=InMemoryFilesRepository(),
        repos_repo=InMemoryReposRepository(),
    )


def eval_dependencies(graph, row: dict) -> dict:
    symbol = row["symbol_name"]
    direction = row.get("direction", "both")
    pred_callers = callers_of(graph, symbol) if direction in {"both", "callers"} else []
    pred_callees = callees_of(graph, symbol) if direction in {"both", "callees"} else []
    gold_callers = row.get("expected_callers") or []
    gold_callees = row.get("expected_callees") or []
    complete = bool(row.get("gold_complete"))
    recs: list[float] = []
    if gold_callers:
        recs.append(set_recall(pred_callers, gold_callers))
    if gold_callees:
        recs.append(set_recall(pred_callees, gold_callees))
    recall = macro_average(recs)
    prec = None
    if complete:
        parts = []
        if gold_callers:
            parts.append(set_precision(pred_callers, gold_callers))
        if gold_callees:
            parts.append(set_precision(pred_callees, gold_callees))
        prec = macro_average(parts) if parts else None
    return {
        "recall": recall,
        "precision": prec,
        "predicted_callers": pred_callers,
        "predicted_callees": pred_callees,
    }


def eval_flow(kg, row: dict) -> dict:
    trace = FlowTracer().trace(kg, row["question"])
    steps = [s.symbol or "" for s in trace.steps]
    files = [s.file_path or "" for s in trace.steps]
    chain_cov = ordered_coverage(steps, row.get("expected_chain") or [])
    file_cov = set_recall(files, row.get("expected_files") or [])
    cited = all(
        (s.is_synthetic or (s.file_path and s.start_line is not None)) for s in trace.steps
    )
    return {
        "recall": chain_cov,
        "file_recall": file_cov,
        "cited": cited,
        "steps": len(trace.steps),
        "chain": steps,
    }


def eval_impact(graph, row: dict) -> dict:
    report = analyze_impact(
        graph,
        [row["symbol_name"]],
        depth=2,
        direction=row.get("direction", "affected"),
    )
    pred = [e.symbol_ref for e in report.all_hits]
    gold = row.get("expected_symbols") or []
    return {"recall": set_recall(pred, gold), "predicted": pred}


def eval_architecture(kg, ingest, row: dict) -> dict:
    """Score analyze_architecture: pattern match + key-file recall."""
    from app.intelligence.architecture import ArchitectureAnalyzer

    arch = ArchitectureAnalyzer().analyze(kg, workspace_root=ingest.local_path)
    expected_pattern = (row.get("expected_pattern") or "").lower()
    primary = arch.primary_pattern.value.lower()
    pattern_match = (not expected_pattern) or (expected_pattern in primary or primary in expected_pattern)

    expected_files = row.get("expected_files") or []
    found_files: list[str] = []
    for finding in arch.findings:
        for cite in finding.citations:
            # citations are like "path/to/file.py:10-20"
            fp = cite.rpartition(":")[0] if ":" in cite else cite
            if fp:
                found_files.append(fp)
    file_recall = set_recall(found_files, expected_files) if expected_files else 1.0

    # Overall recall: average of pattern match + file recall
    parts = [float(pattern_match), file_recall]
    recall = macro_average(parts)
    return {
        "recall": recall,
        "primary_pattern": primary,
        "pattern_match": pattern_match,
        "file_recall": file_recall,
        "finding_count": len(arch.findings),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-remote", action="store_true")
    args = parser.parse_args()
    rows = load_jsonl(GOLD)
    work = Path(tempfile.mkdtemp(prefix="reposcope-tools-"))
    cache: dict[str, tuple] = {}
    results = []
    for row in rows:
        if args.skip_remote and row.get("repo_url"):
            continue
        try:
            path = resolve_repo(row.get("repo_path"), row.get("repo_url"), ROOT / "data" / "eval_repos")
        except FileNotFoundError:
            results.append({**row, "skipped": True, "reason": "repo missing"})
            continue
        key = str(path)
        if key not in cache:
            pipe = _pipe(work / path.name)
            ingest = pipe.run(str(path))
            kg = pipe.try_load_knowledge_graph(ingest.repo_id)
            cache[key] = (ingest, ingest.graph, kg)
        ingest, graph, kg = cache[key]
        tool = row["tool"]
        scored: dict
        if tool == "query_dependencies":
            scored = eval_dependencies(graph, row)
        elif tool == "trace_flow":
            if kg is None:
                scored = {"recall": 0.0, "error": "no knowledge graph"}
            else:
                scored = eval_flow(kg, row)
        elif tool == "analyze_impact":
            scored = eval_impact(graph, row)
        elif tool == "analyze_architecture":
            if kg is None:
                scored = {"recall": 0.0, "error": "no knowledge graph"}
            else:
                scored = eval_architecture(kg, ingest, row)
        else:
            scored = {"recall": 0.0, "error": f"unknown tool {tool}"}
        results.append({"id": row["id"], "tool": tool, **scored})

    by_tool: dict[str, list[float]] = {}
    for r in results:
        if r.get("skipped") or "recall" not in r:
            continue
        by_tool.setdefault(r["tool"], []).append(float(r["recall"]))
    summary = {
        tool: {"n": len(vals), "recall": macro_average(vals)} for tool, vals in by_tool.items()
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary": summary,
        "rows": results,
        "note": "Precision only when gold_complete=true. Incomplete gold → recall only.",
    }
    out = REPORT_DIR / "tool_eval.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Tool eval (NL → structured)")
    for tool, stats in summary.items():
        print(f"  {tool}: n={stats['n']} recall={stats['recall']:.3f}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
