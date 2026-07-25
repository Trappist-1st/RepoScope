"""Format FlowTrace for humans / MCP / future SSE."""

from __future__ import annotations

from app.intelligence.flow_models import FlowStep, FlowTrace


def format_flow_markdown(trace: FlowTrace) -> str:
    topic = trace.query.topic or "flow"
    lines = [
        f"## Flow Trace: {topic}",
        "",
        f"**Question:** {trace.query.question}",
        f"**Confidence:** {trace.confidence}",
        f"**Score:** {trace.ranking_score:.2f}",
        "",
    ]
    if not trace.steps:
        lines.append("_No flow path found._")
        if trace.unresolved:
            lines.append("")
            lines.append("### Unresolved")
            for u in trace.unresolved:
                lines.append(f"- {u}")
        return "\n".join(lines)

    lines.append("### Steps")
    for step in trace.steps:
        lines.append(_format_step_line(step))
        if step.reason:
            lines.append(f"  - reason: {step.reason}")
        if step.inference_reason:
            lines.append(f"  - inference: {step.inference_reason}")
        lines.append("")

    if trace.alternatives:
        lines.append("### Alternatives")
        for i, alt in enumerate(trace.alternatives, 1):
            chain = " → ".join(alt.step_symbols) if alt.step_symbols else "(empty)"
            lines.append(f"{i}. `{chain}` (score={alt.score:.2f}, {alt.confidence})")
        lines.append("")

    if trace.unresolved:
        lines.append("### Unresolved")
        for u in trace.unresolved:
            lines.append(f"- {u}")
        lines.append("")

    if trace.warnings:
        lines.append("### Warnings")
        for w in trace.warnings:
            lines.append(f"- {w}")

    return "\n".join(lines).rstrip() + "\n"


def _format_step_line(step: FlowStep) -> str:
    loc = ""
    if step.file_path and step.start_line is not None:
        end = step.end_line if step.end_line is not None else step.start_line
        loc = f" — `{step.file_path}:{step.start_line}-{end}`"
    syn = ", inferred" if step.is_synthetic else ""
    return (
        f"{step.order}. **{step.symbol}** "
        f"(`{step.role.value}`{syn}, {step.confidence}){loc}"
    )


def steps_for_sse(trace: FlowTrace) -> list[dict]:
    """Payloads suitable for future `trace.step` SSE events."""
    return [s.model_dump(mode="json") for s in trace.steps]
