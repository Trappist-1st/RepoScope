"""Smoke-test all MCP tools against a real repository.

Runs every tool in RepoScopeFacade once and reports pass/fail, latency,
and confidence. Use this to quickly verify the MCP server is useful on
a new classic repo before writing gold annotations.

Usage:
    # Against an already-cloned repo
    python -m eval.run_smoke --repo data/eval_repos/requests

    # Clone on demand (shallow)
    python -m eval.run_smoke --repo-url https://github.com/psf/requests.git

    # Specify a focus symbol / question
    python -m eval.run_smoke --repo data/eval_repos/flask --symbol "Flask.dispatch_request" --query "How does request routing work?"

    # All repos in repos.yaml (small bucket only, cloned repos only)
    python -m eval.run_smoke --all-cloned

Output: console table + eval/reports/smoke_<repo_id>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root so relative paths in repos.yaml resolve correctly
ROOT = Path(__file__).parent.parent


def _load_yaml_repos() -> list[dict]:
    """Return repos from repos.yaml that have a local_path."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return []
    cfg = yaml.safe_load((ROOT / "eval" / "repos.yaml").read_text(encoding="utf-8"))
    repos = []
    for bucket in cfg.get("buckets", {}).values():
        for r in bucket.get("repos", []):
            repos.append(r)
    return repos


