from __future__ import annotations

import re
from typing import Any

from app.retrieval.schemas import RetrieveRequest, RetrievalHit
from app.retrieval.service import RetrievalService
from app.workflow.schemas import AnalysisPlan
from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout

# Keep queries short — long ";"-joined planner dumps kill BM25 recall.
_MAX_QUERY_CHARS = 220


def _coerce_analysis_plan(raw: Any) -> AnalysisPlan | None:
    if raw is None:
        return None
    if isinstance(raw, AnalysisPlan):
        return raw
    if isinstance(raw, dict):
        try:
            return AnalysisPlan.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None
    return None


def _hint_terms(hints: list[str]) -> tuple[list[str], list[str]]:
    symbol_bits: list[str] = []
    path_bits: list[str] = []
    for hint in hints:
        hint = hint.strip()
        if not hint:
            continue
        if re.search(r":\d+-\d+$", hint):
            path = hint.rsplit(":", 1)[0]
            path_bits.append(path)
            stem = path.replace("\\", "/").split("/")[-1].split(".")[0]
            if stem:
                symbol_bits.append(stem)
        elif hint.startswith("symbol:"):
            symbol_bits.append(hint.split(":", 1)[1].strip())
        elif hint.startswith("search:"):
            symbol_bits.append(hint.split(":", 1)[1].strip())
        else:
            symbol_bits.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", hint)[:3])
    return symbol_bits, path_bits


def _join_query(parts: list[str], *, max_chars: int = _MAX_QUERY_CHARS) -> str:
    merged = list(dict.fromkeys(p.strip() for p in parts if p and p.strip()))
    if not merged:
        return ""
    out: list[str] = []
    used = 0
    for part in merged:
        # space-join is friendlier for BM25 than many ";" segments
        add = (" " + part) if out else part
        if used + len(add) > max_chars:
            break
        out.append(part)
        used += len(add)
    return " ".join(out)


def build_retrieve_candidates(state: WorkflowState) -> list[tuple[str, str]]:
    """
    Ordered (strategy, query) candidates.
    Broader / simpler queries come later for hits=0 fallback and review retries.
    """
    base = (state.get("question") or "").strip()
    hints = list(state.get("retry_hints") or [])
    retry = int(state.get("retry_count") or 0)
    plan = _coerce_analysis_plan(state.get("analysis_plan"))
    plan_parts = plan.retrieval_query_parts() if plan else []
    keyword_only: list[str] = []
    if plan:
        for step in plan.steps:
            keyword_only.extend(step.keywords[:3])
            # also pull short tokens from search_query
            keyword_only.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", step.search_query)[:4])
    keyword_only = list(dict.fromkeys(keyword_only))[:12]

    symbol_bits, path_bits = _hint_terms(hints)
    hint_q = _join_query([*symbol_bits, *path_bits], max_chars=120)

    candidates: list[tuple[str, str]] = []

    def add(label: str, parts: list[str]) -> None:
        q = _join_query(parts)
        if q and (label, q) not in candidates and q not in {c[1] for c in candidates}:
            candidates.append((label, q))

    if retry <= 0:
        add("plan_compact", [base[:80], *plan_parts[:4], hint_q])
        add("plan_keywords", [base[:60], *keyword_only[:8], hint_q])
        add("question", [base[:100], hint_q])
    elif retry == 1:
        add("retry_hints", [base[:60], hint_q, *keyword_only[:6]])
        add("keywords_only", [*keyword_only[:10], hint_q] or [base[:80]])
        add("question", [base[:80], hint_q])
    else:
        add("retry_broad", [base[:50], hint_q])
        add("question_short", [base[:50], hint_q])
        add("explore_seed", ["readme overview module class function main entry", hint_q])

    if not candidates and base:
        candidates.append(("question", base[:100]))
    return candidates


def _build_retrieve_query(state: WorkflowState) -> str:
    """Backward-compatible single-query helper (first candidate)."""
    cands = build_retrieve_candidates(state)
    return cands[0][1] if cands else (state.get("question") or "")


def make_retrieve_node(retrieval: RetrievalService) -> Any:
    def retrieve_node(state: WorkflowState) -> WorkflowState:
        def _run() -> WorkflowState:
            repo_id = state.get("repo_id")
            if not repo_id:
                return {
                    "hits": [],
                    "expanded_hits": [],
                    "retrieve_query": state.get("question") or "",
                    "primary_citations": [],
                    "expanded_citations": [],
                    "errors": ["retrieve skipped: missing repo_id"],
                }

            candidates = build_retrieve_candidates(state)
            used_query = ""
            strategy = ""
            resp_hits: list[RetrievalHit] = []
            resp_expanded: list[RetrievalHit] = []
            errors: list[str] = []

            for label, query in candidates:
                used_query = query
                strategy = label
                resp = retrieval.retrieve(
                    RetrieveRequest(
                        repo_id=repo_id,
                        query=query,
                        mode="hybrid",
                        graph_expand=True,
                    )
                )
                if resp.hits:
                    resp_hits = list(resp.hits)
                    resp_expanded = list(resp.expanded_hits)
                    break

            if not resp_hits:
                # Last resort: diverse chunk sample so analyze is not empty-handed.
                explore = retrieval.explore(repo_id, limit=5)
                if explore:
                    resp_hits = explore
                    strategy = f"{strategy}|explore_chunks"
                    errors.append(
                        "retrieve: all query strategies returned 0 hits; "
                        "fell back to explore_chunks sample"
                    )
                else:
                    errors.append(
                        "retrieve: 0 hits and no indexed chunks "
                        "(repo may contain only unsupported file types)"
                    )

            primary = [h.citation.format() for h in resp_hits]
            expanded = [h.citation.format() for h in resp_expanded]
            out: WorkflowState = {
                "hits": resp_hits,
                "expanded_hits": resp_expanded,
                "retrieve_query": f"[{strategy}] {used_query}"[:300],
                "primary_citations": primary,
                "expanded_citations": expanded,
                "retry_hints": [],
            }
            if errors:
                out["errors"] = errors
            return out

        try:
            return run_with_timeout("retrieve", _run)
        except NodeTimeoutError:
            return {
                "timeouts": {"retrieve": True},
                "errors": ["retrieve timed out"],
                "hits": state.get("hits") or [],
                "expanded_hits": state.get("expanded_hits") or [],
                "status": "partial",
            }

    return retrieve_node
