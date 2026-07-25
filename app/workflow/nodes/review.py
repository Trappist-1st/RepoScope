from __future__ import annotations

import re

from app.workflow.schemas import Finding, ReviewIssue
from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout

_CITE_RE = re.compile(r"^(.+):(\d+)-(\d+)$")


def _parseable(cite: str) -> bool:
    return bool(_CITE_RE.match(cite))


def _symbol_in_code(sym: str, code: str) -> bool:
    if not sym or not code:
        return False
    # Support Class.method → check full and parts
    parts = [sym] + ([sym.split(".", 1)[0], sym.split(".", 1)[1]] if "." in sym else [])
    for part in parts:
        if not part:
            continue
        if re.search(rf"\b{re.escape(part)}\b", code):
            return True
    return False


def _covers(finding: Finding, step_idx: int, subq: str) -> bool:
    if finding.plan_step_idx == step_idx:
        return True
    tokens = [t for t in re.findall(r"[\w\u4e00-\u9fff]+", subq.lower()) if len(t) > 1]
    claim = finding.claim.lower()
    return bool(tokens) and sum(1 for t in tokens if t in claim) >= max(1, len(tokens) // 2)


def _content_by_citation(state: WorkflowState) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for h in list(state.get("hits") or []) + list(state.get("expanded_hits") or []):
        mapping[h.citation.format()] = h.content
    return mapping


def _expansion_reason_by_citation(state: WorkflowState) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for h in state.get("expanded_hits") or []:
        if h.expansion_reason:
            mapping[h.citation.format()] = h.expansion_reason
    return mapping


def run_review(
    state: WorkflowState,
    *,
    expand_citation_hard_fail: bool = False,
) -> WorkflowState:
    primary = set(state.get("primary_citations") or [])
    expanded = set(state.get("expanded_citations") or [])
    # Also accept live hits in case lists drifted
    for h in state.get("hits") or []:
        primary.add(h.citation.format())
    for h in state.get("expanded_hits") or []:
        expanded.add(h.citation.format())
    allowed = primary | expanded

    content_by_cite = _content_by_citation(state)
    expand_reason = _expansion_reason_by_citation(state)

    findings = [f.model_copy(deep=True) for f in (state.get("findings") or [])]
    plan = list(state.get("plan") or [])
    issues: list[ReviewIssue] = []

    # A: plan coverage
    for i, subq in enumerate(plan):
        if not any(_covers(f, i, subq) for f in findings):
            issues.append(
                ReviewIssue(
                    type="incomplete_plan",
                    detail=f"plan step not covered: {subq}",
                    severity="high",
                )
            )

    # B/C: per finding
    for idx, finding in enumerate(findings):
        cites = list(finding.citations)
        if not cites:
            issues.append(
                ReviewIssue(
                    type="missing_citation",
                    finding_idx=idx,
                    detail="claim has no citation",
                    severity="high",
                )
            )
            continue

        used_expanded = False
        used_direct = False
        reasons: list[str] = list(finding.expansion_reasons)

        for cite in cites:
            if not _parseable(cite):
                issues.append(
                    ReviewIssue(
                        type="malformed_citation",
                        finding_idx=idx,
                        citation=cite,
                        detail="citation must be path:start-end",
                        severity="high",
                    )
                )
                continue

            if cite not in allowed:
                issues.append(
                    ReviewIssue(
                        type="citation_not_in_retrieve",
                        finding_idx=idx,
                        citation=cite,
                        detail="hallucinated or stale citation",
                        severity="high",
                    )
                )
                continue

            if cite in primary:
                used_direct = True
            elif cite in expanded:
                used_expanded = True
                reason = expand_reason.get(cite)
                if reason and reason not in reasons:
                    reasons.append(reason)
                sev: str = "high" if expand_citation_hard_fail else "low"
                issues.append(
                    ReviewIssue(
                        type="citation_from_expand",
                        finding_idx=idx,
                        citation=cite,
                        detail=(
                            f"evidence from one-hop graph expand"
                            + (f" ({reason})" if reason else "")
                        ),
                        severity=sev,  # type: ignore[arg-type]
                    )
                )

        # C: symbol grounding against any of the finding's cited snippets
        for sym in finding.symbols:
            if not any(
                _symbol_in_code(sym, content_by_cite.get(c, ""))
                for c in cites
                if c in content_by_cite
            ):
                # Only flag if at least one cite was allowed (otherwise B2 already failed)
                if any(c in allowed for c in cites):
                    issues.append(
                        ReviewIssue(
                            type="symbol_not_in_citation",
                            finding_idx=idx,
                            citation=cites[0] if cites else None,
                            detail=f"symbol `{sym}` not found in cited snippets",
                            severity="high",
                        )
                    )

        # Update evidence provenance on finding (for finalize transparency)
        if used_direct and used_expanded:
            tier = "mixed"
        elif used_direct:
            tier = "direct"
        elif used_expanded:
            tier = "expanded"
        else:
            tier = finding.evidence_tier or "none"

        updates: dict = {"evidence_tier": tier, "expansion_reasons": reasons}
        if used_expanded and not used_direct and finding.confidence == "high":
            updates["confidence"] = "medium"
        findings[idx] = finding.model_copy(update=updates)

    hard = [i for i in issues if i.severity == "high"]
    soft = [i for i in issues if i.severity == "low"]
    passed = len(hard) == 0

    # Soft downgrades already applied via evidence_tier; mark confidence for soft-only findings
    if passed and soft:
        for issue in soft:
            if issue.finding_idx is not None and issue.finding_idx < len(findings):
                f = findings[issue.finding_idx]
                if f.confidence == "high":
                    findings[issue.finding_idx] = f.model_copy(update={"confidence": "medium"})

    retry_count = int(state.get("retry_count") or 0)
    max_retries = int(state.get("max_review_retries") or 2)
    should_retry = (not passed) and (retry_count < max_retries)

    out: WorkflowState = {
        "findings": findings,
        "review_issues": issues,
        "review_passed": passed,
        "review_should_retry": should_retry,
    }

    if not passed and should_retry:
        hints: list[str] = []
        for issue in hard:
            if issue.citation:
                hints.append(issue.citation)
            if issue.type == "symbol_not_in_citation" and issue.detail:
                # extract `symbol`
                m = re.search(r"`([^`]+)`", issue.detail)
                if m:
                    hints.append(f"symbol:{m.group(1)}")
                else:
                    hints.append(issue.detail)
            if issue.type == "incomplete_plan":
                hints.append(issue.detail)
                # Prefer the planner's search_query for the uncovered action.
                analysis_plan = state.get("analysis_plan")
                steps = []
                if analysis_plan is not None:
                    steps = getattr(analysis_plan, "steps", None) or []
                    if isinstance(analysis_plan, dict):
                        steps = analysis_plan.get("steps") or []
                for step in steps:
                    action = getattr(step, "action", None)
                    search = getattr(step, "search_query", None)
                    if isinstance(step, dict):
                        action = step.get("action")
                        search = step.get("search_query")
                    if action and action in issue.detail and search:
                        hints.append(f"search:{search}")
                        break
        # dedupe
        out["retry_hints"] = list(dict.fromkeys(hints))
        out["retry_count"] = retry_count + 1
    elif not passed and not should_retry:
        out["low_confidence"] = True
        out["retry_count"] = retry_count
        # mark hard-failed findings low confidence
        for issue in hard:
            if issue.finding_idx is not None and issue.finding_idx < len(findings):
                f = findings[issue.finding_idx]
                findings[issue.finding_idx] = f.model_copy(update={"confidence": "low"})
        out["findings"] = findings
    else:
        out["low_confidence"] = False

    return out


def review_node(state: WorkflowState) -> WorkflowState:
    try:
        return run_with_timeout("review", lambda: run_review(state))
    except NodeTimeoutError:
        return {
            "timeouts": {"review": True},
            "errors": ["review timed out"],
            "review_passed": False,
            "review_should_retry": False,
            "low_confidence": True,
            "review_issues": [
                ReviewIssue(
                    type="review_timeout",
                    detail="review timed out",
                    severity="high",
                )
            ],
        }
