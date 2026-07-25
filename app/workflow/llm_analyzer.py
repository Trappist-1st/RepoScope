"""OpenAI-compatible chat analyzer (DeepSeek / OpenAI / Ollama / etc.)."""

from __future__ import annotations

import json
import re
from typing import Any

from app.context_engine import HistoryWindow, assemble_context, load_context_config
from app.models.schemas import DependencyGraph
from app.retrieval.schemas import RetrievalHit
from app.workflow.analyzers import _INTENT_PLANS, _findings_to_markdown
from app.workflow.llm_client import chat_completion, is_placeholder_api_key
from app.workflow.schemas import Finding, Intent


class LLMAnalyzer:
    """
    Calls any OpenAI-compatible Chat Completions API and maps JSON → Finding list.
    Prefer planner-provided `plan` steps; otherwise fall back to intent templates.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_sec: float = 90.0,
        json_response: bool = True,
    ) -> None:
        if is_placeholder_api_key(api_key):
            raise ValueError(
                "LLM API key is missing or still a placeholder. "
                "Set REPOSCOPE_LLM_API_KEY in .env"
            )
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec
        self.json_response = json_response

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
        plan_hint = list(plan) if plan else list(_INTENT_PLANS[intent])
        cfg = load_context_config()
        assembled = assemble_context(
            question=question,
            plan_hint="; ".join(plan_hint),
            hits=hits,
            expanded_hits=expanded_hits,
            graph=graph,
            history=history,
            config=cfg,
            budget=token_budget,
        )
        evidence = list(assembled.code_hits) + list(assembled.expanded_hits)
        allowed = [h.citation.format() for h in evidence]
        allowed_set = set(allowed)

        user_prompt = self._build_user_prompt(
            question=question,
            intent=intent,
            plan_hint=plan_hint,
            evidence=evidence,
            graph_summary=assembled.graph_summary,
            history_text=assembled.history_text,
            allowed_citations=allowed,
            plan_locked=bool(plan),
        )
        raw = chat_completion(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are RepoScope's code-analysis engine. "
                        "Answer ONLY from the provided evidence. "
                        "Every citation MUST be copied exactly from the allowed list. "
                        "Respond with a single JSON object, no markdown fences."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            timeout_sec=self.timeout_sec,
            temperature=0.2,
            json_response=self.json_response,
        )
        out_plan, findings, md = self._parse_response(
            raw,
            question=question,
            intent=intent,
            plan_hint=plan_hint,
            allowed_citations=allowed_set,
            evidence=evidence,
            plan_locked=bool(plan),
        )
        return out_plan, findings, md, assembled.after_tokens

    def _build_user_prompt(
        self,
        *,
        question: str,
        intent: Intent,
        plan_hint: list[str],
        evidence: list[RetrievalHit],
        graph_summary: str,
        history_text: str,
        allowed_citations: list[str],
        plan_locked: bool,
    ) -> str:
        blocks: list[str] = [
            f"Intent: {intent}",
            f"Question: {question}",
            "",
        ]
        if plan_locked:
            blocks.append(
                "Analysis plan (LOCKED — keep these steps; produce one finding per step):"
            )
        else:
            blocks.append(
                "Suggested plan steps (you may refine wording, keep same count/order):"
            )
        for i, step in enumerate(plan_hint):
            blocks.append(f"{i}. {step}")

        blocks.append("")
        blocks.append("Allowed citations (use ONLY these strings):")
        if allowed_citations:
            for c in allowed_citations:
                blocks.append(f"- {c}")
        else:
            blocks.append("- (none — say insufficient evidence)")

        blocks.append("")
        blocks.append("Evidence snippets:")
        if not evidence:
            blocks.append("(empty)")
        for h in evidence:
            cite = h.citation.format()
            sym = h.symbol_name or ""
            tier = "expanded" if h.source == "graph_expand" else "direct"
            reason = h.expansion_reason or ""
            blocks.append(f"### {cite} | symbol={sym} | tier={tier} | reason={reason}")
            blocks.append(h.content[:2000])
            blocks.append("")

        if graph_summary.strip():
            blocks.append("Graph context:")
            blocks.append(graph_summary[:3000])
            blocks.append("")
        if history_text.strip():
            blocks.append("Prior analysis:")
            blocks.append(history_text[:2000])
            blocks.append("")

        blocks.append(
            "Return JSON with keys: "
            "plan (string[]), "
            "findings (array of {claim, citations, symbols, plan_step_idx, "
            "confidence, evidence_tier, expansion_reasons}), "
            "analysis_markdown (string). "
            "confidence in high|medium|low; evidence_tier in direct|expanded|mixed|none."
        )
        if plan_locked:
            blocks.append(
                "IMPORTANT: set plan to the locked steps verbatim; "
                "do not invent new plan steps."
            )
        return "\n".join(blocks)

    def _parse_response(
        self,
        raw: str,
        *,
        question: str,
        intent: Intent,
        plan_hint: list[str],
        allowed_citations: set[str],
        evidence: list[RetrievalHit],
        plan_locked: bool,
    ) -> tuple[list[str], list[Finding], str]:
        data = _loads_json_object(raw)
        if plan_locked:
            plan = list(plan_hint)
        else:
            plan = data.get("plan")
            if not isinstance(plan, list) or not plan:
                plan = list(plan_hint)
            else:
                plan = [str(p) for p in plan]

        findings_raw = data.get("findings")
        findings: list[Finding] = []
        if isinstance(findings_raw, list):
            for i, item in enumerate(findings_raw):
                if not isinstance(item, dict):
                    continue
                findings.append(
                    _finding_from_dict(
                        item,
                        fallback_idx=i,
                        allowed_citations=allowed_citations,
                        evidence=evidence,
                    )
                )

        if not findings:
            findings = [
                Finding(
                    claim=f"[{intent}] insufficient structured findings from model for `{question}`",
                    citations=[],
                    symbols=[],
                    plan_step_idx=0,
                    confidence="low",
                    evidence_tier="none",
                )
            ]

        md = data.get("analysis_markdown")
        if not isinstance(md, str) or not md.strip():
            md = _findings_to_markdown(question, intent, plan, findings)
        return plan, findings, md


def _loads_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError(f"LLM returned non-JSON content: {raw[:500]}")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("LLM JSON root must be an object")
    return data


def _finding_from_dict(
    item: dict[str, Any],
    *,
    fallback_idx: int,
    allowed_citations: set[str],
    evidence: list[RetrievalHit],
) -> Finding:
    citations_in = item.get("citations") or []
    if not isinstance(citations_in, list):
        citations_in = []
    citations = [str(c) for c in citations_in if str(c) in allowed_citations]

    symbols_in = item.get("symbols") or []
    symbols = [str(s) for s in symbols_in] if isinstance(symbols_in, list) else []

    conf = item.get("confidence", "medium")
    if conf not in {"high", "medium", "low"}:
        conf = "medium"
    tier = item.get("evidence_tier", "direct" if citations else "none")
    if tier not in {"direct", "expanded", "mixed", "none"}:
        tier = "direct" if citations else "none"

    reasons_in = item.get("expansion_reasons") or []
    reasons = [str(r) for r in reasons_in] if isinstance(reasons_in, list) else []
    if not reasons and citations:
        for h in evidence:
            if h.citation.format() in citations and h.expansion_reason:
                reasons.append(h.expansion_reason)

    idx = item.get("plan_step_idx", fallback_idx)
    try:
        plan_step_idx = int(idx) if idx is not None else fallback_idx
    except (TypeError, ValueError):
        plan_step_idx = fallback_idx

    claim = str(item.get("claim") or "").strip() or f"Finding {fallback_idx + 1}"
    return Finding(
        claim=claim,
        citations=citations,
        symbols=symbols,
        plan_step_idx=plan_step_idx,
        confidence=conf,  # type: ignore[arg-type]
        evidence_tier=tier,  # type: ignore[arg-type]
        expansion_reasons=reasons,
    )
