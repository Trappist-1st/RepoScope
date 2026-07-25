from __future__ import annotations

from app.workflow.planner import generate_analysis_plan
from app.workflow.schemas import Intent
from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout


def _run_planner(state: WorkflowState) -> WorkflowState:
    intent: Intent = state.get("intent") or "summary"
    plan = generate_analysis_plan(
        question=state.get("question") or "",
        intent=intent,
        repo_id=state.get("repo_id"),
        local_path=state.get("local_path"),
        token_budget=int(state.get("token_budget") or 4000),
    )
    return {
        "analysis_plan": plan,
        "plan": plan.action_list(),
        "plan_source": plan.source,
    }


def planner_node(state: WorkflowState) -> WorkflowState:
    try:
        return run_with_timeout("planner", lambda: _run_planner(state))
    except NodeTimeoutError:
        from app.workflow.planner import template_analysis_plan

        intent: Intent = state.get("intent") or "summary"
        plan = template_analysis_plan(
            question=state.get("question") or "",
            intent=intent,
        )
        return {
            "analysis_plan": plan,
            "plan": plan.action_list(),
            "plan_source": "template",
            "timeouts": {"planner": True},
            "errors": ["planner timed out; fell back to template plan"],
        }
