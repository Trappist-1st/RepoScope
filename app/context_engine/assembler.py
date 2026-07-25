from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.context_engine.config import ContextConfig, load_context_config
from app.context_engine.features import estimate_tokens
from app.context_engine.priority import ScoredHit, score_candidates
from app.models.schemas import DependencyGraph
from app.retrieval.schemas import RetrievalHit


@dataclass
class HistoryItem:
    """One analyze-round summary for the history sliding window."""
    text: str
    round_idx: int = 0


@dataclass
class AssembledContext:
    code_hits: list[RetrievalHit]
    expanded_hits: list[RetrievalHit]
    graph_summary: str
    history_text: str
    before_tokens: int
    after_tokens: int
    code_tokens: int
    graph_tokens: int
    history_tokens: int
    reserve_tokens: int
    kept_breakdowns: list
    dropped_breakdowns: list
    budget: int

    @property
    def trim_ratio(self) -> float:
        if self.before_tokens <= 0:
            return 0.0
        return 1.0 - (self.after_tokens / self.before_tokens)


def format_graph_summary(
    selected: list[RetrievalHit],
    graph: DependencyGraph | None,
    token_limit: int,
) -> str:
    if graph is None or not selected or token_limit <= 0:
        return ""
    files = {h.citation.file_path for h in selected}
    symbols = set()
    for h in selected:
        if h.symbol_name:
            symbols.add(f"{h.citation.file_path}::{h.symbol_name}")
            symbols.add(f"{h.citation.file_path}::{h.symbol_name.split('.')[-1]}")

    lines: list[str] = []
    for e in graph.file_edges:
        if e.source in files or e.target in files:
            lines.append(f"[imports] {e.source} -> {e.target}")
    for e in graph.call_edges:
        src_file = e.caller.split("::", 1)[0]
        dst_file = e.callee.split("::", 1)[0]
        if (
            src_file in files
            or dst_file in files
            or e.caller in symbols
            or e.callee in symbols
        ):
            lines.append(f"[calls] {e.caller} -> {e.callee}")

    # Prefer shorter unique lines until budget
    out: list[str] = []
    used = 0
    for line in dict.fromkeys(lines):
        cost = estimate_tokens(line + "\n")
        if used + cost > token_limit:
            break
        out.append(line)
        used += cost
    return "\n".join(out)


def findings_to_history_text(findings: list[Any], round_idx: int) -> str:
    if not findings:
        return ""
    lines = [f"[round {round_idx}]"]
    for f in findings:
        cites = getattr(f, "citations", None) or []
        if not isinstance(cites, list):
            cites = []
        cite_s = ", ".join(cites) if cites else "no-cite"
        claim = getattr(f, "claim", str(f))
        confidence = getattr(f, "confidence", "?")
        tier = getattr(f, "evidence_tier", "?")
        lines.append(f"- ({confidence}/{tier}) {claim} [{cite_s}]")
    return "\n".join(lines)


class HistoryWindow:
    """Sliding window: keep only the latest N summaries; never priority-trim older ones."""

    def __init__(self, window: int = 1) -> None:
        self.window = max(1, window)
        self._items: list[HistoryItem] = []

    def push(self, text: str, round_idx: int | None = None) -> None:
        if not text.strip():
            return
        idx = round_idx if round_idx is not None else len(self._items)
        self._items.append(HistoryItem(text=text, round_idx=idx))
        if len(self._items) > self.window:
            self._items = self._items[-self.window :]

    def text(self, token_limit: int) -> str:
        if not self._items or token_limit <= 0:
            return ""
        # Newest first; truncate within the single (or few) window items by chars
        parts = [item.text for item in reversed(self._items)]
        joined = "\n\n".join(parts)
        # Hard cap by approximate tokens
        while estimate_tokens(joined) > token_limit and len(joined) > 32:
            joined = joined[: int(len(joined) * 0.85)]
        return joined

    def clear(self) -> None:
        self._items.clear()


def assemble_context(
    *,
    question: str,
    plan_hint: str,
    hits: list[RetrievalHit],
    expanded_hits: list[RetrievalHit],
    graph: DependencyGraph | None = None,
    history: HistoryWindow | None = None,
    config: ContextConfig | None = None,
    budget: int | None = None,
) -> AssembledContext:
    cfg = config or load_context_config()
    B = budget if budget is not None else cfg.token_budget

    b_code = int(B * cfg.buckets.code)
    b_graph = int(B * cfg.buckets.graph)
    b_hist = int(B * cfg.buckets.history)
    b_res = max(1, B - b_code - b_graph - b_hist)

    reserve_text = f"Question: {question}\nPlan: {plan_hint}"
    reserve_tokens = min(b_res, estimate_tokens(reserve_text))

    scored = score_candidates(hits, expanded_hits, graph=graph, config=cfg)
    before_tokens = (
        sum(estimate_tokens(s.hit.content) for s in scored)
        + reserve_tokens
    )

    # Sort ascending priority for drop order; also prefer drop lower tier / longer content
    def drop_key(s: ScoredHit) -> tuple:
        return (s.breakdown.priority, s.breakdown.tier, -estimate_tokens(s.hit.content))

    ordered = sorted(scored, key=drop_key)
    kept = list(ordered)

    def code_tokens(items: list[ScoredHit]) -> int:
        return sum(estimate_tokens(s.hit.content) for s in items)

    # Drop lowest priority until within code budget (keep ≥1 when possible)
    while len(kept) > 1 and code_tokens(kept) > b_code:
        kept.pop(0)

    kept_ids = {s.hit.chunk_id for s in kept}
    dropped = [s for s in ordered if s.hit.chunk_id not in kept_ids]

    # Borrow unused code budget to graph, then history
    used_code = code_tokens(kept)
    leftover = max(0, b_code - used_code)
    graph_limit = b_graph + leftover
    selected_hits = [s.hit for s in kept]
    graph_summary = format_graph_summary(selected_hits, graph, graph_limit)
    graph_tokens = estimate_tokens(graph_summary)
    leftover2 = max(0, graph_limit - graph_tokens)

    hist_limit = b_hist + leftover2
    history_text = history.text(hist_limit) if history is not None else ""
    history_tokens = estimate_tokens(history_text) if history_text else 0

    after_tokens = used_code + graph_tokens + history_tokens + reserve_tokens

    primary = [s.hit for s in kept if s.hit.source != "graph_expand"]
    expanded = [s.hit for s in kept if s.hit.source == "graph_expand"]

    return AssembledContext(
        code_hits=primary,
        expanded_hits=expanded,
        graph_summary=graph_summary,
        history_text=history_text,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        code_tokens=used_code,
        graph_tokens=graph_tokens,
        history_tokens=history_tokens,
        reserve_tokens=reserve_tokens,
        kept_breakdowns=[s.breakdown for s in kept],
        dropped_breakdowns=[s.breakdown for s in dropped],
        budget=B,
    )
