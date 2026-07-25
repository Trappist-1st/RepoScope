"""Dynamic analysis planner (LLM) with template fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.workflow.analyzers import _INTENT_PLANS
from app.workflow.llm_client import chat_completion, is_placeholder_api_key
from app.workflow.schemas import AnalysisPlan, Intent, PlanStep

_INTENT_GUIDANCE: dict[Intent, str] = {
    "summary": (
        "Focus on architecture, entry points, module responsibilities, and dependencies."
    ),
    "interview": (
        "Focus on design decisions, patterns, complexity hotspots, and follow-up questions."
    ),
    "refactor": (
        "Focus on coupling, smells, concrete refactor directions, and involved symbols."
    ),
}


def summarize_repo_tree(local_path: str | None, *, max_entries: int = 48) -> str:
    if not local_path:
        return ""
    root = Path(local_path)
    if not root.is_dir():
        return ""
    skip = {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "target",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
    lines: list[str] = []
    try:
        for path in sorted(root.rglob("*")):
            if len(lines) >= max_entries:
                break
            rel = path.relative_to(root).as_posix()
            parts = rel.split("/")
            if any(p in skip for p in parts):
                continue
            if path.is_dir():
                lines.append(rel + "/")
            elif path.suffix.lower() in {
                ".py",
                ".js",
                ".ts",
                ".tsx",
                ".jsx",
                ".java",
                ".go",
                ".rs",
                ".md",
            }:
                lines.append(rel)
    except OSError:
        return ""
    return "\n".join(lines)


def template_analysis_plan(
    *,
    question: str,
    intent: Intent,
) -> AnalysisPlan:
    steps: list[PlanStep] = []
    for i, action in enumerate(_INTENT_PLANS[intent], start=1):
        keywords = _keywords_from_text(action)
        search = _short_query(" ".join(dict.fromkeys([*keywords[:5], action])))
        steps.append(
            PlanStep(
                step_id=i,
                action=action,
                search_query=search,
                reasoning="Template fallback step (LLM planner unavailable or failed).",
                keywords=keywords,
            )
        )
    return AnalysisPlan(
        intent=intent,
        steps=steps,
        overall_goal=question.strip() or f"{intent} analysis",
        expected_outputs=[s.action for s in steps],
        source="template",
    )


def generate_analysis_plan(
    *,
    question: str,
    intent: Intent,
    repo_id: str | None = None,
    local_path: str | None = None,
    token_budget: int = 4000,
) -> AnalysisPlan:
    """
    Prefer LLM plan when REPOSCOPE_ANALYZER_PROVIDER=llm and a real API key is set.
    Always falls back to intent templates on failure / placeholder key / provider=stub.
    """
    from app.config import settings

    provider = (settings.analyzer_provider or "stub").strip().lower()
    if provider not in {"llm", "openai", "openai_compatible"}:
        return template_analysis_plan(question=question, intent=intent)
    if is_placeholder_api_key(settings.llm_api_key):
        return template_analysis_plan(question=question, intent=intent)

    tree = summarize_repo_tree(local_path)
    prompt = _build_prompt(
        question=question,
        intent=intent,
        repo_id=repo_id,
        repo_structure=tree,
        token_budget=token_budget,
    )
    try:
        raw = chat_completion(
            api_key=settings.llm_api_key or "",
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a repository analysis planner. "
                        "Break the user question into 2-5 executable steps. "
                        "Each search_query must be SHORT (<= 8 tokens) and match indexed content. "
                        "Indexed file types: .py .js/.ts/.tsx .java .md. "
                        "Respond with one JSON object only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            timeout_sec=min(60.0, float(settings.llm_timeout_sec)),
            temperature=0.2,
            json_response=settings.llm_json_response,
        )
        plan = _parse_plan(raw, question=question, intent=intent)
        if plan.steps:
            return plan
    except Exception:  # noqa: BLE001 — planner must never crash the workflow
        pass
    return template_analysis_plan(question=question, intent=intent)


def _build_prompt(
    *,
    question: str,
    intent: Intent,
    repo_id: str | None,
    repo_structure: str,
    token_budget: int,
) -> str:
    structure = repo_structure[:2500] if repo_structure else "(unavailable)"
    return f"""## Task
