from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from app.audit import new_run_id
from app.mcp.service import RepoScopeFacade


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_analyze_events(
    facade: RepoScopeFacade,
    *,
    question: str,
    repo_source: str,
    intent_hint: str | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Stream workflow node updates as SSE-friendly dicts.
    Uses LangGraph stream_mode='updates'.
    """
    run_id = new_run_id()
    timings: dict[str, float] = {}
    yield {
        "event": "run_started",
        "run_id": run_id,
        "node_name": "run",
        "status": "started",
        "timestamp": _ts(),
        "summary": f"question={question[:120]}",
        "warnings": facade._audit_warnings(),
    }

    initial = {
        "question": question,
        "repo_source": repo_source,
        "intent_hint": intent_hint,
        "token_budget": 4000,
        "max_review_retries": 2,
        "retry_count": 0,
        "retry_hints": [],
        "timeouts": {},
        "errors": [],
        "hits": [],
        "expanded_hits": [],
        "findings": [],
        "review_issues": [],
        "plan": [],
        "low_confidence": False,
        "review_passed": False,
        "review_should_retry": False,
        "indexed": False,
    }

    facade.state_cache.set(run_id, {"status": "running", "node": "start"})
    final_state: dict[str, Any] = dict(initial)
    t_node = time.perf_counter()

    try:
        for update in facade.runner.graph.stream(initial, stream_mode="updates"):
            for node_name, payload in update.items():
                elapsed = time.perf_counter() - t_node
                timings[node_name] = timings.get(node_name, 0.0) + elapsed
                t_node = time.perf_counter()
                if isinstance(payload, dict):
                    final_state.update(payload)
                summary = _summarize_node(node_name, payload if isinstance(payload, dict) else {})
                facade.state_cache.set(
                    run_id,
                    {
                        "status": "running",
                        "node": node_name,
                        "summary": summary,
                        "repo_id": final_state.get("repo_id"),
                    },
                )
                yield {
                    "event": "node",
                    "run_id": run_id,
                    "node_name": node_name,
                    "status": "completed",
                    "timestamp": _ts(),
                    "summary": summary,
                    "elapsed_ms": int(elapsed * 1000),
                }
    except Exception as exc:  # noqa: BLE001
        facade.state_cache.set(run_id, {"status": "failed", "error": str(exc)})
        yield {
            "event": "error",
            "run_id": run_id,
            "node_name": "run",
            "status": "failed",
            "timestamp": _ts(),
            "summary": str(exc),
        }
        return

    # Persist audit
    from app.audit import AgentRunRecord

    warnings = facade._audit_warnings()
    record = AgentRunRecord(
        run_id=run_id,
        repo_id=str(final_state.get("repo_id") or ""),
        question=question,
        intent=final_state.get("intent"),
        node_timings={k: round(v * 1000, 2) for k, v in timings.items()},
        result={
            "status": final_state.get("status"),
            "report_markdown": final_state.get("report_markdown"),
            "low_confidence": final_state.get("low_confidence"),
        },
        review_passed=bool(final_state.get("review_passed")),
        low_confidence=bool(final_state.get("low_confidence")),
        status=str(final_state.get("status") or "ok"),
        warnings=warnings,
    )
    facade.audit_store.save(record)
    facade.state_cache.set(
        run_id,
        {
            "status": "done",
            "repo_id": record.repo_id,
            "review_passed": record.review_passed,
        },
    )
    yield {
        "event": "done",
        "run_id": run_id,
        "node_name": "finalize",
        "status": "completed",
        "timestamp": _ts(),
        "summary": f"status={record.status} review_passed={record.review_passed}",
        "report_markdown": final_state.get("report_markdown"),
        "warnings": warnings,
        "timings_ms": record.node_timings,
    }


def _summarize_node(node_name: str, payload: dict[str, Any]) -> str:
    if node_name == "route":
        return f"intent={payload.get('intent')}"
    if node_name == "repo_parse":
        return f"repo_id={payload.get('repo_id')} indexed={payload.get('indexed')}"
    if node_name == "planner":
        plan = payload.get("plan") or []
        return f"source={payload.get('plan_source')} steps={len(plan)}"
    if node_name == "retrieve":
        hits = payload.get("hits") or []
        exp = payload.get("expanded_hits") or []
        return f"hits={len(hits)} expanded={len(exp)} q={payload.get('retrieve_query', '')[:80]}"
    if node_name == "analyze":
        findings = payload.get("findings") or []
        return f"findings={len(findings)} tokens={payload.get('tokens_used')}"
    if node_name == "review":
        return (
            f"passed={payload.get('review_passed')} "
            f"retry={payload.get('review_should_retry')} "
            f"issues={len(payload.get('review_issues') or [])}"
        )
    if node_name == "finalize":
        return f"status={payload.get('status')}"
    return node_name


def format_sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
