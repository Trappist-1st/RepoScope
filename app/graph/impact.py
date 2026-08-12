"""Impact / blast-radius analysis over DependencyGraph.

``affected``  = who would feel a change to the seed (callers + subtypes)
``depends_on`` = what the seed relies on (callees + super-types)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from app.graph.query import callees_of, callers_of, children_of_type, parents_of
from app.models.schemas import DependencyGraph

ImpactDirection = Literal["affected", "depends_on", "both"]


@dataclass
class ImpactEdge:
    symbol_ref: str
    relation: str
    hops: int
    direction: Literal["affected", "depends_on"]


@dataclass
class ImpactReport:
    seeds: list[str]
    depth: int
    direction: ImpactDirection
    affected: list[ImpactEdge] = field(default_factory=list)
    depends_on: list[ImpactEdge] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    depends_on_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def all_hits(self) -> list[ImpactEdge]:
        return list(self.affected) + list(self.depends_on)


def _file_of(ref: str) -> str:
    return ref.split("::", 1)[0] if "::" in ref else ref


def _inherit_relation(graph: DependencyGraph, child: str, parent: str) -> str:
    for e in graph.inherit_edges:
        if e.child == child and e.parent == parent:
            return e.relation
    return "extends"


def analyze_impact(
    graph: DependencyGraph,
    seed_refs: list[str],
    *,
    depth: int = 2,
    direction: ImpactDirection = "both",
    limit: int = 50,
) -> ImpactReport:
    """
    BFS impact analysis.

    - affected: callers + subtype children (change blast radius)
    - depends_on: callees + parent types
    """
    depth = max(1, min(int(depth), 8))
    limit = max(1, min(int(limit), 200))
    seeds = [s for s in seed_refs if s]
    report = ImpactReport(seeds=seeds, depth=depth, direction=direction)
    if not seeds:
        report.notes.append("No resolved seed symbols.")
        return report

    want_aff = direction in {"affected", "both"}
    want_dep = direction in {"depends_on", "both"}

    affected: list[ImpactEdge] = []
    depends: list[ImpactEdge] = []
    seen_aff: set[str] = set(seeds)
    seen_dep: set[str] = set(seeds)

    if want_aff:
        frontier = list(seeds)
        for hop in range(1, depth + 1):
            nxt: list[str] = []
            for ref in frontier:
                for caller in callers_of(graph, ref):
                    if caller in seen_aff:
                        continue
                    seen_aff.add(caller)
                    affected.append(
                        ImpactEdge(caller, "caller", hop, "affected")
                    )
                    nxt.append(caller)
                for child in children_of_type(graph, ref):
                    if child in seen_aff:
                        continue
                    seen_aff.add(child)
                    affected.append(
                        ImpactEdge(child, "subtype", hop, "affected")
                    )
                    nxt.append(child)
                if len(affected) >= limit:
                    break
            frontier = nxt
            if not frontier or len(affected) >= limit:
                break

    if want_dep:
        frontier = list(seeds)
        for hop in range(1, depth + 1):
            nxt: list[str] = []
            for ref in frontier:
                for callee in callees_of(graph, ref):
                    if callee in seen_dep:
                        continue
                    seen_dep.add(callee)
                    depends.append(
                        ImpactEdge(callee, "callee", hop, "depends_on")
                    )
                    nxt.append(callee)
                for parent in parents_of(graph, ref):
                    if parent in seen_dep:
                        continue
                    seen_dep.add(parent)
                    rel = _inherit_relation(graph, ref, parent)
                    depends.append(
                        ImpactEdge(parent, rel, hop, "depends_on")
                    )
                    # don't expand further through parents by default unless depth allows
                    nxt.append(parent)
                if len(depends) >= limit:
                    break
            frontier = nxt
            if not frontier or len(depends) >= limit:
                break

    report.affected = affected[:limit]
    report.depends_on = depends[:limit]
    report.affected_files = sorted({_file_of(e.symbol_ref) for e in report.affected})
    report.depends_on_files = sorted({_file_of(e.symbol_ref) for e in report.depends_on})

    if want_aff and not report.affected:
        report.notes.append("No upstream callers/subtypes within depth.")
    if want_dep and not report.depends_on:
        report.notes.append("No downstream callees/supertypes within depth.")
    return report


def format_impact_markdown(report: ImpactReport) -> str:
    lines = [
        "## Impact Analysis",
        "",
        f"**Seeds:** {', '.join(f'`{s}`' for s in report.seeds) or '_none_'}",
        f"**Depth:** {report.depth} · **Direction:** {report.direction}",
        "",
    ]
    if report.direction in {"affected", "both"}:
        lines.append("### Affected (change blast radius)")
        if not report.affected:
            lines.append("_Nothing upstream within depth._")
        else:
            by_hop: dict[int, list[ImpactEdge]] = defaultdict(list)
            for e in report.affected:
                by_hop[e.hops].append(e)
            for hop in sorted(by_hop):
                lines.append(f"**hop {hop}**")
                for e in by_hop[hop]:
                    lines.append(f"- `{e.symbol_ref}` — {e.relation}")
        if report.affected_files:
            lines.append("")
            lines.append("**Files:** " + ", ".join(f"`{f}`" for f in report.affected_files[:40]))
        lines.append("")

    if report.direction in {"depends_on", "both"}:
        lines.append("### Depends on (downstream)")
        if not report.depends_on:
            lines.append("_Nothing downstream within depth._")
        else:
            by_hop = defaultdict(list)
            for e in report.depends_on:
                by_hop[e.hops].append(e)
            for hop in sorted(by_hop):
                lines.append(f"**hop {hop}**")
                for e in by_hop[hop]:
                    lines.append(f"- `{e.symbol_ref}` — {e.relation}")
        if report.depends_on_files:
            lines.append("")
            lines.append(
                "**Files:** " + ", ".join(f"`{f}`" for f in report.depends_on_files[:40])
            )
        lines.append("")

    if report.notes:
        lines.append("### Notes")
        for n in report.notes:
            lines.append(f"- {n}")
    return "\n".join(lines).rstrip() + "\n"