# ---------------------------------------------------------------------------
# Tool probe definitions
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    tool: str
    ok: bool
    took_ms: int
    low_confidence: bool
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def _probe_initial_context(facade, repo: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.get_initial_context(repo_url=repo)
        ok = bool(r.languages or r.core_modules)
        note = f"languages={r.languages} modules={len(r.core_modules)}"
        return ProbeResult(
            tool="get_initial_context",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=False,
            note=note,
            detail={"languages": r.languages, "module_count": len(r.core_modules)},
        )
    except Exception as exc:
        return ProbeResult(
            tool="get_initial_context",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_architecture(facade, repo: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.analyze_architecture(repo_url=repo)
        ok = r.primary_pattern != "unknown" or r.finding_count > 0
        note = f"pattern={r.primary_pattern} findings={r.finding_count} low={r.low_confidence}"
        return ProbeResult(
            tool="analyze_architecture",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=r.low_confidence,
            note=note,
            detail={"pattern": r.primary_pattern, "findings": r.finding_count},
        )
    except Exception as exc:
        return ProbeResult(
            tool="analyze_architecture",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_context_explore(facade, repo: str, query: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.context_explore(repo_url=repo, query=query)
        ok = bool(r.seeds)
        note = f"seeds={len(r.seeds)} paths={len(r.call_paths)} blast={len(r.blast_radius)}"
        return ProbeResult(
            tool="context_explore",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=r.low_confidence,
            note=note,
            detail={"seeds": len(r.seeds), "call_paths": len(r.call_paths)},
        )
    except Exception as exc:
        return ProbeResult(
            tool="context_explore",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_trace_flow(facade, repo: str, query: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.trace_flow(repo_url=repo, question=query)
        trace = r.trace or {}
        steps = trace.get("steps") or []
        ok = bool(steps)
        note = f"steps={len(steps)} conf={trace.get('confidence','?')} low={r.low_confidence}"
        return ProbeResult(
            tool="trace_flow",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=r.low_confidence,
            note=note,
            detail={"steps": len(steps), "confidence": trace.get("confidence")},
        )
    except Exception as exc:
        return ProbeResult(
            tool="trace_flow",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_search_code(facade, repo: str, query: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.search_code(repo_url=repo, query=query, top_k=5)
        ok = bool(r.hits)
        note = f"hits={len(r.hits)}"
        return ProbeResult(
            tool="search_code",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=not r.hits,
            note=note,
            detail={"hits": len(r.hits), "top_file": r.hits[0].citation.file_path if r.hits else None},
        )
    except Exception as exc:
        return ProbeResult(
            tool="search_code",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_query_dependencies(facade, repo: str, symbol: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.query_dependencies(repo_url=repo, symbol_name=symbol, direction="both")
        total = len(r.callers) + len(r.callees) + len(r.file_imports)
        ok = total > 0
        note = f"callers={len(r.callers)} callees={len(r.callees)} imports={len(r.file_imports)} resolved={r.query.get('resolved_refs', [])}"
        return ProbeResult(
            tool="query_dependencies",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=not ok,
            note=note,
            detail={"callers": len(r.callers), "callees": len(r.callees), "imports": len(r.file_imports)},
        )
    except Exception as exc:
        return ProbeResult(
            tool="query_dependencies",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_analyze_impact(facade, repo: str, symbol: str) -> ProbeResult:
    t0 = time.perf_counter()
    try:
        r = facade.analyze_impact(repo_url=repo, symbol_name=symbol, depth=2, direction="both")
        total = len(r.affected) + len(r.depends_on)
        ok = bool(r.seeds) and total > 0
        note = f"seeds={len(r.seeds)} affected={len(r.affected)} depends={len(r.depends_on)} low={r.low_confidence}"
        return ProbeResult(
            tool="analyze_impact",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=r.low_confidence,
            note=note,
            detail={"seeds": r.seeds, "affected_count": len(r.affected), "depends_count": len(r.depends_on)},
        )
    except Exception as exc:
        return ProbeResult(
            tool="analyze_impact",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


def _probe_view_source(facade, repo: str, symbol: str) -> ProbeResult:
    """Derive a file path from symbol (file::name or just a file path)."""
    t0 = time.perf_counter()
    try:
        if "::" in symbol:
            file_path, _, sym = symbol.partition("::")
        else:
            file_path, sym = symbol, None
        r = facade.view_source(repo_url=repo, file_path=file_path, symbol_name=sym or None)
        ok = bool(r.content)
        note = f"lines={r.total_lines} truncated={r.truncated} symbols_in_outline={len(r.outline)}"
        return ProbeResult(
            tool="view_source",
            ok=ok,
            took_ms=r.meta.took_ms,
            low_confidence=not ok,
            note=note,
            detail={"file": r.file_path, "total_lines": r.total_lines},
        )
    except Exception as exc:
        return ProbeResult(
            tool="view_source",
            ok=False,
            took_ms=int((time.perf_counter() - t0) * 1000),
            low_confidence=True,
            note=str(exc),
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _discover_symbol(facade, repo: str) -> str | None:
    """Pick a reasonable default focus symbol from the first indexed chunks."""
    try:
        from app.ingestion import IngestionPipeline
        from app.db import InMemoryFilesRepository, InMemoryReposRepository

        # Reuse already-indexed artifacts if present via the facade
        ingest_result = facade.ingestion.run(repo, force_full=False)
        chunks, _ = facade.ingestion.load_artifacts(ingest_result.repo_id)
        # Prefer a class or function chunk with a file::symbol pattern
        for c in chunks:
            if c.symbol_name and c.kind in ("class", "function") and "::" not in (c.symbol_name or ""):
                return f"{c.file_path}::{c.symbol_name}"
    except Exception:
        pass
    return None


def run_smoke(
    repo_source: str,
    *,
    query: str = "main workflow and entry points",
    symbol: str | None = None,
    workspace_root: Path | None = None,
    artifact_dir: Path | None = None,
) -> list[ProbeResult]:
    from app.mcp.service import RepoScopeFacade

    facade = RepoScopeFacade(
        workspace_root=workspace_root,
        artifact_dir=artifact_dir,
        use_hash_embedder=True,
    )

    # Warm-up: ensure indexed (counts against initial_context timing)
    results: list[ProbeResult] = []

    print(f"  [1/8] get_initial_context ...", flush=True)
    results.append(_probe_initial_context(facade, repo_source))

    print(f"  [2/8] analyze_architecture ...", flush=True)
    results.append(_probe_architecture(facade, repo_source))

    print(f"  [3/8] context_explore ({query!r}) ...", flush=True)
    results.append(_probe_context_explore(facade, repo_source, query))

    print(f"  [4/8] trace_flow ({query!r}) ...", flush=True)
    results.append(_probe_trace_flow(facade, repo_source, query))

    print(f"  [5/8] search_code ({query!r}) ...", flush=True)
    results.append(_probe_search_code(facade, repo_source, query))

    # For symbol-dependent tools, pick or discover a symbol
    focus = symbol
    if focus is None:
        print("  Discovering a focus symbol ...", flush=True)
        focus = _discover_symbol(facade, repo_source)
    if focus:
        print(f"  [6/8] query_dependencies ({focus!r}) ...", flush=True)
        results.append(_probe_query_dependencies(facade, repo_source, focus))

        print(f"  [7/8] analyze_impact ({focus!r}) ...", flush=True)
        results.append(_probe_analyze_impact(facade, repo_source, focus))

        print(f"  [8/8] view_source ({focus!r}) ...", flush=True)
        results.append(_probe_view_source(facade, repo_source, focus))
    else:
        print("  [6-8/8] Skipped symbol tools (no focus symbol found)", flush=True)

    return results


def _print_table(results: list[ProbeResult], repo_label: str) -> None:
    ok_n = sum(1 for r in results if r.ok)
    low_n = sum(1 for r in results if r.low_confidence)
    n = len(results)
    print()
    print(f"{'─' * 72}")
    print(f"  Smoke report: {repo_label}")
    print(f"  Passed: {ok_n}/{n}   Low-confidence: {low_n}/{n}")
    print(f"{'─' * 72}")
    print(f"  {'Tool':<28} {'OK':>4}  {'Low':>4}  {'ms':>6}  Note")
    print(f"  {'-'*28} {'----':>4}  {'----':>4}  {'------':>6}  ----")
    for r in results:
        ok_s = " ok " if r.ok else "FAIL"
        low_s = " low" if r.low_confidence else "    "
        note = r.note[:55]
        print(f"  {r.tool:<28} {ok_s:>4}  {low_s:>4}  {r.took_ms:>6}  {note}")
    print(f"{'─' * 72}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", help="Local path to a cloned repository")
    group.add_argument("--repo-url", help="Git URL to clone (shallow) on demand")
    group.add_argument("--all-cloned", action="store_true", help="Run on every cloned repo in repos.yaml")
    parser.add_argument("--query", default="main workflow and entry points", help="Natural-language query for explore/flow/search probes")
    parser.add_argument("--symbol", help="Focus symbol for dependency/impact/view probes (e.g. 'src/requests/sessions.py::Session.send')")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="reposcope-smoke-"))
    workspace_root = work / "ws"
    artifact_dir = work / "art"

    REPORT_DIR = ROOT / "eval" / "reports"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    all_reports: list[dict] = []

    def _run_one(repo_source: str, label: str) -> None:
        print(f"\nSmoke-testing: {label}")
        results = run_smoke(
            repo_source,
            query=args.query,
            symbol=args.symbol,
            workspace_root=workspace_root,
            artifact_dir=artifact_dir,
        )
        _print_table(results, label)

        ok_n = sum(1 for r in results if r.ok)
        low_n = sum(1 for r in results if r.low_confidence)
        report = {
            "repo": label,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "passed": ok_n,
            "total": len(results),
            "low_confidence": low_n,
            "query": args.query,
            "symbol": args.symbol,
            "results": [
                {
                    "tool": r.tool,
                    "ok": r.ok,
                    "took_ms": r.took_ms,
                    "low_confidence": r.low_confidence,
                    "note": r.note,
                    **r.detail,
                }
                for r in results
            ],
        }
        # Derive a safe filename from label
        safe = label.replace("/", "_").replace("\\", "_").replace(":", "_")
        out = REPORT_DIR / f"smoke_{safe}.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Report → {out}")
        all_reports.append(report)

    if args.all_cloned:
        repos = _load_yaml_repos()
        if not repos:
            print("Could not load repos.yaml (pyyaml not installed?). Pass --repo instead.", file=sys.stderr)
            sys.exit(1)
        for r in repos:
            local = ROOT / r.get("local_path", "")
            if local.exists():
                _run_one(str(local), r.get("id", str(local)))
            else:
                print(f"  Skipping {r.get('id')} — not cloned at {local}")
    elif args.repo_url:
        _run_one(args.repo_url, args.repo_url.split("/")[-1].removesuffix(".git"))
    else:
        _run_one(str(Path(args.repo).resolve()), Path(args.repo).name)

    # Summary if multiple repos
    if len(all_reports) > 1:
        print("\n=== Overall summary ===")
        for rep in all_reports:
            flag = "ok" if rep["passed"] == rep["total"] else "PARTIAL"
            print(f"  [{flag}] {rep['repo']}  {rep['passed']}/{rep['total']} passed  low={rep['low_confidence']}")


if __name__ == "__main__":
    main()
