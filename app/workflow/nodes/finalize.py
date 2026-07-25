from __future__ import annotations

from typing import Any

from app.workflow.schemas import Finding, ReviewIssue
from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout


def _evidence_note(finding: Finding) -> str:
    if finding.evidence_tier == "direct":
        return "证据：直接检索命中"
    if finding.evidence_tier == "expanded":
        reasons = finding.expansion_reasons or ["one-hop graph expand"]
        joined = "; ".join(reasons)
        return f"证据：间接推断（图谱一跳）— {joined}"
    if finding.evidence_tier == "mixed":
        reasons = finding.expansion_reasons or []
        extra = f" — {'; '.join(reasons)}" if reasons else ""
        return f"证据：混合（直接检索 + 图谱扩展）{extra}"
    return "证据：不足 / 未接地"


def build_report_markdown(state: WorkflowState) -> str:
    intent = state.get("intent") or "summary"
    question = state.get("question") or ""
    findings: list[Finding] = list(state.get("findings") or [])
    issues: list[ReviewIssue] = list(state.get("review_issues") or [])
    low = bool(state.get("low_confidence"))
    timeouts = state.get("timeouts") or {}

    lines = [
        f"# RepoScope Report ({intent})",
        "",
        f"**Question:** {question}",
        f"**Repo:** `{state.get('repo_id') or 'n/a'}`",
        f"**Status:** {state.get('status') or ('partial' if low or timeouts else 'ok')}",
        "",
    ]

    if low:
        lines += [
            "> **注意：部分结论置信度较低**",
            "> Review 在重试上限后仍未完全通过校验；请优先核对带 low confidence 的条目。",
            "",
        ]

    if timeouts:
        timed = ", ".join(k for k, v in timeouts.items() if v)
        if timed:
            lines += [f"> 因超时未完成的节点：`{timed}`", ""]

    plan = state.get("plan") or []
    if plan:
        lines.append("## Plan")
        for i, step in enumerate(plan, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    lines.append("## Findings")
    if not findings:
        lines.append("_No findings._")
    for i, f in enumerate(findings, 1):
        lines.append(f"### {i}. {f.claim}")
        lines.append(f"- confidence: **{f.confidence}**")
        lines.append(f"- {_evidence_note(f)}")
        if f.citations:
            lines.append("- citations:")
            for c in f.citations:
                lines.append(f"  - `{c}`")
        if f.symbols:
            lines.append(f"- symbols: {', '.join(f'`{s}`' for s in f.symbols)}")
        lines.append("")

    if issues:
        lines.append("## Review Issues")
        for issue in issues:
            loc = f" finding#{issue.finding_idx}" if issue.finding_idx is not None else ""
            cite = f" ({issue.citation})" if issue.citation else ""
            lines.append(f"- [{issue.severity}] `{issue.type}`{loc}{cite}: {issue.detail}")
        lines.append("")

    retrieve_q = state.get("retrieve_query")
    if retrieve_q:
        lines += ["## Diagnostics", f"- last retrieve_query: `{retrieve_q}`", ""]

    return "\n".join(lines).rstrip() + "\n"


def build_report_json(state: WorkflowState) -> dict[str, Any]:
    findings = [f.model_dump() for f in (state.get("findings") or [])]
    issues = [i.model_dump() for i in (state.get("review_issues") or [])]
    return {
        "intent": state.get("intent"),
        "repo_id": state.get("repo_id"),
        "question": state.get("question"),
        "status": state.get("status"),
        "low_confidence": bool(state.get("low_confidence")),
        "retry_count": int(state.get("retry_count") or 0),
        "retrieve_query": state.get("retrieve_query"),
        "findings": findings,
        "review_issues": issues,
        "timeouts": state.get("timeouts") or {},
        "errors": state.get("errors") or [],
        "plan": state.get("plan") or [],
    }


def finalize_node(state: WorkflowState) -> WorkflowState:
    def _run() -> WorkflowState:
        timeouts = state.get("timeouts") or {}
        errors = state.get("errors") or []
        low = bool(state.get("low_confidence"))
        indexed = bool(state.get("indexed"))

        if state.get("status") == "failed":
            status = "failed"
        elif (not indexed) or timeouts or low or errors:
            status = "partial"
        else:
            status = "ok"

        # If review passed cleanly and no timeouts, prefer ok
        if (
            state.get("review_passed")
            and not low
            and not any(timeouts.values())
            and indexed
        ):
            status = "ok"

        md = build_report_markdown({**state, "status": status})
        js = build_report_json({**state, "status": status})
        return {
            "status": status,
            "report_markdown": md,
            "report_json": js,
        }

    try:
        return run_with_timeout("finalize", _run)
    except NodeTimeoutError:
        return {
            "timeouts": {"finalize": True},
            "errors": ["finalize timed out"],
            "status": "partial",
            "report_markdown": "# Report unavailable (finalize timeout)\n",
            "report_json": {"status": "partial", "errors": ["finalize timed out"]},
        }