Generate a concrete analysis plan for this repository question.

## Question
{question}

## Intent
{intent}
{_INTENT_GUIDANCE.get(intent, "")}

## Repo
repo_id: {repo_id or "unknown"}
structure sample (paths that may exist on disk):
{structure}

## Budget
token_budget≈{token_budget}; produce 2-5 steps.

## Critical rules for search_query
1. search_query MUST be <= 8 tokens / <= 64 characters.
2. Prefer identifiers and topic words that appear in the structure sample
   (e.g. file stems like `two-pointers`, `Scheduler`, `README`).
3. Do NOT invent long file lists or paste many filenames into one query.
4. Do NOT invent files that are not in the structure sample.
5. keywords: 3-5 short terms per step (no sentences).
6. If the repo looks documentation-heavy (.md), plan around topic docs + any code blocks,
   not a fake Java/Spring architecture.
7. Return JSON:
{{
  "intent": "{intent}",
  "overall_goal": "...",
  "expected_outputs": ["..."],
  "steps": [
    {{
      "step_id": 1,
      "action": "...",
      "search_query": "short keywords only",
      "reasoning": "...",
      "keywords": ["...", "..."]
    }}
  ]
}}
"""


def _parse_plan(raw: str, *, question: str, intent: Intent) -> AnalysisPlan:
    data = _loads_json_object(raw)
    steps_raw = data.get("steps")
    steps: list[PlanStep] = []
    if isinstance(steps_raw, list):
        for i, item in enumerate(steps_raw, start=1):
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip()
            search = str(item.get("search_query") or "").strip()
            if not action:
                continue
            if not search:
                search = action
            kws = item.get("keywords") or []
            keywords = [str(k).strip() for k in kws if str(k).strip()] if isinstance(kws, list) else []
            keywords = keywords[:6] or _keywords_from_text(action)
            try:
                step_id = int(item.get("step_id") or i)
            except (TypeError, ValueError):
                step_id = i
            steps.append(
                PlanStep(
                    step_id=max(1, step_id),
                    action=action,
                    search_query=_short_query(search),
                    reasoning=str(item.get("reasoning") or "").strip(),
                    keywords=keywords,
                )
            )

    if len(steps) > 5:
        steps = steps[:5]
    if len(steps) < 2:
        # too thin — treat as failure so caller can fall back
        return AnalysisPlan(intent=intent, steps=[], source="llm")

    # normalize ids to 1..n
    for i, step in enumerate(steps, start=1):
        step.step_id = i

    outputs = data.get("expected_outputs") or []
    if not isinstance(outputs, list):
        outputs = []
    goal = str(data.get("overall_goal") or question).strip()
    return AnalysisPlan(
        intent=intent,
        steps=steps,
        overall_goal=goal or question,
        expected_outputs=[str(o) for o in outputs],
        source="llm",
    )


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
            raise RuntimeError(f"Planner returned non-JSON: {raw[:400]}")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("Planner JSON root must be an object")
    return data


def _keywords_from_text(text: str) -> list[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "into",
        "main",
        "note",
        "list",
        "call",
        "find",
        "out",
        "key",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text)
    out: list[str] = []
    for w in words:
        low = w.lower()
        if low in stop:
            continue
        out.append(w)
    return list(dict.fromkeys(out))[:6]


def _short_query(text: str, *, max_tokens: int = 8, max_chars: int = 64) -> str:
    tokens = [t for t in re.findall(r"[A-Za-z0-9_./+-]+", text or "") if t]
    clipped = " ".join(tokens[:max_tokens])
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars].rstrip()
    return clipped
