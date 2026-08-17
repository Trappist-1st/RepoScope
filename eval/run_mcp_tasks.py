"""Scripted MCP-tool agent tasks (success + step count). Not a live LLM agent.

Policies map a task to a short tool sequence (BIRD/Spider: NL goal → structured calls).

  python -m eval.run_mcp_tasks
  python -m eval.run_mcp_tasks --skip-remote
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.mcp.service import RepoScopeFacade
from eval.metrics import ref_matches
from eval.repo_io import ROOT, load_jsonl, resolve_repo

GOLD = ROOT / "eval" / "gold" / "mcp_tasks.jsonl"
REPORT_DIR = ROOT / "eval" / "reports"


def _dump(obj) -> str:
    if hasattr(obj, "model_dump"):
        return json.dumps(obj.model_dump(), ensure_ascii=False)
    return json.dumps(obj, ensure_ascii=False)


def run_policy(facade: RepoScopeFacade, repo: str, task: dict) -> tuple[list[str], str]:
    policy = task.get("policy", "explore_first")
    tools: list[str] = []
    blobs: list[str] = []
    if policy == "explore_first":
        tools.append("context_explore")
        blobs.append(_dump(facade.context_explore(repo_url=repo, query=task["query"])))
    elif policy == "flow_first":
        tools.append("trace_flow")
        blobs.append(_dump(facade.trace_flow(repo_url=repo, question=task["query"])))
    elif policy == "impact_first":
        tools.append("analyze_impact")
        blobs.append(
            _dump(
                facade.analyze_impact(
                    repo_url=repo,
                    symbol_name=task.get("symbol_name") or task["query"],
                    direction="both",
                    depth=2,
                )
            )
        )
    elif policy == "architecture_first":
        tools.append("analyze_architecture")
        blobs.append(_dump(facade.analyze_architecture(repo_url=repo)))
    elif policy == "search_first":
        tools.append("search_code")
        blobs.append(
            _dump(facade.search_code(repo_url=repo, query=task["query"], top_k=10))
        )
    elif policy == "deps_first":
        tools.append("query_dependencies")
        blobs.append(
            _dump(
                facade.query_dependencies(
                    repo_url=repo,
                    symbol_name=task.get("symbol_name") or task["query"],
                    direction=task.get("direction", "both"),
                    limit=20,
                )
            )
        )
    elif policy == "initial_context_first":
        tools.append("get_initial_context")
        blobs.append(_dump(facade.get_initial_context(repo_url=repo)))
    else:
        tools.append("context_explore")
        blobs.append(_dump(facade.context_explore(repo_url=repo, query=task["query"])))
    return tools, "\n".join(blobs)


def success(blob: str, spec: dict) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for f in spec.get("must_files") or []:
        if f.replace("\\", "/") not in blob.replace("\\", "/"):
            missing.append(f"file:{f}")
    for s in spec.get("must_symbols") or []:
        if not ref_matches(blob, s):
            missing.append(f"symbol:{s}")
    return not missing, missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-remote", action="store_true")
    args = parser.parse_args()
    tasks = load_jsonl(GOLD)
    work = Path(tempfile.mkdtemp(prefix="reposcope-mcp-tasks-"))
    facade = RepoScopeFacade(
        workspace_root=work / "ws",
        artifact_dir=work / "art",
        use_hash_embedder=True,
    )
    rows = []
    ok_n = 0
    for task in tasks:
        if args.skip_remote and task.get("repo_url"):
            continue
        try:
            path = resolve_repo(
                task.get("repo_path"), task.get("repo_url"), ROOT / "data" / "eval_repos"
            )
        except FileNotFoundError:
            rows.append({"id": task["id"], "skipped": True})
            continue
        tools, blob = run_policy(facade, str(path), task)
        passed, missing = success(blob, task.get("success") or {})
        ok_n += int(passed)
        rows.append(
            {
                "id": task["id"],
                "goal": task["goal"],
                "policy": task.get("policy"),
                "tools": tools,
                "steps": len(tools),
                "passed": passed,
                "missing": missing,
            }
        )
    n = len([r for r in rows if not r.get("skipped")])
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n": n,
        "passed": ok_n,
        "success_rate": (ok_n / n) if n else 0.0,
        "mean_steps": (sum(r["steps"] for r in rows if "steps" in r) / n) if n else 0.0,
        "rows": rows,
        "note": "Scripted policies, not a live coding agent. Success = must_files/symbols appear in tool JSON.",
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "mcp_tasks.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"MCP tasks: {ok_n}/{n} passed  mean_steps={payload['mean_steps']:.2f}")
    for r in rows:
        if r.get("skipped"):
            continue
        flag = "ok" if r["passed"] else "FAIL"
        print(f"  [{flag}] {r['id']} steps={r['steps']} missing={r['missing']}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
