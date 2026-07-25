from __future__ import annotations

from typing import Any

from app.context_engine import HistoryWindow, findings_to_history_text, load_context_config
from app.workflow.analyzers import Analyzer, StubAnalyzer
from app.workflow.schemas import Intent
from app.workflow.state import WorkflowState
from app.workflow.timeouts import NodeTimeoutError, run_with_timeout


def _history_from_state(state: WorkflowState, window: int) -> HistoryWindow:
    """Rebuild a per-invocation HistoryWindow from workflow state (no shared closure)."""
    history = HistoryWindow(window=window)
    for i, text in enumerate(state.get("analysis_history") or [], start=1):
        history.push(text, round_idx=i)
    return history


def make_analyze_node(analyzer: Analyzer | None = None) -> Any:
    analyzer = analyzer or StubAnalyzer()

    def analyze_node(state: WorkflowState) -> WorkflowState:
        def _run() -> WorkflowState:
            cfg = load_context_config()
            history = _history_from_state(state, cfg.history_window)
            round_n = int(state.get("history_rounds") or 0) + 1

            intent: Intent = state.get("intent") or "summary"
            plan, findings, md, tokens = analyzer.analyze(
                question=state.get("question") or "",
                intent=intent,
                hits=list(state.get("hits") or []),
                expanded_hits=list(state.get("expanded_hits") or []),
                token_budget=int(state.get("token_budget") or 4000),
                graph=state.get("dependency_graph"),
                history=history,
                plan=list(state.get("plan") or []) or None,
            )

            # Persist this round for the next analyze (e.g. review retry) in *this* run only.
            summary = findings_to_history_text(findings, round_n)
            prior = list(state.get("analysis_history") or [])
            if summary.strip():
                prior.append(summary)
            window = max(1, cfg.history_window)
            if len(prior) > window:
                prior = prior[-window:]

            return {
                "plan": plan,
                "findings": findings,
                "analysis_markdown": md,
                "tokens_used": tokens,
                "history_rounds": round_n,
                "analysis_history": prior,
            }

        try:
            return run_with_timeout("analyze", _run)
        except NodeTimeoutError:
            return {
                "timeouts": {"analyze": True},
                "errors": ["analyze timed out"],
                "status": "partial",
                "findings": state.get("findings") or [],
                "plan": state.get("plan") or [],
                "analysis_markdown": state.get("analysis_markdown") or "",
            }

    return analyze_node
