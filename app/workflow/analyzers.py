from __future__ import annotations

import re
from typing import Protocol

from app.context_engine import (
    HistoryWindow,
    assemble_context,
    findings_to_history_text,
    load_context_config,
)
from app.models.schemas import DependencyGraph
from app.retrieval.schemas import RetrievalHit
from app.workflow.schemas import Finding, Intent


class Analyzer(Protocol):
    def analyze(
        self,
        *,
        question: str,
        intent: Intent,
        hits: list[RetrievalHit],
        expanded_hits: list[RetrievalHit],
        token_budget: int,
        graph: DependencyGraph | None = None,
        history: HistoryWindow | None = None,
        plan: list[str] | None = None,
    ) -> tuple[list[str], list[Finding], str, int]:
        """Return (plan, findings, analysis_markdown, tokens_used)."""
        ...


_INTENT_PLANS: dict[Intent, list[str]] = {
    "summary": [
        "Identify key modules / entry points",
        "Summarize main responsibilities",
        "Note important dependencies between files",
    ],
    "interview": [
        "Surface architecture decisions visible in code",
        "List likely interview follow-up questions",
        "Call out risk / complexity hotspots with citations",
    ],
    "refactor": [
        "Find coupling / smell candidates",
        "Propose concrete refactor directions",
        "Cite the concrete symbols involved",
    ],
}


class StubAnalyzer:
    """
    Deterministic analyzer for control-flow tests.
    Only cites citations that appear in provided hits/expanded_hits.
    Uses planner-provided `plan` when available; otherwise intent templates.
    """

    def analyze(
        self,
        *,
        question: str,
        intent: Intent,
        hits: list[RetrievalHit],
        expanded_hits: list[RetrievalHit],
        token_budget: int,
        graph: DependencyGraph | None = None,
        history: HistoryWindow | None = None,
        plan: list[str] | None = None,
    ) -> tuple[list[str], list[Finding], str, int]:
        resolved_plan = list(plan) if plan else list(_INTENT_PLANS[intent])
        cfg = load_context_config()
        assembled = assemble_context(
            question=question,
            plan_hint="; ".join(resolved_plan),
            hits=hits,
            expanded_hits=expanded_hits,
            graph=graph,
            history=history,
            config=cfg,
            budget=token_budget,
        )
        evidence_pool = list(assembled.code_hits) + list(assembled.expanded_hits)
        findings: list[Finding] = []

        for i, step in enumerate(resolved_plan):
            if not evidence_pool:
                findings.append(
                    Finding(
                        claim=f"[{intent}] {step}: insufficient retrieved evidence for `{question}`",
                        citations=[],
                        symbols=[],
                        plan_step_idx=i,
                        confidence="low",
                        evidence_tier="none",
                    )
                )
                continue

            hit = evidence_pool[i % len(evidence_pool)]
            cite = hit.citation.format()
            symbols = [hit.symbol_name] if hit.symbol_name else _guess_symbols(hit.content)
            tier = "expanded" if hit.source == "graph_expand" else "direct"
            reasons = [hit.expansion_reason] if hit.expansion_reason else []
            findings.append(
                Finding(
                    claim=f"[{intent}] {step}: evidence in `{cite}`"
                    + (f" ({hit.symbol_name})" if hit.symbol_name else ""),
                    citations=[cite],
                    symbols=[s for s in symbols if s],
                    plan_step_idx=i,
                    confidence="medium" if tier == "expanded" else "high",
                    evidence_tier=tier,  # type: ignore[arg-type]
                    expansion_reasons=reasons,
                )
            )

        md_parts = [_findings_to_markdown(question, intent, resolved_plan, findings)]
        if assembled.graph_summary:
            md_parts.append("\n## Graph context\n" + assembled.graph_summary)
        if assembled.history_text:
            md_parts.append("\n## Prior analysis\n" + assembled.history_text)
        md = "\n".join(md_parts)
        return resolved_plan, findings, md, assembled.after_tokens


class HallucinatingAnalyzer:
    """
    Test double: first call injects a fake citation; later calls behave like StubAnalyzer.
    """

    def __init__(self, stub: StubAnalyzer | None = None) -> None:
        self._stub = stub or StubAnalyzer()
        self.calls = 0
        self.hallucinated_citation = "nonexistent/FakeModule.py:1-99"

    def analyze(
        self,
        *,
        question: str,
        intent: Intent,
        hits: list[RetrievalHit],
        expanded_hits: list[RetrievalHit],
        token_budget: int,
        graph: DependencyGraph | None = None,
        history: HistoryWindow | None = None,
        plan: list[str] | None = None,
    ) -> tuple[list[str], list[Finding], str, int]:
        self.calls += 1
        plan_out, findings, md, tokens = self._stub.analyze(
            question=question,
            intent=intent,
            hits=hits,
            expanded_hits=expanded_hits,
            token_budget=token_budget,
            graph=graph,
            history=history,
            plan=plan,
        )
        if self.calls == 1 and findings:
            findings[0] = findings[0].model_copy(
                update={
                    "citations": [self.hallucinated_citation],
                    "claim": findings[0].claim + " [HALLUCINATED CITE]",
                    "evidence_tier": "none",
                    "expansion_reasons": [],
                }
            )
            md = _findings_to_markdown(question, intent, plan_out, findings)
        return plan_out, findings, md, tokens


def _guess_symbols(content: str) -> list[str]:
    names = re.findall(
        r"(?:def|class|function|public\s+class|public\s+static\s+\w+)\s+([A-Za-z_][A-Za-z0-9_]*)",
        content,
    )
    return names[:3]


def _findings_to_markdown(
    question: str, intent: Intent, plan: list[str], findings: list[Finding]
) -> str:
    lines = [
        f"# Analysis ({intent})",
        f"Question: {question}",
        "",
        "## Plan",
    ]
    for i, step in enumerate(plan):
        lines.append(f"{i + 1}. {step}")
    lines.append("")
    lines.append("## Findings")
    for f in findings:
        lines.append(f"- {f.claim}")
        if f.citations:
            lines.append(f"  - citations: {', '.join(f.citations)}")
    return "\n".join(lines)
