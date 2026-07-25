"""Format ArchitectureReport for MCP/API consumers."""

from __future__ import annotations

from app.intelligence.architecture.models import ArchitectureReport


def format_architecture_markdown(report: ArchitectureReport) -> str:
    lines: list[str] = [
        "# Architecture Report",
        "",
        f"**Repo:** `{report.meta.repo_id}`",
        f"**Commit:** `{report.meta.commit_hash or 'n/a'}`",
        f"**Generated:** {report.meta.generated_at}",
        f"**Primary pattern:** `{report.primary_pattern.value}`",
        f"**Method:** {report.meta.method}",
        "",
    ]

    p = report.profile
    lines += [
        "## Repository Profile",
        "",
        f"- files: {p.file_count}, symbols: {p.symbol_count}, modules: {p.module_count}",
        f"- languages: {_fmt_dict(p.languages)}",
        f"- build systems: {', '.join(p.build_systems) or 'n/a'}",
        f"- frameworks: {', '.join(f.name for f in p.frameworks) or 'n/a'}",
        f"- infra: {', '.join(f'{i.kind.value}:{i.name}' for i in p.infra) or 'n/a'}",
        f"- entrypoints: {', '.join(f'`{e}`' for e in p.entrypoints[:8]) or 'n/a'}",
        "",
    ]

    lines += ["## Modules", ""]
    if not report.modules.modules:
        lines.append("_No modules discovered._")
    for m in report.modules.modules:
        lines.append(
            f"- **{m.name}** (`{m.module_type.value}`, boundary={m.boundary_confidence}) "
            f"— {m.responsibility or 'n/a'} "
            f"— files={len(m.file_paths)} cohesion={m.cohesion if m.cohesion is not None else 'n/a'}"
        )
    lines.append("")

    lines += ["## Patterns", ""]
    for match in report.patterns:
        if match.pattern.value == "unknown" and match.score <= 0:
            continue
        lines.append(
            f"- **{match.pattern.value}** score={match.score:.2f} ({match.confidence})"
        )
        if match.signals:
            lines.append(f"  - signals: {', '.join(match.signals[:6])}")
        if match.counter_signals:
            lines.append(f"  - counter: {', '.join(match.counter_signals[:4])}")
    lines.append("")

    m = report.metrics
    lines += [
        "## Dependency Metrics",
        "",
        f"- cross-module edges: {m.cross_module_edges}",
        f"- dependency density: {m.dependency_density:.3f}",
        f"- cycles: {m.cycle_count}",
        f"- max fan-in/out: {m.max_fan_in:.0f}/{m.max_fan_out:.0f}",
        "",
    ]

    lines += ["## Findings", ""]
    if not report.findings:
        lines.append("_No findings._")
    for i, f in enumerate(report.findings, 1):
        lines.append(f"### {i}. {f.title}")
        lines.append(f"- category: `{f.category.value}`")
        lines.append(f"- confidence: **{f.confidence}**")
        if f.detail:
            lines.append(f"- detail: {f.detail}")
        if f.related_modules:
            lines.append(f"- modules: {', '.join(f'`{x}`' for x in f.related_modules[:8])}")
        if f.related_symbols:
            lines.append(f"- symbols: {', '.join(f'`{x}`' for x in f.related_symbols[:6])}")
        lines.append("- evidence:")
        for ev in f.evidence[:6]:
            loc = ev.file_path or ev.edge_id or ev.module_id or ev.note or "?"
            extra = ""
            if ev.start_line is not None:
                end = ev.end_line if ev.end_line is not None else ev.start_line
                extra = f":{ev.start_line}-{end}"
            lines.append(f"  - `{ev.kind.value}` {loc}{extra}")
        lines.append("")

    if report.warnings:
        lines += ["## Warnings", ""]
        for w in report.warnings:
            lines.append(f"- {w}")
        lines.append("")

    if report.unresolved:
        lines += ["## Unresolved", ""]
        for u in report.unresolved[:20]:
            lines.append(f"- {u}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _fmt_dict(d: dict[str, int]) -> str:
    if not d:
        return "n/a"
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))
