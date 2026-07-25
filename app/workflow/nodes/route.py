from __future__ import annotations

import re

from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout


_SUMMARY = re.compile(r"(摘要|总结|架构|overview|summary|architecture|模块)", re.I)
_INTERVIEW = re.compile(r"(面试|追问|interview|follow-?up|考察)", re.I)
_REFACTOR = re.compile(r"(重构|refactor|smell|耦合|优化建议)", re.I)


def _route_intent(state: WorkflowState) -> WorkflowState:
    hint = state.get("intent_hint")
    if hint in {"summary", "interview", "refactor"}:
        return {"intent": hint, "route_notes": "from intent_hint"}

    q = state.get("question") or ""
    if _INTERVIEW.search(q):
        return {"intent": "interview", "route_notes": "keyword:interview"}
    if _REFACTOR.search(q):
        return {"intent": "refactor", "route_notes": "keyword:refactor"}
    if _SUMMARY.search(q):
        return {"intent": "summary", "route_notes": "keyword:summary"}
    return {"intent": "summary", "route_notes": "default:summary"}


def route_node(state: WorkflowState) -> WorkflowState:
    try:
        return run_with_timeout("route", lambda: _route_intent(state))
    except NodeTimeoutError:
        return {
            "intent": "summary",
            "route_notes": "timeout:defaulted",
            "timeouts": {"route": True},
            "errors": ["route timed out; defaulted to summary"],
        }
